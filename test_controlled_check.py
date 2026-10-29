import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import controlled_check as control
import pilot


class ControlTests(unittest.TestCase):
    def test_prepared_factorial_changes_only_context_and_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / 'source.pdf'
            pdf.write_bytes(b'read-only-original')
            blocks = ['公司2025年年度报告全文', '（3）研发风险', '甲' * 140 + '。',
                      '应对措施：' + '乙' * 170 + '。', '（4）供应风险', '丙' * 140 + '。',
                      '应对措施：' + '丁' * 120 + '。', '十二、报告期内']
            plain, layout = '\n'.join(blocks), '\n\n'.join(blocks)
            page = {'source_id': 's', 'page': 40, 'path': str(pdf), 'text': plain,
                    'sha256': pilot.sha256(pdf.read_bytes())}
            old = {**page, 'sample_id': 'old', 'text': plain[:500], 'end_char': 500}
            pilot.save_rows(root / 'data/pages.jsonl', [page])
            pilot.save_rows(root / 'runs/20261029_catl_short_smoke/inputs.jsonl', [old])
            (root / 'prompt.txt').write_text('old', encoding='utf-8')
            (root / 'prompts').mkdir()
            (root / 'prompts/claim_protocol_v1.txt').write_text('new', encoding='utf-8')
            reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda **kw: layout)] * 40)
            out = root / 'runs/control'
            with patch.object(pilot, 'ROOT', root), patch('pypdf.PdfReader', return_value=reader):
                control.prepare(out)
                control.verify_old(pilot.load(out / 'design.json'))
            a, b, c, d = [pilot.rows(out / f'input_{name}.jsonl')[0]['text'] for name in 'ABCD']
            self.assertEqual(a, c)
            self.assertEqual(b, d)
            self.assertTrue(b.startswith(a))
            self.assertTrue(b.endswith('。'))
            cells = pilot.load(out / 'design.json')['cells']
            self.assertEqual([(x['complete_paragraphs'], x['clarified_protocol']) for x in cells],
                             [(False, False), (True, False), (False, True), (True, True)])

    def test_boundaries_preserve_exact_plain_source(self):
        plain = '甲公司2025年年度报告全文\n40\n（3）研发风险\n如果研发\n不足，可能受损。\n应对措施：持续投入。\n'
        layout = '  甲公司2025年年度报告全文\n\n（3）研发风险\n\n  如果研发\n不足，可能受损。\n\n  应对措施：持续投入。\n\n40'
        blocks = control.paragraph_blocks(plain, layout)
        self.assertEqual([b['kind'] for b in blocks], ['heading', 'narrative', 'narrative'])
        for block in blocks:
            self.assertEqual(plain[block['start_char']:block['end_char']], block['text'])
        self.assertEqual(blocks[1]['text'], '如果研发\n不足，可能受损。')

    def test_incomplete_and_unmapped_are_retained_for_review(self):
        blocks = control.paragraph_blocks('措施：追踪重要\n', '措施：追踪重要\n\n文字顺序不一致')
        self.assertTrue(blocks[0]['requires_review'])
        self.assertFalse(blocks[0]['ends_at_punctuation'])
        self.assertEqual(blocks[1]['kind'], 'unmapped_layout')
        self.assertIsNone(blocks[1]['text'])

    def test_complete_paragraph_is_never_character_cut(self):
        text = '风险条件' * 500 + '。'
        blocks = control.paragraph_blocks(text, text)
        self.assertEqual(blocks[0]['text'], text)

    def test_duplicate_check_does_not_create_semantic_labels(self):
        record = {'entity': 'x', 'status': '确定'}
        stats = control.metrics({'status': 'ok', 'prediction': {'records': [record, dict(record)], 'needs_context': False}})
        self.assertEqual(stats['exact_duplicate_excess'], 1)
        self.assertNotIn('accuracy', stats)

    def test_explicit_prompt_and_generation_budget_are_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prompt = root / 'prompt.txt'
            prompt.write_text('明确规则', encoding='utf-8')
            samples = root / 'inputs.jsonl'
            pilot.save_rows(samples, [{'sample_id': 's', 'company': 'x', 'publication_date': None,
                                       'text': '资料。', 'page': 40, 'selection_group': 'control'}])
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"records":[],"needs_context":false}'}}]}
            with patch.object(pilot, 'request_json', side_effect=[{'data': [{'id': 'same-model'}]}, response]) as request:
                pilot.infer(argparse.Namespace(samples=samples, run_dir=root / 'run', limit=1,
                            prompt_file=prompt, seed=42, max_tokens=1800, timeout=480))
            manifest = pilot.load(root / 'run/manifest.json')
            self.assertEqual(manifest['max_tokens'], 1800)
            self.assertEqual(manifest['prompt'], '明确规则')
            payload = request.call_args_list[1].args[1]
            self.assertEqual((payload['temperature'], payload['seed'], payload['max_tokens']), (0, 42, 1800))

    def test_frozen_old_files_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'old.json'
            path.write_bytes(b'old')
            design = {'old_file_hashes': {'old.json': pilot.sha256(b'old')}}
            with patch.object(pilot, 'ROOT', root):
                control.verify_old(design)
                path.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    control.verify_old(design)


if __name__ == '__main__':
    unittest.main()
