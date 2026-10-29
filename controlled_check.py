"""One immutable paragraph/wording control, using only the existing CATL corpus."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re

import pilot


def normalize(text):
    return ''.join(c for c in text if not c.isspace())


def paragraph_blocks(plain, layout):
    """Layout supplies boundaries; every mapped character comes from old plain text."""
    positions = [i for i, c in enumerate(plain) if not c.isspace()]
    normalized = normalize(plain)
    cursor = 0
    result = []
    for block in re.split(r'\n\s*\n', layout):
        content = block.strip()
        if not content:
            continue
        needle = normalize(content)
        if '年度报告全文' in needle or needle.isdigit():
            continue
        at = normalized.find(needle, cursor)
        if at < 0:
            result.append({'kind': 'unmapped_layout', 'layout_text': content,
                           'text': None, 'requires_review': True})
            continue
        start, end = positions[at], positions[at + len(needle) - 1] + 1
        cursor = at + len(needle)
        lines = content.splitlines()
        if len(needle) < 90 and re.match(r'^(?:[（(]?\d+[）)、.．]|[一二三四五六七八九十]+、)', needle):
            kind = 'heading'
        elif any(re.search(r'\S\s{3,}\S', line.strip()) for line in lines) or '□' in content or '\uf052' in content:
            kind = 'table_or_layout'
        else:
            kind = 'narrative'
        complete = needle.endswith(('。', '！', '？', '；', '：'))
        result.append({'kind': kind, 'text': plain[start:end], 'start_char': start,
                       'end_char': end, 'ends_at_punctuation': complete,
                       'requires_review': kind != 'narrative' or not complete})
    return result


def snapshots(root):
    paths = [pilot.ROOT / 'prompt.txt']
    for folder in ('20261029_catl_smoke', '20261029_catl_short_smoke'):
        paths.extend(sorted((pilot.ROOT / 'runs' / folder).rglob('*')))
    return {str(p.relative_to(pilot.ROOT)): pilot.sha256(p.read_bytes()) for p in paths if p.is_file()}


def prepare(destination):
    from pypdf import PdfReader
    pages = pilot.rows(pilot.ROOT / 'data/pages.jsonl')
    readers = {}
    blocks = []
    for page in pages:
        path = page['path']
        if path not in readers:
            if pilot.sha256(Path(path).read_bytes()) != page['sha256']:
                raise ValueError('Original PDF hash changed')
            readers[path] = PdfReader(path)
        layout = readers[path].pages[page['page'] - 1].extract_text(extraction_mode='layout') or ''
        heading = None
        page_blocks = paragraph_blocks(page['text'], layout)
        for index, block in enumerate(page_blocks):
            if block['kind'] == 'heading':
                heading = block['text']
            block.update({'source_id': page['source_id'], 'sha256': page['sha256'],
                          'page': page['page'], 'block_id': f"p{page['page']}-b{index}",
                          'local_heading': heading,
                          'boundary_note': 'PDF blank-line candidate; cross-page and table blocks require source review'})
            blocks.append(block)
    old = pilot.rows(pilot.ROOT / 'runs/20261029_catl_short_smoke/inputs.jsonl')[0]
    page = next(p for p in pages if p['source_id'] == old['source_id'] and p['page'] == old['page'])
    relevant = [b for b in blocks if b['page'] == old['page'] and b['kind'] == 'narrative'
                and b.get('start_char', 10**9) < page['text'].index('十二、报告期内')]
    if len(relevant) != 4:
        raise ValueError(f'Expected four source-checked paragraphs, found {len(relevant)}')
    full = page['text'][:relevant[-1]['end_char']]
    if not full.startswith(old['text']) or not full.endswith('。'):
        raise ValueError('Full context must extend exact old input and end at source punctuation')
    destination.mkdir(parents=True, exist_ok=False)
    pilot.save_rows(destination / 'paragraphs.jsonl', blocks)
    cells = []
    for name, complete, clarified in [('A', False, False), ('B', True, False), ('C', False, True), ('D', True, True)]:
        item = dict(old)
        item.update({'sample_id': name + '-p40', 'text': full if complete else old['text'],
                     'end_char': len(full) if complete else old['end_char'],
                     'selection_group': 'matched_development_control', 'label_status': 'unreviewed',
                     'sampling_note': 'One shared development case; not independent test observations'})
        pilot.save_rows(destination / f'input_{name}.jsonl', [item])
        prompt = pilot.ROOT / ('prompts/claim_protocol_v1.txt' if clarified else 'prompt.txt')
        cells.append({'cell': name, 'complete_paragraphs': complete, 'clarified_protocol': clarified,
                      'input_file': f'input_{name}.jsonl', 'input_sha256': pilot.sha256(item['text'].encode()),
                      'prompt_file': str(prompt), 'prompt_sha256': pilot.sha256(prompt.read_bytes()),
                      'characters': len(item['text'])})
    pilot.save(destination / 'design.json', {'created_at': pilot.utc(), 'cells': cells,
        'parameters': {'seed': 42, 'temperature': 0, 'max_tokens': 1800, 'timeout': 480},
        'source_pages': [p['page'] for p in pages], 'segmentation': dict(Counter(b['kind'] for b in blocks)),
        'old_file_hashes': snapshots(pilot.ROOT),
        'annotation': 'Independent annotator templates remain blank. Assistant review is not gold.',
        'decision': 'Separate input/rule failures from remaining understanding failures; no model change or prompt tuning.'})
    print(json.dumps({'prepared': str(destination), 'cells': cells,
                      'segmentation': dict(Counter(b['kind'] for b in blocks))}, ensure_ascii=False), flush=True)


def run(destination):
    design = pilot.load(destination / 'design.json')
    for cell in design['cells']:
        prompt = Path(cell['prompt_file'])
        item = pilot.rows(destination / cell['input_file'])[0]
        if pilot.sha256(prompt.read_bytes()) != cell['prompt_sha256'] or pilot.sha256(item['text'].encode()) != cell['input_sha256']:
            raise ValueError('Frozen prompt or input changed')
        print('Starting cell ' + cell['cell'], flush=True)
        pilot.infer(argparse.Namespace(samples=destination / cell['input_file'],
            run_dir=destination / cell['cell'], prompt_file=prompt, limit=1,
            seed=42, max_tokens=1800, timeout=480))
    verify_old(design)
    report(destination)


def verify_old(design):
    for path, digest in design['old_file_hashes'].items():
        if pilot.sha256((pilot.ROOT / path).read_bytes()) != digest:
            raise ValueError('Preserved file changed: ' + path)


def metrics(prediction):
    records = prediction.get('prediction', {}).get('records', [])
    unique = {json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records}
    raw_records = None
    if prediction['status'] != 'ok':
        try:
            content = prediction['raw_response']['choices'][0]['message']['content']
            candidate = json.loads(content)
            if isinstance(candidate.get('records'), list):
                raw_records = candidate['records']
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return {'run_status': prediction['status'], 'records': len(records),
            'exact_duplicate_excess': len(records) - len(unique),
            'unvalidated_raw_candidate_count': len(raw_records) if raw_records is not None else None,
            'needs_context': prediction.get('prediction', {}).get('needs_context'),
            'quote_not_found': sum(not c['found'] for c in prediction.get('evidence_checks', [])),
            'finish_reason': prediction.get('raw_response', {}).get('choices', [{}])[0].get('finish_reason'),
            'elapsed_seconds': prediction.get('elapsed_seconds')}


def report(destination, prefix='comparison'):
    design = pilot.load(destination / 'design.json')
    verify_old(design)
    summary = {}
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>同模型四组对照</title>',
        '<style>body{max-width:1100px;margin:30px auto;font:17px/1.75 system-ui;color:#222;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:14px}article{border-top:1px solid #bbb;margin:30px 0}td,th{border:1px solid #ddd;padding:8px}table{border-collapse:collapse;width:100%}h1,h2,h3{color:#111}</style>',
        '<h1>宁德时代年报：同模型四组开发对照</h1><p>同一个片段、同一个模型；两项因素为输入完整性和规则说明。四组是一个案例的四种处理，不是四个独立样本。原始运行保留。下列机械统计不等于语义正确率。</p>']
    for cell in design['cells']:
        name = cell['cell']
        manifest = pilot.load(destination / name / 'manifest.json')
        for key in ('seed', 'temperature', 'max_tokens', 'timeout'):
            if manifest[key] != design['parameters'][key]:
                raise ValueError('Control parameter changed: ' + key)
        if manifest['prompt_sha256'] != cell['prompt_sha256']:
            raise ValueError('Run used a different prompt')
        pred = pilot.rows(destination / name / 'predictions.jsonl')[0]
        summary[name] = {**cell, **metrics(pred)}
    manifests = [pilot.load(destination / cell['cell'] / 'manifest.json') for cell in design['cells']]
    if any(m['model'] != manifests[0]['model'] or m['assets'] != manifests[0]['assets'] for m in manifests):
        raise ValueError('Model identity/assets differ across controls')
    parts.append('<table><tr><th>组别</th><th>输入</th><th>规则</th><th>记录/完全重复</th><th>缺上下文</th><th>完成</th></tr>')
    for name, value in summary.items():
        count = f"{value['records']} / {value['exact_duplicate_excess']}" if value['run_status'] == 'ok' else f"失败；原始候选{value['unvalidated_raw_candidate_count']}条，未接纳"
        parts.append(f"<tr><td>{name}</td><td>{'完整段落' if value['complete_paragraphs'] else '旧500字符'}</td><td>{'明确' if value['clarified_protocol'] else '旧规则'}</td><td>{count}</td><td>{value['needs_context']}</td><td>{value['run_status']}</td></tr>")
    parts.append('</table>')
    for cell in design['cells']:
        name = cell['cell']
        item = pilot.rows(destination / cell['input_file'])[0]
        pred = pilot.rows(destination / name / 'predictions.jsonl')[0]
        parts.extend([f'<article><h2>{name}组</h2><h3>实际输入</h3><pre>{html.escape(item["text"])}</pre>'])
        if pred['status'] != 'ok':
            parts.append('<p>本组输出未通过格式校验：' + html.escape(pred.get('error', 'unknown')) + '。原始候选并非空结果，也未自动修复为成功；见下方原始回答。</p>')
        for i, record in enumerate(pred.get('prediction', {}).get('records', []), 1):
            parts.append(f'<h3>记录{i}：{html.escape(record["event"])}</h3><p>状态：{html.escape(record["status"])}</p><p>{html.escape(record["relationship"])}</p><blockquote>{html.escape(" / ".join(record["evidence"]))}</blockquote>')
        parts.append('<details><summary>完整JSON与运行细节</summary><pre>' + html.escape(json.dumps(pred, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    parts.append('</html>')
    assessment = destination / 'assistant_assessment.md'
    if assessment.exists():
        parts.insert(3, '<h2>逐项试读判断（助手核对，非独立人工金标准）</h2><pre>' + html.escape(assessment.read_text(encoding='utf-8')) + '</pre>')
    pilot.save(destination / (prefix + '.json'), summary)
    with (destination / (prefix + '.html')).open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(parts))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'report'])
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--report-name', default='comparison', choices=['comparison', 'review'])
    args = parser.parse_args()
    if args.action == 'report':
        report(args.out, prefix=args.report_name)
    else:
        {'prepare': prepare, 'run': run}[args.action](args.out)
