"""Fixed-case structure-use diagnostic, not a trained-model benchmark."""
import argparse
from copy import deepcopy
import html
import json
from pathlib import Path
import time
import urllib.request

from . import pilot as p
from .bootstrap_local import digest

FIELDS = ('actor', 'event', 'target', 'condition', 'status', 'time')
CONDITIONS = ('direct', 'generated', 'reference', 'corrupted')
RECORD = {'type': 'object', 'properties': {k: {'type': 'string'} for k in FIELDS},
          'required': list(FIELDS), 'additionalProperties': False}
EXTRACT_SCHEMA = {'type': 'object', 'properties': {'records': {
    'type': 'array', 'items': RECORD, 'minItems': 1, 'maxItems': 3}},
    'required': ['records'], 'additionalProperties': False}
JUDGE_SCHEMA = {'type': 'object', 'properties': {
    'label': {'type': 'string', 'enum': ['S', 'C', 'U']}, 'reason': {'type': 'string'}},
    'required': ['label', 'reason'], 'additionalProperties': False}


def validate_config(config):
    cases = config['cases']
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Case IDs must be nonempty and unique')
    for case in cases:
        if case['source'] not in config['sources'] or case['reference_label'] not in {'S', 'C', 'U'}:
            raise ValueError('Invalid source or reference label')
        if set(case['reference_structure']) != set(FIELDS):
            raise ValueError('Invalid reference structure')
        change = case['corruption']
        if change['field'] not in FIELDS or case['reference_structure'][change['field']] == change['value']:
            raise ValueError('Corruption must change exactly one declared field')


def section(text, spec):
    # Ignore whitespace only while locating anchors; preserve it in model input.
    compact = ''.join(text.split())
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    start, stop = ''.join(spec['start'].split()), ''.join(spec['end_before'].split())
    if compact.count(start) != 1:
        raise ValueError('Start anchor must occur exactly once')
    left = compact.index(start)
    right = compact.find(stop, left + len(start))
    if right < 0:
        raise ValueError('End anchor missing after start')
    raw_left, raw_right = positions[left], positions[right]
    return text[raw_left:raw_right], raw_left, raw_right


def prepare(args):
    from pypdf import PdfReader
    config = p.load(args.config)
    validate_config(config)
    out = Path(args.run_dir)
    out.mkdir(parents=True, exist_ok=False)
    sources, pages = {}, {}
    for key, spec in config['sources'].items():
        path = p.ROOT / 'data/raw' / (key + '_structure_probe.pdf')
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(spec['url'], headers={'User-Agent': 'risk-text-pilot/0.2'})
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
            if not content.startswith(b'%PDF') or p.sha256(content) != spec['sha256']:
                raise ValueError('Downloaded source is not the pinned PDF: ' + key)
            with path.open('xb') as stream:
                stream.write(content)
        if p.sha256(path.read_bytes()) != spec['sha256']:
            raise ValueError('Source changed: ' + key)
        sources[key] = {**spec, 'physical_pages': len(PdfReader(path).pages)}
        reader = PdfReader(path)
        for number in {s['page'] for c in config['cases'] if c['source'] == key for s in c['sections']}:
            pages[key, number] = reader.pages[number - 1].extract_text() or ''
    inputs = []
    for case in config['cases']:
        fragments, locations = [], []
        for spec in case['sections']:
            value, left, right = section(pages[case['source'], spec['page']], spec)
            fragments.append(value)
            locations.append({'physical_page': spec['page'], 'start_char': left, 'end_char': right,
                              'offset_basis': 'pypdf-6.10.0_raw_text',
                              'section_sha256': p.sha256(value.encode())})
        context = '\n\n'.join(fragments)
        inputs.append({**case, 'text': context, 'locations': locations,
                       'text_sha256': p.sha256(context.encode())})
    p.save_rows(out / 'inputs.jsonl', inputs)
    prompts = {key: (p.ROOT / path).read_text(encoding='utf-8') for key, path in {
        'extract': 'prompts/structure_extract_v1.txt', 'judge': 'prompts/structure_judge_v1.txt'}.items()}
    p.save(out / 'protocol.json', {'prepared_at': p.utc(), 'config': config, 'sources': sources,
        'preprocessing': 'preserve_raw_pypdf_text_whitespace_and_table_column_spacing',
        'prompts': prompts, 'schemas': {'extract': EXTRACT_SCHEMA, 'judge': JUDGE_SCHEMA},
        'inputs_sha256': p.sha256((out / 'inputs.jsonl').read_bytes()),
        'conditions': list(CONDITIONS), 'temperature': 0, 'seed': 42,
        'extract_max_tokens': 768, 'judge_max_tokens': 192})
    print(f'Frozen {len(inputs)} cases from {len(sources)} sources: {out}', flush=True)


