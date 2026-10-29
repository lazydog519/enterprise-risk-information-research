from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from risk_text_pilot import pilot as p
from risk_text_pilot import structure_probe as s


class StructureProbeTests(unittest.TestCase):
    def setUp(self):
        self.config = p.load(p.ROOT / 'configs/structure-probe.json')
        self.case = self.config['cases'][0]

    def test_config_and_single_field_perturbation(self):
        s.validate_config(self.config)
        ref = s.auxiliary(self.case, 'reference', {})['records'][0]
        bad = s.auxiliary(self.case, 'corrupted', {})['records'][0]
        self.assertEqual([k for k in ref if ref[k] != bad[k]], [self.case['corruption']['field']])
        self.assertEqual(ref, self.case['reference_structure'])
        self.assertIsNone(s.auxiliary(self.case, 'direct', {}))

    def test_duplicate_cases_rejected(self):
        config = deepcopy(self.config)
        config['cases'].append(config['cases'][0])
        with self.assertRaises(ValueError):
            s.validate_config(config)

    def test_full_section_retains_condition_and_negation(self):
        spec = {'start': '供应链风险', 'end_before': '下一节'}
        value, left, right = s.section('页头\n供应链风险\n如果供应不足，\n可能影响。未发生。\n下一节', spec)
        self.assertEqual(value, '供应链风险\n如果供应不足，\n可能影响。未发生。\n')
        self.assertEqual(right - left, len(value))
        with self.assertRaises(ValueError):
            s.section('供应链风险供应链风险下一节', spec)

    def test_table_column_whitespace_is_not_removed(self):
        value, _, _ = s.section('销量表\n产量  销量\n405554  396270\n结束',
                                {'start': '销量表', 'end_before': '结束'})
        self.assertIn('405554  396270', value)

    def test_count_changes_without_calling_them_accuracy(self):
        calls = [{'case_id': self.case['id'], 'phase': 'extract', 'condition': None, 'status': 'failed'}]
        for condition, label in zip(s.CONDITIONS, ['S', 'U', 'U', 'C']):
            calls.append({'case_id': self.case['id'], 'phase': 'judge', 'condition': condition,
                          'status': 'ok', 'output': {'label': label}})
        result = s.summarize([self.case], calls)
        self.assertIsNone(result['semantic_accuracy'])
        self.assertEqual(result['extraction_successes'], 0)
        self.assertEqual(result['conditions']['generated']['improved_vs_draft'], 1)
        self.assertEqual(result['conditions']['corrupted']['changed_from_direct'], 1)
        with self.assertRaises(ValueError):
            s.summarize([self.case], calls + [calls[-1]])

    def test_length_failure_not_accepted(self):
        response = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"label":"S","reason":"x"}'}}]}
        with patch.object(p, 'request_json', return_value=response):
            result = s.call('http://127.0.0.1:18080/v1', 'model', 'system', {}, s.JUDGE_SCHEMA, 10)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('output', result)

    def test_loaded_model_hash_must_match_pinned_weights(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.gguf'
            path.write_bytes(b'test weights')
            assets = {'model': {'sha256': p.sha256(b'test weights')}}
            with patch.object(p, 'request_json', return_value={'model_path': str(path)}):
                self.assertEqual(s.verify_model('http://127.0.0.1:18081/v1', assets), assets['model']['sha256'])
                assets['model']['sha256'] = '0' * 64
                with self.assertRaises(ValueError):
                    s.verify_model('http://127.0.0.1:18081/v1', assets)


if __name__ == '__main__':
    unittest.main()
