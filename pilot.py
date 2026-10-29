"""Small, local-only research pilot. No automatic semantic ground truth."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
STATUSES = {'确定', '可能或条件成立', '明确否定', '无法判断'}
KEYWORDS = ('风险', '不确定', '供应', '中断', '短缺', '制裁', '限制', '原材料', '价格波动', '停产', '减产', '不利影响', '需求下降')
FIELDS = {'entity', 'event', 'relationship', 'status', 'event_time', 'evidence', 'unknowns'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8-sig').splitlines() if s.strip()]


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write('\n')


def save_rows(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        for value in values:
            out.write(json.dumps(value, ensure_ascii=False) + '\n')


def settings():
    path = ROOT / 'settings.local.json'
    return load(path) if path.exists() else {}


def local_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Only loopback HTTP inference is allowed; document upload is disabled')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Credentials/query/fragment are not allowed in the inference URL')
    return url.rstrip('/')


class LoopbackRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        local_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_json(url, data=None, timeout=300):
    url = local_url(url)
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
    headers = {'Content-Type': 'application/json'}
    key_file = ROOT / '.runtime' / 'inference.key'
    if key_file.exists():
        headers['Authorization'] = 'Bearer ' + key_file.read_text(encoding='ascii').strip()
    req = urllib.request.Request(url, data=payload, headers=headers)
    # Local documents must not be routed through an HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), LoopbackRedirect()).open(req, timeout=timeout) as response:
        return json.load(response)


def parse_prediction(text):
    text = re.sub(r'^\s*<think>.*?</think>\s*', '', text, flags=re.S)
    if text.strip().startswith('```'):
        text = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', text)
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'records', 'needs_context'}:
        raise ValueError('Expected records and needs_context only')
    if not isinstance(value['records'], list) or type(value['needs_context']) is not bool:
        raise ValueError('Invalid records/needs_context types')
    for record in value['records']:
        if not isinstance(record, dict) or set(record) != FIELDS:
            raise ValueError('Record fields do not match the pilot schema')
        for field in ('entity', 'event', 'relationship', 'status'):
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError(f'Empty/invalid {field}')
        if record['status'] not in STATUSES:
            raise ValueError('Invalid statement status')
        if record['event_time'] is not None and not isinstance(record['event_time'], str):
            raise ValueError('event_time must be text or null')
        for field in ('evidence', 'unknowns'):
            if not isinstance(record[field], list) or any(not isinstance(s, str) or not s.strip() for s in record[field]):
                raise ValueError(f'Invalid {field}')
        if not record['evidence']:
            raise ValueError('Each record needs a verbatim evidence quote')
    return value


def evidence_checks(prediction, text):
    compact = re.sub(r'\s+', '', text)
    return [
        {'record_index': i, 'quote': quote,
         'found': bool(quote.strip()) and re.sub(r'\s+', '', quote) in compact}
        for i, record in enumerate(prediction['records']) for quote in record['evidence']
    ]


def doctor(_):
    base = settings().get('base_url', 'http://127.0.0.1:18080/v1')
    report = {'python': sys.executable, 'version': sys.version.split()[0],
              'pypdf_available': importlib.util.find_spec('pypdf') is not None,
              'project': str(ROOT), 'main_corpus_confirmed': False,
              'model_file_exists': Path(settings().get('model_path', '.missing-model')).is_file()}
    registry = ROOT / 'data' / 'sources.json'
    if registry.exists():
        report['registered_sources'] = len(load(registry)['sources'])
    try:
        report['local_server'] = request_json(base + '/models', timeout=3)
    except (OSError, ValueError) as error:
        report['local_server'] = {'status': 'not_ready', 'message': str(error)}
    print(json.dumps(report, ensure_ascii=False, indent=2))


def inventory(args):
    found = []
    for root in args.roots:
        root = Path(root).resolve(strict=True)
        paths = [root] if root.is_file() else sorted(root.rglob('*'))
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in {'.pdf', '.txt', '.html', '.docx', '.zip', '.jsonl'}:
                continue
            if any(part in {'.git', '.venv', '.runtime', 'node_modules', '__pycache__'} for part in path.parts):
                continue
            found.append({'path': str(path), 'size_bytes': path.stat().st_size,
                          'role': 'unreviewed_file_not_main_corpus'})
    save_rows(args.out, found)
    print(f'Inventoried {len(found)} files. No automatic corpus inclusion: {args.out}')


def register(args):
    path = Path(args.path).resolve(strict=True)
    if path.suffix.lower() not in {'.pdf', '.txt'}:
        raise ValueError('Pilot accepts PDF/TXT original documents only')
    registry = Path(args.registry)
    document = load(registry) if registry.exists() else {'sources': []}
    digest = sha256(path.read_bytes())
    if any(s['sha256'] == digest for s in document['sources']):
        raise ValueError('Duplicate source hash; not registered again')
    document['sources'].append({'source_id': digest[:16], 'sha256': digest, 'path': str(path),
                                'company': args.company, 'kind': args.kind,
                                'url': args.url, 'publication_date': args.date,
                                'data_role': 'pilot_candidate_not_final_corpus', 'registered_at': utc()})
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_suffix('.json.partial')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(document, output, ensure_ascii=False, indent=2)
        output.write('\n')
    temporary.replace(registry)
    print(f'Registered a read-only source: {args.company} / {digest[:16]}')


def ingest(args):
    sources = load(args.registry)['sources']
    if not sources:
        raise ValueError('No registered enterprise originals. Do not substitute papers or DocFEE.')
    pages = []
    for source in sources:
        path = Path(source['path'])
        if sha256(path.read_bytes()) != source['sha256']:
            raise ValueError(f'Original changed; re-audit source: {path}')
        if path.suffix.lower() == '.pdf':
            from pypdf import PdfReader
            texts = [page.extract_text() or '' for page in PdfReader(path).pages]
        else:
            texts = [path.read_text(encoding='utf-8-sig')]
        for number, text in enumerate(texts, 1):
            if number < args.page_start or (args.page_end is not None and number > args.page_end):
                continue
            pages.append({**source, 'page': number, 'text': text,
                          'extraction_warning': 'empty_or_scanned' if len(text.strip()) < 30 else None})
    if not pages:
        raise ValueError('Selected page range contains no pages')
    save_rows(args.out, pages)
    print(f'Extracted {len(pages)} pages. Originals were not modified: {args.out}')


def make_samples(pages, limit, seed, width=1200):
    if limit < 1 or width < 200:
        raise ValueError('limit must be positive and width >= 200')
    rng = random.Random(seed)
    groups = {}
    for page in pages:
        text = page['text']
        if len(text.strip()) < 100:
            continue
        for start in range(0, len(text), width):
            snippet = text[start:start + width]
            if len(snippet.strip()) < 100:
                continue
            hits = [word for word in KEYWORDS if word in snippet]
            row = {k: page[k] for k in ('source_id', 'company', 'page', 'sha256', 'url', 'publication_date')}
            row.update({'sample_id': f'{page["source_id"]}-p{page["page"]}-{start}',
                        'start_char': start, 'end_char': start + len(snippet), 'text': snippet,
                        'selection_group': 'keyword_candidate' if hits else 'random_control',
                        'keyword_hits': hits, 'label_status': 'unreviewed',
                        'sampling_note': 'Purposive pilot, not a population error-rate sample; context may be incomplete'})
            groups.setdefault((page['source_id'], row['selection_group']), []).append(row)
    for pool in groups.values():
        rng.shuffle(pool)
    selected = []
    # Alternate source/group pools; a long report must not monopolize the pilot.
    keys = sorted(groups)
    while keys and len(selected) < limit:
        remaining = []
        for key in keys:
            if groups[key] and len(selected) < limit:
                selected.append(groups[key].pop())
            if groups[key]:
                remaining.append(key)
        keys = remaining
    return selected


def sample(args):
    selected = make_samples(rows(args.pages), args.limit, args.seed, args.width)
    if not selected:
        raise ValueError('No extractable text samples; inspect PDF extraction/OCR before continuing')
    save_rows(args.out, selected)
    print(f'Selected {len(selected)} development samples; not a representative test set: {args.out}')


def infer(args):
    inputs = rows(args.samples)
    sample_ids = getattr(args, 'sample_id', None)
    if sample_ids:
        index = {s['sample_id']: s for s in inputs}
        missing = set(sample_ids) - set(index)
        if missing:
            raise ValueError(f'Unknown requested sample IDs: {sorted(missing)}')
        if len(set(sample_ids)) != len(sample_ids):
            raise ValueError('Duplicate requested sample IDs')
        inputs = [index[key] for key in sample_ids]
    inputs = inputs[:args.limit]
    if not inputs:
        raise ValueError('No samples to infer')
    base = local_url(settings().get('base_url', 'http://127.0.0.1:18080/v1'))
    models = request_json(base + '/models', timeout=5)['data']
    if not models:
        raise ValueError('Local server has no loaded model')
    model = models[0]['id']
    prompt = (ROOT / 'prompt.txt').read_text(encoding='utf-8')
    run = Path(args.run_dir)
    run.mkdir(parents=True, exist_ok=False)
    save(run / 'manifest.json', {'started_at': utc(), 'model': model,
                                 'assets': load(ROOT / 'assets.json'),
                                 'prompt': prompt, 'prompt_sha256': sha256(prompt.encode()),
                                 'temperature': 0, 'seed': args.seed,
                                 'selected_sample_ids': [s['sample_id'] for s in inputs],
                                 'semantic_labels_available': False,
                                 'note': 'Local inference baseline; no calibrated probabilities or novelty claim'})
    save_rows(run / 'inputs.jsonl', inputs)
    with (run / 'predictions.jsonl').open('x', encoding='utf-8') as output:
        for index, item in enumerate(inputs):
            started = time.monotonic()
            result = {'sample_id': item['sample_id'], 'status': 'not_completed'}
            payload = {'model': model, 'temperature': 0, 'seed': args.seed,
                       'max_tokens': args.max_tokens, 'stream': False,
                       'response_format': {'type': 'json_object'},
                       'chat_template_kwargs': {'enable_thinking': False},
                       'messages': [{'role': 'system', 'content': prompt},
                                    {'role': 'user', 'content': json.dumps({'company': item['company'], 'publication_date': item['publication_date'], 'text': item['text']}, ensure_ascii=False)}]}
            try:
                response = request_json(base + '/chat/completions', payload, args.timeout)
                result['raw_response'] = response
                choice = response['choices'][0]
                if choice.get('finish_reason') == 'length':
                    raise ValueError('Generation truncated; not a successful empty result')
                prediction = parse_prediction(choice['message']['content'])
                result.update({'status': 'ok', 'prediction': prediction,
                               'evidence_checks': evidence_checks(prediction, item['text'])})
            except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                result.update({'status': 'failed', 'error': str(error)})
            result.update({'elapsed_seconds': round(time.monotonic() - started, 3), 'finished_at': utc()})
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()
            print(f'{index + 1}/{len(inputs)} {item["sample_id"]}: {result["status"]}', flush=True)
    save_rows(run / 'annotation_a.jsonl', [{'sample_id': s['sample_id'], 'annotator': None, 'reference_records': None, 'notes': ''} for s in inputs])
    save_rows(run / 'annotation_b.jsonl', [{'sample_id': s['sample_id'], 'annotator': None, 'reference_records': None, 'notes': ''} for s in inputs])
    audit(argparse.Namespace(run_dir=run))


def audit(args):
    run = Path(args.run_dir)
    inputs = {s['sample_id']: s for s in rows(run / 'inputs.jsonl')}
    predictions = rows(run / 'predictions.jsonl')
    seen = set()
    for row in predictions:
        if row['sample_id'] not in inputs or row['sample_id'] in seen:
            raise ValueError('Unknown or duplicate prediction sample ID')
        seen.add(row['sample_id'])
    successful = [s for s in predictions if s['status'] == 'ok']
    checks = [c for s in successful for c in s['evidence_checks']]
    summary = {'input_count': len(inputs), 'completed_count': len(predictions),
               'format_success': len(successful), 'failed': len(predictions) - len(successful),
               'missing_outputs': len(inputs) - len(seen), 'evidence_quotes': len(checks),
               'quotes_not_located': sum(not c['found'] for c in checks),
               'semantic_error_rate': None, 'calibration_error': None,
               'interpretation': 'Quote location is a mechanical check only; semantic correctness and error probability require independent annotation.'}
    save(run / 'audit.json', summary)
    blocks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>企业风险文本试读</title><style>body{max-width:1000px;margin:30px auto;font:16px/1.7 system-ui;color:#202020}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:16px}article{margin:40px 0;border-top:1px solid #ddd}h1,h2{color:#111}</style><h1>企业风险文本试读</h1><p>以下是模型候选输出。引用定位通过不代表语义正确。人工参照尚未建立。</p>', '<pre>' + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre>']
    for row in predictions:
        item = inputs[row['sample_id']]
        blocks.extend(['<article><h2>' + html.escape(item['company']) + ' · PDF页码 ' + str(item['page']) + '</h2>',
                       '<p>' + html.escape(item['sample_id']) + ' / ' + html.escape(item['selection_group']) + '</p>',
                       '<h3>原文</h3><pre>' + html.escape(item['text']) + '</pre>',
                       '<h3>候选输出与机械核查</h3><pre>' + html.escape(json.dumps(row, ensure_ascii=False, indent=2)) + '</pre></article>'])
    blocks.append('</html>')
    with (run / 'review.html').open('x', encoding='utf-8') as output:
        output.write('\n'.join(blocks))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('doctor'); p.set_defaults(func=doctor)
    p = sub.add_parser('inventory'); p.add_argument('roots', nargs='+'); p.add_argument('--out', required=True); p.set_defaults(func=inventory)
    p = sub.add_parser('register'); p.add_argument('--path', required=True); p.add_argument('--company', required=True)
    p.add_argument('--kind', choices=['annual_report', 'announcement', 'news'], required=True)
    p.add_argument('--url', default=''); p.add_argument('--date', default=None)
    p.add_argument('--registry', default='data/sources.json'); p.set_defaults(func=register)
    p = sub.add_parser('ingest'); p.add_argument('--registry', default='data/sources.json'); p.add_argument('--out', default='data/pages.jsonl')
    p.add_argument('--page-start', type=int, default=1); p.add_argument('--page-end', type=int); p.set_defaults(func=ingest)
    p = sub.add_parser('sample'); p.add_argument('--pages', default='data/pages.jsonl'); p.add_argument('--out', default='data/samples.jsonl')
    p.add_argument('--limit', type=int, default=12); p.add_argument('--seed', type=int, default=42); p.add_argument('--width', type=int, default=1200); p.set_defaults(func=sample)
    p = sub.add_parser('infer'); p.add_argument('--samples', default='data/samples.jsonl'); p.add_argument('--run-dir', required=True)
    p.add_argument('--sample-id', action='append', help='Optional explicit development sample ID; repeat for several')
    p.add_argument('--limit', type=int, default=3); p.add_argument('--seed', type=int, default=42)
    p.add_argument('--max-tokens', type=int, default=1200); p.add_argument('--timeout', type=int, default=300); p.set_defaults(func=infer)
    p = sub.add_parser('audit'); p.add_argument('--run-dir', required=True); p.set_defaults(func=audit)
    args = parser.parse_args()
    if hasattr(args, 'limit') and args.limit < 1:
        parser.error('--limit must be positive')
    if hasattr(args, 'page_start') and (args.page_start < 1 or (args.page_end is not None and args.page_end < args.page_start)):
        parser.error('Invalid PDF page range')
    try:
        args.func(args)
    except (OSError, ValueError, KeyError, ImportError) as error:
        parser.exit(2, f'ERROR: {error}\n')


if __name__ == '__main__':
    main()