def call(base, model, system, user, schema, max_tokens):
    start = time.monotonic()
    result = {'status': 'failed'}
    try:
        response = p.request_json(base + '/chat/completions', {
            'model': model, 'temperature': 0, 'seed': 42, 'max_tokens': max_tokens,
            'stream': False, 'cache_prompt': False,
            'chat_template_kwargs': {'enable_thinking': False},
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'output', 'schema': schema}},
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]}, timeout=300)
        result['raw_response'] = response
        choice = response['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Incomplete generation: ' + str(choice.get('finish_reason')))
        value = json.loads(choice['message']['content'])
        if schema == JUDGE_SCHEMA:
            if set(value) != {'label', 'reason'} or value['label'] not in {'S', 'C', 'U'} or not isinstance(value['reason'], str):
                raise ValueError('Invalid judgment')
        else:
            if set(value) != {'records'} or not isinstance(value['records'], list) or not 1 <= len(value['records']) <= 3:
                raise ValueError('Invalid record list')
            for record in value['records']:
                if set(record) != set(FIELDS) or any(not isinstance(v, str) or not v.strip() for v in record.values()):
                    raise ValueError('Invalid event record')
        result.update(status='ok', output=value)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        result['error'] = str(error)
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    return result


def auxiliary(case, condition, extracted):
    if condition == 'direct':
        return None
    if condition == 'generated':
        return extracted['output']
    record = deepcopy(case['reference_structure'])
    if condition == 'corrupted':
        record[case['corruption']['field']] = case['corruption']['value']
    return {'records': [record]}


def verify_model(base, assets):
    props = p.request_json(base.removesuffix('/v1') + '/props', timeout=5)
    actual = digest(Path(props['model_path']))
    if actual != assets['model']['sha256']:
        raise ValueError('Loaded server model differs from the pinned weights')
    return actual


def run(args):
    folder = Path(args.run_dir)
    protocol = p.load(folder / 'protocol.json')
    if p.sha256((folder / 'inputs.jsonl').read_bytes()) != protocol['inputs_sha256']:
        raise ValueError('Prepared inputs changed')
    # Prompts, schemas, cases, labels and sampling settings are frozen together.
    if protocol['schemas'] != {'extract': EXTRACT_SCHEMA, 'judge': JUDGE_SCHEMA}:
        raise ValueError('Runner schema differs from frozen protocol')
    base = p.local_url(args.base_url)
    metadata = p.request_json(base + '/models', timeout=5)['data'][0]
    assets = p.load(p.ROOT / 'configs/assets.json')
    verified_hash = verify_model(base, assets)
    p.save(folder / 'manifest.json', {'started_at': p.utc(), 'base_url': base,
        'server_model_metadata': metadata, 'assets': assets, 'verified_model_sha256': verified_hash,
        'protocol_sha256': p.sha256((folder / 'protocol.json').read_bytes()),
        'hardware': args.hardware, 'training_performed': False,
        'reference_status': protocol['config']['reference_status']})
    with (folder / 'calls.jsonl').open('x', encoding='utf-8') as stream:
        def record(case_id, phase, result, condition=None):
            row = {'case_id': case_id, 'phase': phase, 'condition': condition, **result}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            print(case_id, phase, condition or '', result['status'], result.get('output', {}).get('label', ''), flush=True)
        for case in p.rows(folder / 'inputs.jsonl'):
            # This call sees the source only: no claim, reference or expected label.
            extracted = call(base, metadata['id'], protocol['prompts']['extract'],
                             {'text': case['text']}, EXTRACT_SCHEMA, protocol['extract_max_tokens'])
            record(case['id'], 'extract', extracted)
            for condition in protocol['conditions']:
                if condition == 'generated' and extracted['status'] != 'ok':
                    record(case['id'], 'judge', {'status': 'skipped', 'error': 'Extraction failed'}, condition)
                    continue
                result = call(base, metadata['id'], protocol['prompts']['judge'], {
                    'text': case['text'], 'claim': case['claim'],
                    'auxiliary_structure': auxiliary(case, condition, extracted)},
                    JUDGE_SCHEMA, protocol['judge_max_tokens'])
                record(case['id'], 'judge', result, condition)


