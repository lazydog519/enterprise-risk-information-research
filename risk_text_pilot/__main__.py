"""Single entry point; help and result viewing never run inference."""
import argparse
from pathlib import Path
import subprocess
import sys
import webbrowser

from . import pilot

COMMANDS = ('results', 'test', 'control', 'probe', 'bootstrap', 'doctor', 'inventory',
            'register', 'ingest', 'sample', 'infer', 'audit')


def result_target(run_dir=None):
    run = Path(run_dir or 'runs/20261029_structure_probe_v2')
    run = (run if run.is_absolute() else pilot.ROOT / run).resolve()
    runs_root = (pilot.ROOT / 'runs').resolve()
    if run == runs_root or not run.is_relative_to(runs_root):
        raise ValueError('--run-dir must be a child of this project runs/ directory')
    for name in ('review.html', 'comparison.html'):
        report = run / name
        if report.is_file():
            return report.as_uri()
    return 'https://github.com/lazydog519/enterprise-risk-information-research/blob/main/experiments/20261029-structure-probe/README.md'


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='python -m risk_text_pilot',
        description='Risk-text experiments: read results, prepare data, run explicit controls.')
    parser.add_argument('command', nargs='?', choices=COMMANDS)
    if not args or args[0] in ('-h', '--help'):
        parser.print_help()
        return
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        parser.error('unknown command: ' + command)
    if command == 'results':
        view = argparse.ArgumentParser(prog='python -m risk_text_pilot results')
        view.add_argument('--run-dir')
        view.add_argument('--path-only', action='store_true', help='Print the URL without opening a browser')
        options = view.parse_args(rest)
        try:
            target = result_target(options.run_dir)
        except ValueError as error:
            view.error(str(error))
        print(target)
        if not options.path_only:
            webbrowser.open(target)
    elif command == 'test':
        argparse.ArgumentParser(prog='python -m risk_text_pilot test').parse_args(rest)
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest',
                                 'discover', '-s', 'tests', '-v', '-b'], cwd=pilot.ROOT)
        if result.returncode:
            raise SystemExit(result.returncode)
    elif command == 'control':
        from .controlled_check import main as control_main
        control_main(rest)
    elif command == 'bootstrap':
        from .bootstrap_local import main as bootstrap_main
        bootstrap_main(rest)
    elif command == 'probe':
        from .structure_probe import main as probe_main
        probe_main(rest)
    else:
        pilot.main(args)

if __name__ == '__main__':
    main()
