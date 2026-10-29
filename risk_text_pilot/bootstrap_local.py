"""Download pinned official public artifacts; never install system-wide software."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import secrets
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(spec, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != spec['sha256']:
            raise ValueError(f'Existing artifact hash mismatch; preserve and inspect: {target}')
        print(f'Verified existing {target.name}', flush=True)
        return
    partial = target.with_suffix(target.suffix + '.partial')
    start = partial.stat().st_size if partial.exists() else 0
    attempts = 0
    while start < spec['size']:
        attempts += 1
        if attempts > 128:
            raise OSError(f'Download interrupted repeatedly; rerun to resume {partial}')
        if shutil.disk_usage(target.parent).free < spec['size'] - start + 512 * 1024**2:
            raise OSError('Not enough free space for this download')
        request = urllib.request.Request(spec['url'], headers={'User-Agent': 'risk-text-pilot/0.1'})
        if start:
            request.add_header('Range', f'bytes={start}-')
        with urllib.request.urlopen(request, timeout=60) as response:
            resumed = response.status == 206
            if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                raise ValueError('Unexpected Content-Range; preserving partial file')
            if start and not resumed:
                raise OSError('Server refused resume; preserving partial download')
            done = start if resumed else 0
            checkpoint = done
            with partial.open('ab' if resumed else 'wb') as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
                    done += len(block)
                    if done - checkpoint >= 64 * 1024**2:
                        print(f'{target.name}: {done / spec["size"]:.0%}', flush=True)
                        checkpoint = done
        new_start = partial.stat().st_size
        if new_start <= start:
            raise OSError('Download made no progress; preserving partial file')
        start = new_start
    if partial.stat().st_size != spec['size'] or digest(partial) != spec['sha256']:
        raise ValueError(f'Download size/hash mismatch; preserving {partial}')
    partial.rename(target)
    print(f'SHA256 verified: {target.name}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--runtime-only', action='store_true')
    parser.add_argument('--backend', choices=['cpu', 'vulkan'], default='cpu')
    args = parser.parse_args(argv)
    specs = json.loads((ROOT / 'configs/assets.json').read_text(encoding='utf-8'))
    archive = ROOT / '.runtime' / ('llama-b11960-' + args.backend + '.zip')
    runtime_spec = specs['runtime'] if args.backend == 'cpu' else specs['vulkan_runtime']
    download(runtime_spec, archive)
    runtime = ROOT / '.runtime' / ('llama-b11960-' + args.backend)
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not (runtime / name).resolve().is_relative_to(runtime.resolve()):
                raise ValueError('Unsafe archive member')
            member = zipped.getinfo(name)
            target = runtime / name
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif target.exists():
                if digest(target) != hashlib.sha256(zipped.read(name)).hexdigest():
                    raise ValueError(f'Existing runtime member differs; preserve and inspect: {target}')
            else:
                zipped.extract(member, runtime)
    print(f'Runtime ready: {runtime}', flush=True)
    if args.runtime_only:
        return
    model = args.model_dir.resolve() / specs['model']['filename']
    download(specs['model'], model)
    settings = ROOT / 'settings.local.json'
    if settings.exists():
        current = json.loads(settings.read_text(encoding='utf-8'))
    else:
        current = {}
    current.update({'model_path': str(model), 'base_url': 'http://127.0.0.1:18080/v1'})
    key_file = ROOT / '.runtime' / 'inference.key'
    if not key_file.exists():
        with key_file.open('x', encoding='ascii') as output:
            output.write(secrets.token_urlsafe(32) + '\n')
    current['api_key_file'] = str(key_file)
    settings.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Local settings saved; model stays outside Git.', flush=True)


if __name__ == '__main__':
    main()