def summarize(inputs, calls):
    case_ids = {case['id'] for case in inputs}
    seen = set()
    for row in calls:
        key = (row['case_id'], row['phase'], row['condition'])
        if row['case_id'] not in case_ids or key in seen:
            raise ValueError('Duplicate or unknown call')
        if row['phase'] not in {'extract', 'judge'} or (row['phase'] == 'judge' and row['condition'] not in CONDITIONS):
            raise ValueError('Unknown phase or condition')
        seen.add(key)
    rows = []
    totals = {key: {'completed': 0, 'draft_reference_agreement': 0, 'changed_from_direct': 0,
                    'improved_vs_draft': 0, 'worsened_vs_draft': 0, 'failed_or_skipped': 0} for key in CONDITIONS}
    for case in inputs:
        outputs = {r['condition']: r for r in calls if r['case_id'] == case['id'] and r['phase'] == 'judge'}
        labels = {key: outputs.get(key, {}).get('output', {}).get('label') for key in CONDITIONS}
        for key, label in labels.items():
            count = totals[key]
            if label is None:
                count['failed_or_skipped'] += 1
                continue
            count['completed'] += 1
            correct = label == case['reference_label']
            count['draft_reference_agreement'] += correct
            if labels['direct'] is not None:
                direct_correct = labels['direct'] == case['reference_label']
                count['changed_from_direct'] += label != labels['direct']
                count['improved_vs_draft'] += correct and not direct_correct
                count['worsened_vs_draft'] += not correct and direct_correct
        rows.append({'case_id': case['id'], 'source': case['source'], 'role': case['role'],
                     'dependency': case['dependency'], 'claim': case['claim'],
                     'reference_label': case['reference_label'], 'labels': labels})
    matched = [row for row in rows if row['labels']['reference'] is not None and row['labels']['corrupted'] is not None]
    return {'case_count': len(inputs), 'source_count': len({c['source'] for c in inputs}),
            'expected_calls': len(inputs) * 5, 'recorded_calls': len(calls),
            'extraction_successes': sum(r['phase'] == 'extract' and r['status'] == 'ok' for r in calls),
            'reference_status': 'assistant_source_reviewed_not_independent_human_gold',
            'semantic_accuracy': None, 'calibration_error': None,
            'reference_corrupted_pairs': len(matched),
            'reference_corrupted_label_changes': sum(row['labels']['reference'] != row['labels']['corrupted'] for row in matched),
            'conditions': totals, 'cases': rows}


def report(args):
    folder = Path(args.run_dir)
    inputs, calls = p.rows(folder / 'inputs.jsonl'), p.rows(folder / 'calls.jsonl')
    summary = summarize(inputs, calls)
    manifest = p.load(folder / 'manifest.json')
    summary['run'] = {'model': manifest['server_model_metadata']['id'],
                      'model_sha256': manifest['assets']['model']['sha256'],
                      'hardware': manifest['hardware'], 'training_performed': False,
                      'protocol_sha256': manifest['protocol_sha256'], 'seed': 42, 'temperature': 0}
    p.save(folder / 'summary.json', summary)
    if args.publish:
        p.save(Path(args.publish) / 'results.json', summary)
    escape = lambda value: html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>结构信息利用对照</title>',
             '<style>body{max-width:1080px;margin:30px auto;font:16px/1.7 system-ui;color:#222}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:16px}article{border-top:1px solid #ccc;margin-top:32px}h1,h2{color:#111}</style>',
             '<h1>结构信息利用对照</h1><p>参考标签由AI对照原文拟定，尚未经两人独立复核。S原文支持；C原文明确相反；U依据不足。</p>',
             '<pre>' + escape({k: v for k, v in summary.items() if k != 'cases'}) + '</pre>']
    for case in inputs:
        parts += ['<article><h2>' + html.escape(case['id']) + '</h2>',
                  '<p>' + html.escape(case['claim']) + '；参考 ' + case['reference_label'] + '</p>',
                  '<h3>原文证据窗口</h3><pre>' + html.escape(case['text']) + '</pre>',
                  '<p>定位</p><pre>' + escape(case['locations']) + '</pre>',
                  '<h3>结构与判断</h3>']
        for row in (r for r in calls if r['case_id'] == case['id']):
            visible = {k: v for k, v in row.items() if k != 'raw_response'}
            parts += ['<pre>' + escape(visible) + '</pre>']
        parts += ['</article>']
    with (folder / 'review.html').open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(parts) + '</html>')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, func in [('prepare', prepare), ('run', run), ('report', report)]:
        item = sub.add_parser(name)
        item.add_argument('--run-dir', type=Path, required=True)
        item.set_defaults(func=func)
        if name == 'prepare':
            item.add_argument('--config', type=Path, default=p.ROOT / 'configs/structure-probe.json')
        if name == 'run':
            item.add_argument('--base-url', default='http://127.0.0.1:18080/v1')
            item.add_argument('--hardware', required=True, help='Actual backend and GPU offload setting')
        if name == 'report':
            item.add_argument('--publish', type=Path, help='Export only compact, source-free results')
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
