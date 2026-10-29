from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from risk_text_pilot import __main__ as cli
from risk_text_pilot import pilot


class CLITests(unittest.TestCase):
    def test_vscode_tasks_use_python_without_powershell(self):
        tasks = pilot.load(pilot.ROOT / '.vscode/tasks.json')['tasks']
        for task in tasks:
            self.assertTrue(task['command'].endswith('/python.exe'))
            self.assertEqual(task['options']['cwd'], '${workspaceFolder}')
            self.assertIn('risk_text_pilot', task['args'])

    def test_results_path_only_never_opens_browser_or_infers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / 'runs/example/review.html'
            report.parent.mkdir(parents=True)
            report.write_text('<html>saved result</html>', encoding='utf-8')
            with patch.object(pilot, 'ROOT', root), patch.object(pilot, 'main') as infer, \
                    patch.object(cli.webbrowser, 'open') as browser:
                cli.main(['results', '--run-dir', 'runs/example', '--path-only'])
                self.assertEqual(cli.result_target('runs/example'), report.as_uri())
                infer.assert_not_called()
                browser.assert_not_called()

    def test_missing_report_uses_published_summary_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pilot, 'ROOT', Path(folder)):
                self.assertTrue(cli.result_target().startswith('https://github.com/lazydog519/'))
                with self.assertRaises(ValueError):
                    cli.result_target('../outside')

    def test_default_help_never_dispatches_to_model(self):
        with patch.object(pilot, 'main') as infer:
            cli.main([])
            infer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
