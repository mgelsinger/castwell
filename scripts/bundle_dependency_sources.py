"""Collect hash-pinned dependency sources and notices for a Windows release.

This checks the reviewed inventory and bytes, not legal sufficiency. Incomplete
inventories can be archived for review, but cannot pass --require-complete.
Archives are retained without extracting or executing upstream code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'Castwell-Dependency-Sources/'
PIN_FILES = ('packaging/windows-runtime.json', 'packaging/windows-requirements.lock')


def stream_digest(stream) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(chunk)
    return value.hexdigest()


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return stream_digest(stream)


def safe_relative(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid source artifact path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ValueError('Invalid source artifact path')
    return value


def read_lock(path: Path, root: Path = ROOT) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported dependency source lock')
    for relative in PIN_FILES:
        expected = data.get('release_inputs', {}).get(relative)
        if not expected or digest(root / relative) != expected:
            raise ValueError('Dependency source inventory is stale: ' + relative)
    ids, paths = set(), set()
    for item in data.get('artifacts', []):
        identifier = item['id']
        relative = safe_relative(item['path'])
        if not identifier or identifier in ids or relative.casefold() in paths:
            raise ValueError('Duplicate dependency source artifact')
        if not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid dependency source checksum')
        if item.get('kind') == 'repository':
            source = root / relative
            if (not source.resolve().is_relative_to(root.resolve()) or not source.is_file()
                    or digest(source) != item['sha256'] or source.stat().st_size != item['bytes']):
                raise ValueError('Dependency build recipe is missing or changed: ' + identifier)
        else:
            url = urlsplit(item['url'])
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                raise ValueError('Dependency source URLs must use public HTTPS without credentials')
        if not isinstance(item.get('bytes'), int) or item['bytes'] <= 0:
            raise ValueError('Missing dependency source size')
        ids.add(identifier)
        paths.add(relative.casefold())
    if not ids:
        raise ValueError('Dependency source inventory has no artifacts')
    components = data.get('components', [])
    if not components or len({item['id'] for item in components}) != len(components):
        raise ValueError('Missing or duplicate component review')
    states = {'source-collected', 'notice-retained', 'exception-reviewed', 'incomplete'}
    for item in components:
        if item.get('status') not in states or not item.get('basis'):
            raise ValueError('Missing component review basis')
        required = item.get('artifacts', [])
        if not required or not set(required) <= ids:
            raise ValueError('Component references missing source artifacts')
        if item['status'] == 'incomplete' and not item.get('remaining'):
            raise ValueError('Incomplete component must state remaining work')
    binaries = data.get('binary_bindings', [])
    if not binaries or len({item['path'].casefold() for item in binaries}) != len(binaries):
        raise ValueError('Missing or duplicate distributed binary binding')
    for item in binaries:
        safe_relative(item['path'])
        if not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid distributed binary checksum')
    for directory in data.get('exclusive_native_directories', []):
        safe_relative(directory)
        if not any(item['path'].startswith(directory + '/') for item in binaries):
            raise ValueError('Exclusive native directory has no binary bindings')
    return data


def is_complete(data: dict) -> bool:
    return (not data.get('remaining')
            and all(item['status'] != 'incomplete' for item in data['components']))


def verify_binaries(data: dict, directory: Path) -> None:
    for item in data['binary_bindings']:
        path = directory / item['path']
        if (not path.resolve().is_relative_to(directory.resolve()) or not path.is_file()
                or digest(path) != item['sha256']):
            raise ValueError('Source inventory does not match the distributed binary: ' + item['path'])
    for relative in data.get('exclusive_native_directories', []):
        target = directory / relative
        if not target.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Native directory escapes the distribution')
        expected = {item['path'].casefold() for item in data['binary_bindings']
                    if item['path'].startswith(relative + '/')
                    and Path(item['path']).suffix.lower() in {'.dll', '.pyd', '.exe'}}
        actual = {path.relative_to(directory).as_posix().casefold() for path in target.rglob('*')
                  if path.is_file() and path.suffix.lower() in {'.dll', '.pyd', '.exe'}}
        if actual != expected:
            raise ValueError('Unexpected native files in source-bound directory: ' + relative)


def collect(item: dict, cache: Path, download: bool) -> Path:
    path = cache / safe_relative(item['path'])
    if not path.resolve().is_relative_to(cache.resolve()):
        raise ValueError('Source cache path escapes its directory')
    if path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']:
        return path
    if not download:
        raise ValueError('Source artifact is missing or changed: ' + item['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    if not partial.resolve().is_relative_to(cache.resolve()):
        raise ValueError('Source download path escapes its directory')
    try:
        request = Request(item['url'], headers={'User-Agent': 'Castwell-source-collection/1'})
        with urlopen(request, timeout=90) as response, partial.open('wb') as target:
            if urlsplit(response.url).scheme != 'https':
                raise ValueError('Source download redirected away from HTTPS')
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > item['bytes']:
                    raise ValueError('Source download exceeds its pinned size: ' + item['id'])
                target.write(chunk)
        if partial.stat().st_size != item['bytes'] or digest(partial) != item['sha256']:
            raise ValueError('Source download checksum mismatch: ' + item['id'])
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def add_file(archive: zipfile.ZipFile, name: str, path: Path) -> None:
    info = zipfile.ZipInfo(PREFIX + name, (2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
        shutil.copyfileobj(source, target, 1024 * 1024)


def add_text(archive: zipfile.ZipFile, name: str, value: str) -> None:
    info = zipfile.ZipInfo(PREFIX + name, (2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, value.encode('utf-8'))


def bundle(lock: Path, cache: Path, output: Path, *, download=False,
           require_complete=False, root: Path = ROOT) -> dict:
    data = read_lock(lock, root)
    complete = is_complete(data)
    if require_complete and not complete:
        raise ValueError('Dependency source review is incomplete; public distribution remains gated')
    files = [(item, root / item['path'] if item.get('kind') == 'repository'
              else collect(item, cache, download)) for item in data['artifacts']]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + '.partial')
    manifest = dict(data, source_requirements_complete=complete)
    text = ('Castwell dependency sources and notices\n\n'
            'Status: ' + ('source inventory collected and reviewed' if complete else 'INCOMPLETE - review archive only') + '.\n'
            'See manifest.json for the exact component scope, hashes, source URLs, review basis and remaining work.\n'
            'Each component retains its own upstream license. Source archives preserve upstream notices.\n'
            'Castwell application source is supplied separately. Model weights are not included.\n'
            'This archive does not certify legal clearance, security, or ad-removal accuracy.\n'
            'Publish this archive with the exact matching app downloads only after every release gate passes.\n'
            'Recipients do not need to download these sources to run Castwell.\n')
    try:
        with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            add_text(archive, 'manifest.json', json.dumps(manifest, indent=2) + '\n')
            add_text(archive, 'README.txt', text)
            add_text(archive, 'SHA256SUMS.txt', ''.join(
                item['sha256'] + '  sources/' + item['path'] + '\n' for item, _ in files))
            for item, path in files:
                add_file(archive, 'sources/' + item['path'], path)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    report = {'schema_version': 1, 'archive': output.name, 'sha256': digest(output),
              'bytes': output.stat().st_size, 'artifact_count': len(files),
              'source_lock_sha256': digest(lock), 'source_requirements_complete': complete}
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def verify_bundle(path: Path, lock: Path, root: Path = ROOT, *, require_complete=False) -> dict:
    data = read_lock(lock, root)
    complete = is_complete(data)
    if require_complete and not complete:
        raise ValueError('Dependency source review is incomplete')
    expected = {PREFIX + 'sources/' + item['path']: item for item in data['artifacts']}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names))
                or set(names) != set(expected) | {PREFIX + name for name in ('manifest.json', 'README.txt', 'SHA256SUMS.txt')}):
            raise ValueError('Unexpected or missing dependency source archive members')
        if json.loads(archive.read(PREFIX + 'manifest.json')) != dict(data, source_requirements_complete=complete):
            raise ValueError('Dependency source archive inventory mismatch')
        sums = ''.join(item['sha256'] + '  sources/' + item['path'] + '\n' for item in data['artifacts'])
        if archive.read(PREFIX + 'SHA256SUMS.txt').decode('utf-8') != sums:
            raise ValueError('Dependency source archive checksum list mismatch')
        for name, item in expected.items():
            if archive.getinfo(name).file_size != item['bytes']:
                raise ValueError('Dependency source archive member size mismatch')
            with archive.open(name) as stream:
                if stream_digest(stream) != item['sha256']:
                    raise ValueError('Dependency source archive checksum mismatch')
    return {'archive': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size,
            'source_lock_sha256': digest(lock), 'source_requirements_complete': complete}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=ROOT / 'packaging/windows-sources.lock.json')
    parser.add_argument('--cache', type=Path, default=ROOT / '.local/dependency-source-cache')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--binary-root', type=Path, help='Also verify the unpacked application against the source bindings')
    args = parser.parse_args()
    if args.verify:
        report = verify_bundle(args.output, args.lock, require_complete=args.require_complete)
    else:
        report = bundle(args.lock, args.cache, args.output, download=args.download,
                        require_complete=args.require_complete)
    if args.binary_root:
        verify_binaries(read_lock(args.lock), args.binary_root)
        report['distributed_binary_bindings_verified'] = True
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print('Dependency source collection failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
