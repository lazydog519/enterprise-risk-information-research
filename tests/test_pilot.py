import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from risk_text_pilot import pilot


def valid_prediction():
    return {'records': [{'entity': '甲公司', 'event': '交付延迟', 'relationship': '材料称可能延迟交付',
                         'status': '可能或条件成立', 'event_time': None,
                         'evidence': ['可能延迟交付'], 'unknowns': ['实际交付日期']}], 'needs_context': False}


class PilotTests(unittest.TestCase):
    def test_local_endpoint_only(self):
        self.assertEqual(pilot.local_url('http://127.0.0.1:18080/v1/'), 'http://127.0.0.1:18080/v1')
        for url in ['https://example.com/v1', 'http://127.0.0.1.evil.test/v1', 'http://user@localhost/v1', 'file:///tmp']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                pilot.local_url(url)
        with self.assertRaises(ValueError):
            pilot.LoopbackRedirect().redirect_request(None, None, 302, '', {}, 'https://outside.test')

    def test_schema_and_status_validation(self):
        value = valid_prediction()
        self.assertEqual(pilot.parse_prediction(json.dumps(value)), value)
        self.assertEqual(pilot.parse_prediction('<think>hidden</think>\n' + json.dumps(value)), value)
        self.assertEqual(pilot.parse_prediction('```json\n' + json.dumps(value) + '\n```'), value)
        for field, replacement in [('status', '置信度90%'), ('evidence', []), ('unknowns', 'unknown')]:
            bad = copy.deepcopy(value)
            bad['records'][0][field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                pilot.parse_prediction(json.dumps(bad))

    def test_evidence_not_semantic_truth(self):
        value = valid_prediction()
        self.assertTrue(pilot.evidence_checks(value, '公司可能 延迟交付。')[0]['found'])
        self.assertFalse(pilot.evidence_checks(value, '公司交付正常。')[0]['found'])
        # Matching a quote does not validate entity assignment; the checker must not claim it does.
        value['records'][0]['entity'] = '错误公司'
        self.assertTrue(pilot.evidence_checks(value, '公司可能延迟交付。')[0]['found'])

    def test_deterministic_sampling_and_offsets(self):
        pages = [{'source_id': source, 'company': source, 'page': number, 'sha256': source,
                  'url': '', 'publication_date': None,
                  'text': ('企业经营风险可能影响供应。' if number == 1 else '企业介绍和一般业务说明。') * 60}
                 for source in ['a', 'b'] for number in [1, 2]]
        samples = pilot.make_samples(pages, 4, 42, width=300)
        self.assertEqual(samples, pilot.make_samples(pages, 4, 42, width=300))
        self.assertEqual({s['source_id'] for s in samples}, {'a', 'b'})
        self.assertEqual({s['selection_group'] for s in samples}, {'keyword_candidate', 'random_control'})
        lookup = {(p['source_id'], p['page']): p for p in pages}
        for sample in samples:
            original = lookup[sample['source_id'], sample['page']]['text']
            self.assertEqual(sample['text'], original[sample['start_char']:sample['end_char']])

    def test_sources_read_only_and_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'company.txt'
            original.write_text('企业原始资料。', encoding='utf-8')
            registry = root / 'sources.json'
            args = argparse.Namespace(path=original, registry=registry, company='甲公司', kind='annual_report', url='', date=None)
            before = original.read_bytes()
            pilot.register(args)
            self.assertEqual(original.read_bytes(), before)
            with self.assertRaises(ValueError):
                pilot.register(args)
            self.assertEqual(len(pilot.load(registry)['sources']), 1)

    def test_unreviewed_annotations_are_not_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pilot.save_rows(root / 'inputs.jsonl', [{'sample_id': '1', 'company': '<script>', 'page': 1,
                                                   'selection_group': 'random_control', 'text': '<script>alert(1)</script>'}])
            pilot.save_rows(root / 'predictions.jsonl', [{'sample_id': '1', 'status': 'failed', 'error': 'timeout'}])
            pilot.audit(argparse.Namespace(run_dir=root))
            report = pilot.load(root / 'audit.json')
            self.assertEqual(report['failed'], 1)
            self.assertIsNone(report['semantic_error_rate'])
            self.assertIsNone(report['calibration_error'])
            self.assertNotIn('<script>', (root / 'review.html').read_text(encoding='utf-8'))
            with self.assertRaises(FileExistsError):
                pilot.save(root / 'audit.json', {})

    def test_changed_original_fails_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'company.txt'
            original.write_text('original', encoding='utf-8')
            registry = root / 'sources.json'
            pilot.register(argparse.Namespace(path=original, registry=registry, company='x', kind='news', url='', date=None))
            original.write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError):
                pilot.ingest(argparse.Namespace(registry=registry, out=root / 'pages.jsonl', page_start=1, page_end=None))

    def test_truncated_generation_kept_as_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            samples = root / 'samples.jsonl'
            pilot.save_rows(samples, [{'sample_id': 's1', 'company': 'x', 'publication_date': None,
                                       'text': '企业资料', 'page': 1, 'selection_group': 'random_control'}])
            replies = [{'data': [{'id': 'test-model'}]},
                       {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}]
            with patch.object(pilot, 'request_json', side_effect=replies):
                pilot.infer(argparse.Namespace(samples=samples, limit=1, run_dir=root / 'run', seed=42, max_tokens=3, timeout=1))
            record = pilot.rows(root / 'run' / 'predictions.jsonl')[0]
            self.assertEqual(record['status'], 'failed')
            self.assertIn('raw_response', record)
            self.assertIsNone(pilot.rows(root / 'run' / 'annotation_a.jsonl')[0]['reference_records'])

    def test_partial_download_resumes_and_hashes(self):
        from risk_text_pilot import bootstrap_local
        import io

        class Response(io.BytesIO):
            def __init__(self, body, status, headers):
                super().__init__(body)
                self.status = status
                self.headers = headers

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'asset.bin'
            spec = {'url': 'https://official.example/asset', 'size': 6, 'sha256': pilot.sha256(b'abcdef')}
            responses = [Response(b'abc', 200, {}), Response(b'def', 206, {'Content-Range': 'bytes 3-5/6'})]
            with patch('risk_text_pilot.bootstrap_local.urllib.request.urlopen', side_effect=responses) as get:
                bootstrap_local.download(spec, target)
            self.assertEqual(target.read_bytes(), b'abcdef')
            self.assertEqual(get.call_args_list[1].args[0].get_header('Range'), 'bytes=3-')


if __name__ == '__main__':
    unittest.main()
