"""Build a pinned Windows x64 CUDA distribution without user data or model weights.

The supplied native stages must come from fetch_windows_dependencies.py and the
official CUDA/cuDNN packages recorded in packaging/windows-runtime.json. This
script installs only hash-locked wheels, freezes one application directory, and
builds a portable archive plus a current-user Inno Setup installer.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'packaging' / 'windows-requirements.lock'
FORBIDDEN = {'.gguf', '.sqlite', '.sqlite3', '.db', '.mp3', '.m4a', '.wav', '.mp4'}
PROJECT_LICENSE_FILES = ('LICENSE', 'NOTICE')
BUNDLED_GUIDES = ('ad-removal-production-plan.md',
                 'third-party-sources.md',
                 'third-party-sources.json',
                 'evaluations/2026-10-15-controlled-media.json',
                 'evaluations/2026-10-15-controlled-pyav-pcm.json',
                 'evaluations/2026-10-12-ad-removal-system.md',
                 'evaluations/2026-10-12-ad-removal-system.json',
                 'evaluations/2026-10-13-five-minute-cuda.md',
                 'evaluations/2026-10-13-five-minute-cuda.json',
                 'evaluations/2026-10-13-saved-cut-validation.json',
                 'evaluations/2026-10-14-tolerance-comparison.md',
                 'evaluations/2026-10-14-tolerance-comparison.json')
RELEASE_INPUTS = ('castwell', 'pyproject.toml', 'README.md', 'LICENSE', 'NOTICE',
                  'packaging', 'scripts/build_windows.py', 'scripts/desktop_entry.py',
                  'scripts/bundle_dependency_sources.py', 'scripts/build_windows_media.py',
                  'scripts/build_windows_pyav.py', 'scripts/fetch_windows_dependencies.py',
                  'docs/windows.md', 'docs/production.md',
                  *('docs/' + relative for relative in BUNDLED_GUIDES))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def clean_source_revision(root: Path = ROOT, expected: str | None = None) -> str:
    """Name only committed release inputs; never expose dirty paths in errors."""
    def git(arguments):
        try:
            result = subprocess.run(['git', *arguments], cwd=root, capture_output=True,
                                    text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Cannot verify committed release inputs') from None
        if result.returncode:
            raise ValueError('Cannot verify committed release inputs')
        return result.stdout
    status = git(['status', '--porcelain=v1', '-z', '--untracked-files=all', '--', *RELEASE_INPUTS])
    if status:
        raise ValueError('Release inputs contain uncommitted changes; commit or discard them before building')
    revision = git(['rev-parse', '--verify', 'HEAD']).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Cannot determine the exact project source revision')
    if expected is not None and revision != expected:
        raise ValueError('Project source revision changed during the build; rebuild from the intended commit')
    return revision


def source_fingerprint(root: Path = ROOT) -> str:
    files = list((root / 'castwell').rglob('*.py'))
    files += [path for path in (root / 'castwell' / 'static').rglob('*') if path.is_file()]
    files += [root / 'scripts' / 'desktop_entry.py', root / 'pyproject.toml',
              root / 'packaging' / 'castwell.spec', root / 'packaging' / 'castwell.iss',
              root / 'packaging' / 'windows-requirements.lock', root / 'packaging' / 'windows-runtime.json']
    files += [root / name for name in PROJECT_LICENSE_FILES]
    value = hashlib.sha256()
    for path in sorted(set(files)):
        if path.is_file():
            value.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
            value.update(digest(path).encode('ascii') + b'\n')
    return value.hexdigest()


def project_license_files(root: Path) -> list[Path]:
    paths = [root / name for name in PROJECT_LICENSE_FILES]
    for path in paths:
        if not path.is_file() or not path.read_bytes().strip():
            raise ValueError('Missing or empty project distribution file: ' + path.name)
    return paths


def bundle_project_notices(app: Path, root: Path) -> None:
    for source in project_license_files(root):
        shutil.copy2(source, app / source.name)


def run(command, *, env=None) -> None:
    # Log the operation, not embedded Python source or inherited configuration.
    display = ' '.join(str(part) for part in command[:3])
    print('Running ' + display, flush=True)
    subprocess.run([str(part) for part in command], cwd=ROOT, env=env, check=True)


def managed_remove(path: Path) -> None:
    """Only delete our build outputs, never a caller's vendor or library path."""
    resolved = path.resolve()
    allowed = ((ROOT / '.local' / 'windows-build').resolve(),)
    if not any(resolved.is_relative_to(parent) and resolved != parent for parent in allowed):
        raise ValueError(f'Refusing to replace unmanaged build path: {path}')
    if resolved.exists():
        shutil.rmtree(resolved)


def native_files(directory: Path, names: set[str]) -> list[Path]:
    names = {name.lower() for name in names}
    if not directory.is_dir():
        raise ValueError(f'Native stage is missing: {directory}')
    paths = [path for path in sorted(directory.rglob('*')) if path.is_file()
             and (not path.name.lower().startswith('ggml-cpu-') or path.name.lower() == 'ggml-cpu-x64.dll')
             and (path.suffix.lower() == '.dll' or path.name.lower() in names)]
    actual = {path.name.lower() for path in paths}
    if not names <= actual:
        raise ValueError(f'Native stage lacks required files: {sorted(names - actual)}')
    for path in paths:
        # Confirm vendor PE files are x64 before analysis; don't execute unknown tools.
        with path.open('rb') as stream:
            header = stream.read(64)
            if header[:2] != b'MZ':
                raise ValueError(f'Not a Windows executable: {path.name}')
            stream.seek(struct.unpack_from('<I', header, 60)[0])
            image = stream.read(6)
            if image[:4] != b'PE\0\0' or image[4:] != b'\x64\x86':
                raise ValueError(f'Expected an x64 Windows executable: {path.name}')
    return paths


def make_icon(python: Path, destination: Path) -> None:
    # Render the existing 64x64 favicon's rounded square and headphone strokes.
    program = r'''
from PIL import Image, ImageDraw
import sys
s=8
im=Image.new('RGBA',(64*s,64*s),(0,0,0,0))
d=ImageDraw.Draw(im)
d.rounded_rectangle((0,0,64*s-1,64*s-1),radius=18*s,fill='#ff9c72')
ink='#10151e'; width=7*s
d.arc((19*s,14*s,45*s,40*s),180,360,fill=ink,width=width)
for x in (19,45):
    d.line((x*s,27*s,x*s,44*s),fill=ink,width=width)
    for y in (27,44):
        r=3.5*s
        d.ellipse((x*s-r,y*s-r,x*s+r,y*s+r),fill=ink)
im.save(sys.argv[1],format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
'''
    run([python, '-c', program, destination])


def wheel_notices(wheelhouse: Path, destination: Path, source_built: dict | None = None) -> list[dict]:
    entries = []
    locked = {}
    for line in LOCK.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([^= ]+)==([^ ]+) --hash=sha256:([a-f0-9]{64})', line)
        if match:
            locked[match[1].lower().replace('_', '-')] = (match[2], match[3])
    if source_built:
        name = source_built['name']
        if name in locked:
            raise ValueError('Source-built dependency must be separate from the upstream wheel lock')
        locked[name] = (source_built['version'], source_built['wheel']['sha256'])
    for wheel in sorted(wheelhouse.glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
            raw = archive.read(metadata_name)
            metadata = BytesParser().parsebytes(raw)
            name = metadata['Name'].lower().replace('_', '-')
            version = metadata['Version']
            if name not in locked or locked[name][0] != version:
                continue
            sha = digest(wheel)
            if sha != locked[name][1]:
                raise ValueError(f'Wheel digest changed: {wheel.name}')
            target = destination / 'python' / (name + '-' + version)
            target.mkdir(parents=True, exist_ok=True)
            (target / 'METADATA.txt').write_bytes(raw)
            count = 0
            for member in archive.namelist():
                filename = Path(member).name
                if member.endswith('/') or not re.match(r'(?i)(license|licence|notice|copying)', filename):
                    continue
                # Retain each package's license filenames, avoiding wheel path traversal.
                relative = Path(*Path(member).parts[1:])
                if '..' in relative.parts or relative.is_absolute():
                    raise ValueError('Unsafe license path inside wheel')
                license_path = target / relative
                license_path.parent.mkdir(parents=True, exist_ok=True)
                license_path.write_bytes(archive.read(member))
                count += 1
            entries.append({'name': name, 'version': version, 'wheel': wheel.name,
                            'sha256': sha, 'license_files': count,
                            'license_expression': metadata.get('License-Expression', '')})
    if {entry['name'] for entry in entries} != set(locked):
        raise ValueError('Wheelhouse is incomplete for the locked dependency set')
    (destination / 'python-dependencies.json').write_text(json.dumps(entries, indent=2) + '\n', encoding='utf-8')
    return entries


def archive_portable(app: Path, output: Path) -> None:
    # Stable zip member order and timestamps. PE binaries themselves are not
    # promised byte-for-byte reproducible across independent build machines.
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo('Castwell/' + path.relative_to(app).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        marker = zipfile.ZipInfo('Castwell/portable.flag', (2026, 1, 1, 0, 0, 0))
        marker.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(marker, 'Castwell portable mode. The library is stored in the adjacent Data directory.\n')


def remove_duplicate_cuda_dlls(app: Path) -> list[str]:
    """Keep the explicitly configured CUDA directory as the single DLL source."""
    internal = app / '_internal'
    removed = []
    for container in (internal, internal / 'llama'):
        candidates = {path.name.casefold(): path for path in container.glob('*.dll')}
        for canonical in sorted((internal / 'cuda').glob('*.dll')):
            duplicate = candidates.get(canonical.name.casefold())
            if duplicate is None:
                continue
            if duplicate.stat().st_size != canonical.stat().st_size or digest(duplicate) != digest(canonical):
                raise ValueError(f'Conflicting CUDA runtime versions: {canonical.name}')
            duplicate.unlink()
            removed.append(duplicate.relative_to(internal).as_posix())
    return removed


def isolated_build_path(python: Path, native_directories: list[Path], system_root: Path) -> str:
    """Keep unrelated app/runtime folders out of PyInstaller's DLL search."""
    paths = [*native_directories, python.parent, system_root / 'System32', system_root]
    return os.pathsep.join(dict.fromkeys(str(path.resolve()) for path in paths))


def verify_pinned_vc_runtime(directory: Path, metadata: dict) -> list[Path]:
    pins = metadata.get('vc-runtime', {}).get('binaries', [])
    if not pins:
        raise ValueError('Missing pinned Microsoft runtime inventory')
    files = native_files(directory, {item['name'] for item in pins})
    by_name = {path.name.casefold(): path for path in files}
    if len(by_name) != len(files) or set(by_name) != {item['name'].casefold() for item in pins}:
        raise ValueError('Microsoft runtime stage contains unexpected binaries')
    for item in pins:
        if digest(by_name[item['name'].casefold()]) != item['sha256']:
            raise ValueError('Microsoft runtime checksum differs: ' + item['name'])
    return files


def normalize_vc_runtime(app: Path, paths: list[Path]) -> list[str]:
    """Use the reviewed compiler runtime in every collected copy of its DLLs."""
    replacements = []
    internal = app / '_internal'
    for source in paths:
        expected = digest(source)
        targets = {internal / source.name}
        targets.update(path for path in internal.rglob('*.dll') if path.name.casefold() == source.name.casefold())
        for target in sorted(targets):
            if not target.is_file() or digest(target) != expected:
                shutil.copy2(source, target)
                replacements.append(target.relative_to(internal).as_posix())
    return replacements


def bundle_guides(app: Path, root: Path = ROOT, source_ref: str = 'main') -> None:
    """Keep local crosslinks usable without copying source-only guide history."""
    included = {'windows.md', 'production.md', 'ad-removal-production-plan.md',
                'third-party-sources.md'}
    for relative in BUNDLED_GUIDES:
        source = root / 'docs' / relative
        if source.is_file():
            target = app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    inventory = app / 'third-party-sources.json'
    if inventory.is_file():
        notices = app / 'THIRD-PARTY-NOTICES'
        notices.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inventory, notices / 'source-inventory.json')
    guide = root / 'docs' / 'windows.md'
    if guide.is_file():
        content = guide.read_text(encoding='utf-8')
        content = content.split('Maintainers can exercise both artifacts', 1)[0].rstrip() + '\n'
        content = re.sub(r'\]\(\.\./(LICENSE|NOTICE)(#[^)]+)?\)', r'](\1\2)', content)
        for filename in ('QUICKSTART.md', 'windows.md'):
            (app / filename).write_text(content, encoding='utf-8')
    production = root / 'docs' / 'production.md'
    if production.is_file():
        content = production.read_text(encoding='utf-8')
        def link(match):
            target = match.group(1)
            if target.split('#', 1)[0] in included:
                return match.group(0)
            return '](https://github.com/mgelsinger/castwell/blob/' + source_ref + '/docs/' + target + ')'
        content = re.sub(r'\]\(([A-Za-z0-9_.-]+\.md(?:#[A-Za-z0-9_.-]+)?)\)', link, content)
        (app / 'production.md').write_text(content, encoding='utf-8')
    # Source-only history and checkout instructions remain usable in package
    # copies without recursively copying their entire documentation tree.
    docs = (root / 'docs').resolve()
    guide_sources = {relative: relative for relative in BUNDLED_GUIDES if relative.endswith('.md')}
    guide_sources.update({'windows.md': 'windows.md', 'QUICKSTART.md': 'windows.md',
                          'production.md': 'production.md'})
    for relative, source_relative in guide_sources.items():
        destination = app / relative
        if not destination.is_file():
            continue
        def source_link(match):
            target = match.group(1)
            filename, separator, anchor = target.partition('#')
            if not filename or ':' in filename or filename.startswith('/'):
                return match.group(0)
            if (destination.parent / filename).is_file():
                return match.group(0)
            source_target = (docs / source_relative).parent / filename
            source_target = source_target.resolve()
            repository = root.resolve()
            if not source_target.is_relative_to(repository) or not source_target.is_file():
                return match.group(0)
            checkout_relative = source_target.relative_to(repository)
            if checkout_relative.parts[0] not in {'docs', 'packaging', 'scripts'}:
                return match.group(0)
            return '](https://github.com/mgelsinger/castwell/blob/' + source_ref + '/' + checkout_relative.as_posix() + separator + anchor + ')'
        content = re.sub(r'\]\(([^)\s]+)\)', source_link, destination.read_text(encoding='utf-8'))
        destination.write_text(content, encoding='utf-8')


def validate_notice_pins(notices: Path, metadata: dict) -> None:
    for item in metadata.values():
        if item.get('kind') != 'notice':
            continue
        path = notices / item['filename']
        if not path.is_file() or digest(path) != item['sha256']:
            raise ValueError('Missing or unverified third-party notice: ' + item['filename'])


def verify_dependency_sources(archive: Path, app: Path, *, require_complete=False) -> dict:
    command = [sys.executable, str(ROOT / 'scripts/bundle_dependency_sources.py'),
               '--verify', '--output', str(archive.resolve()), '--binary-root', str(app.resolve())]
    if require_complete:
        command.append('--require-complete')
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError('Dependency source verification failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


def verify_pyav_build(receipt: Path, root: Path = ROOT) -> tuple[dict, Path]:
    """Check a locally executed source build's receipt and wheel before use.

    Receipts describe our trusted source-build workflow, not third-party binary
    attestations. A release additionally binds the actual wheel and DLL hashes
    in its reviewed dependency-source inventory.
    """
    data = json.loads(receipt.read_text(encoding='utf-8'))
    source_lock = json.loads((root / 'packaging/windows-pyav.lock.json').read_text(encoding='utf-8'))
    expected = {'lock_sha256': root / 'packaging/windows-pyav.lock.json',
                'recipe_sha256': root / 'scripts/build_windows_pyav.py',
                'media_lock_sha256': root / 'packaging/windows-media.lock.json',
                'media_recipe_sha256': root / 'scripts/build_windows_media.py'}
    if data.get('schema_version') != 1 or data.get('name') != 'av' or data.get('version') != source_lock['version']:
        raise ValueError('Unexpected source-built PyAV identity')
    for key, path in expected.items():
        if not path.is_file() or data.get(key) != digest(path):
            raise ValueError('Source-built PyAV receipt is stale: ' + key)
    wheel = data['wheel']
    path = (receipt.parent / wheel['relative_path']).resolve()
    if (not path.is_relative_to(receipt.parent.resolve()) or not path.is_file()
            or path.name != wheel['filename'] or path.name != source_lock['wheel_filename']
            or path.stat().st_size != wheel['bytes'] or digest(path) != wheel['sha256']):
        raise ValueError('Source-built PyAV wheel is missing or changed')
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(names) != 1:
            raise ValueError('Source-built PyAV wheel metadata is ambiguous')
        metadata = BytesParser().parsebytes(archive.read(names[0]))
        for key in ('Name', 'Version'):
            values = metadata.get_all(key, [])
            if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
                raise ValueError('Source-built PyAV wheel metadata is ambiguous')
        if metadata['Name'].lower() != data['name'] or metadata['Version'] != data['version']:
            raise ValueError('Source-built PyAV wheel identity differs from its receipt')
    return data, path


def python_identity(python: Path) -> dict:
    result = subprocess.run([str(python), '-c',
        'import json,platform,struct,sys; print(json.dumps({"version":list(sys.version_info[:2]),"bits":struct.calcsize("P")*8,"platform":platform.system()}))'],
        capture_output=True, text=True, check=True)
    identity = json.loads(result.stdout)
    if identity != {'version': [3, 12], 'bits': 64, 'platform': 'Windows'}:
        raise ValueError('The selected build Python and existing virtual environment must be Windows x64 CPython 3.12')
    return identity


def dependency_check(python: Path) -> None:
    """GPU ONNX supplies the same import while upstream declares the CPU name."""
    result = subprocess.run([str(python), '-m', 'pip', 'check'], cwd=ROOT,
                            capture_output=True, text=True)
    expected = 'faster-whisper 1.2.1 requires onnxruntime, which is not installed.'
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode and lines != [expected]:
        raise ValueError('Unexpected build dependency inconsistency: ' + result.stdout + result.stderr)
    run([python, '-c', 'import importlib.metadata as m; import faster_whisper,ctranslate2,av,onnxruntime; '
         'assert "CUDAExecutionProvider" in onnxruntime.get_available_providers(); '
         'assert all(d.metadata["Name"].lower() != "onnxruntime" for d in m.distributions()); '
         'print("GPU inference imports verified; upstream CPU ONNX metadata requirement intentionally replaced")'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg-dir', required=True, type=Path)
    parser.add_argument('--llama-dir', required=True, type=Path)
    parser.add_argument('--cuda-dir', required=True, type=Path)
    parser.add_argument('--notices-dir', required=True, type=Path)
    parser.add_argument('--vc-runtime-dir', type=Path, help='Hash-pinned Microsoft runtime stage from the dependency fetcher')
    parser.add_argument('--pyav-build-manifest', type=Path, help='Receipt from the pinned local PyAV source build')
    parser.add_argument('--inno-path', type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--venv', type=Path, default=ROOT / '.local' / 'windows-build-venv')
    parser.add_argument('--wheelhouse', type=Path, default=ROOT / '.local' / 'windows-wheelhouse')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist' / 'windows')
    parser.add_argument('--skip-installer', action='store_true', help='Build portable archive only')
    parser.add_argument('--dependency-source-bundle', type=Path,
                        help='Separate source archive verified against the locked inventory and frozen binaries')
    parser.add_argument('--require-dependency-sources', action='store_true',
                        help='Reject an absent or incomplete corresponding-source review')
    parser.add_argument('--skip-runtime-check', action='store_true',
                        help='CI only: create an explicitly unverified artifact without NVIDIA hardware')
    args = parser.parse_args()
    if platform.system() != 'Windows' or struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 12):
        parser.error('Build with 64-bit CPython 3.12 on Windows')
    project_license_files(ROOT)
    source_revision = clean_source_revision(ROOT)
    if args.require_dependency_sources and not args.dependency_source_bundle:
        parser.error('--require-dependency-sources needs --dependency-source-bundle')
    if not args.skip_installer and (not args.inno_path or not args.inno_path.is_file()):
        parser.error('--inno-path must point to ISCC.exe for the installer build')
    if not args.notices_dir.is_dir() or not any(args.notices_dir.iterdir()):
        parser.error('--notices-dir must contain the native runtime licenses and provenance')
    runtime_metadata = json.loads((ROOT / 'packaging' / 'windows-runtime.json').read_text(encoding='utf-8'))
    pyav_build, pyav_wheel = None, None
    if runtime_metadata.get('pyav', {}).get('kind') == 'source-build':
        if not args.pyav_build_manifest:
            parser.error('--pyav-build-manifest is required for the controlled PyAV build')
        pyav_build, pyav_wheel = verify_pyav_build(args.pyav_build_manifest)
    validate_notice_pins(args.notices_dir, runtime_metadata)
    vc_runtime = []
    if runtime_metadata.get('vc-runtime'):
        if not args.vc_runtime_dir:
            parser.error('--vc-runtime-dir is required by the current runtime pins')
        vc_runtime = verify_pinned_vc_runtime(args.vc_runtime_dir, runtime_metadata)
    stages = [('ffmpeg', args.ffmpeg_dir, {'ffmpeg.exe', 'ffprobe.exe'}),
              ('llama', args.llama_dir, {'llama-server.exe', 'ggml-cuda.dll', 'ggml-cpu-x64.dll'}),
              ('cuda', args.cuda_dir, {'cublas64_12.dll', 'cublasLt64_12.dll', 'cudart64_12.dll',
                                      'cudnn64_9.dll', 'cudnn_ops64_9.dll', 'cudnn_adv64_9.dll',
                                      'cudnn_cnn64_9.dll', 'cudnn_graph64_9.dll',
                                      'cudnn_engines_precompiled64_9.dll',
                                      'cudnn_engines_runtime_compiled64_9.dll', 'cudnn_heuristic64_9.dll',
                                      'cufft64_11.dll', 'curand64_10.dll', 'nvrtc64_120_0.dll',
                                      'nvrtc-builtins64_124.dll'})]
    inputs = {}
    for label, directory, required in stages:
        paths = native_files(directory, required)
        inputs[label] = [{'name': path.relative_to(directory).as_posix(), 'sha256': digest(path),
                          'bytes': path.stat().st_size} for path in paths]
    if vc_runtime:
        inputs['vc-runtime'] = [{'name': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size}
                                for path in vc_runtime]
    if not (ROOT / 'scripts' / 'desktop_entry.py').is_file():
        parser.error('scripts/desktop_entry.py is missing')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        parser.error('Windows version must contain three numeric components')
    build = ROOT / '.local' / 'windows-build'
    build.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.wheelhouse.mkdir(parents=True, exist_ok=True)
    python = args.venv / 'Scripts' / 'python.exe'
    if not python.exists():
        python_identity(args.python)
        run([args.python, '-m', 'venv', args.venv])
    python_identity(python)
    run([python, '-m', 'pip', 'download', '--only-binary=:all:', '--no-deps', '--require-hashes',
         '--requirement', LOCK, '--dest', args.wheelhouse])
    # Never install the CPU ONNX distribution into its shared namespace.
    installed = subprocess.run([str(python), '-m', 'pip', 'show', 'onnxruntime'], capture_output=True)
    reinstall = []
    if installed.returncode == 0:
        run([python, '-m', 'pip', 'uninstall', '--yes', 'onnxruntime'])
        reinstall = ['--force-reinstall']
    run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', '--find-links', args.wheelhouse,
         '--require-hashes', '--requirement', LOCK, *reinstall])
    if pyav_wheel:
        target_wheel = args.wheelhouse / pyav_wheel.name
        if target_wheel.resolve() != pyav_wheel.resolve():
            shutil.copy2(pyav_wheel, target_wheel)
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', '--force-reinstall', target_wheel])
    run([python, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', ROOT])
    dependency_check(python)
    icon = build / 'castwell.ico'
    make_icon(python, icon)
    version_file = build / 'version.txt'
    number = tuple(int(part) for part in version.split('.')) + (0,)
    version_file.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={number!r}, prodvers={number!r}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', 'Castwell contributors'), StringStruct('FileDescription', 'Castwell podcast suite'), StringStruct('FileVersion', '{version}'), StringStruct('ProductName', 'Castwell'), StringStruct('ProductVersion', '{version}'), StringStruct('OriginalFilename', 'Castwell.exe')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n",
        encoding='utf-8')
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME'):
        env.pop(key, None)
    native_search = [path for path in (args.vc_runtime_dir, args.cuda_dir, args.llama_dir, args.ffmpeg_dir) if path]
    env['PATH'] = isolated_build_path(python, native_search, Path(os.environ.get('SystemRoot', r'C:\Windows')))
    env.update(CASTWELL_BUILD_ROOT=str(ROOT), CASTWELL_FFMPEG_DIR=str(args.ffmpeg_dir.resolve()),
               CASTWELL_LLAMA_DIR=str(args.llama_dir.resolve()), CASTWELL_CUDA_DIR=str(args.cuda_dir.resolve()),
               CASTWELL_BUILD_ICON=str(icon), CASTWELL_BUILD_VERSION_FILE=str(version_file),
               PYTHONHASHSEED='0', SOURCE_DATE_EPOCH='1767225600',
               HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    # Prevent the builder from inheriting private application configuration.
    for key in tuple(env):
        if key.startswith('CASTWELL_') and not key.startswith('CASTWELL_BUILD_') and key not in {'CASTWELL_FFMPEG_DIR', 'CASTWELL_LLAMA_DIR', 'CASTWELL_CUDA_DIR'}:
            env.pop(key, None)
    frozen = build / 'frozen'
    for attempt in range(2):
        source_hash = source_fingerprint()
        managed_remove(frozen)
        run([python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', frozen,
             '--workpath', build / 'analysis', ROOT / 'packaging' / 'castwell.spec'], env=env)
        if source_fingerprint() == source_hash:
            break
        if attempt:
            raise ValueError('Application source is still changing during freeze; stop edits and rebuild')
        print('Application source changed during freeze. Rebuilding before packaging.', flush=True)
    app = frozen / 'Castwell'
    normalized_vc_dlls = normalize_vc_runtime(app, vc_runtime)
    deduplicated_dlls = remove_duplicate_cuda_dlls(app)
    shutil.copy2(icon, app / '_internal' / 'castwell.ico')
    notices = app / 'THIRD-PARTY-NOTICES'
    shutil.copytree(args.notices_dir, notices, dirs_exist_ok=True)
    dependencies = wheel_notices(args.wheelhouse, notices, pyav_build)
    bundle_project_notices(app, ROOT)
    bundle_guides(app, source_ref=source_revision)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, notices / 'Python-LICENSE.txt')
    runtime_pins = ROOT / 'packaging' / 'windows-runtime.json'
    shutil.copy2(runtime_pins, notices / 'runtime-provenance.json')
    # Local wheel installation writes the source checkout's absolute URL into
    # metadata. It is irrelevant to a frozen release and should not be shipped.
    for source_url in app.rglob('direct_url.json'):
        source_url.unlink()
    if (app / 'portable.flag').exists():
        raise ValueError('Installer input must not have a portable marker')
    for path in app.rglob('*'):
        if path.is_file() and (path.suffix.lower() in FORBIDDEN or path.name == '.env'):
            raise ValueError(f'Private data or model artifact entered distribution: {path.name}')
    runtime_verified = False
    if not args.skip_runtime_check:
        runtime_report = build / 'frozen-runtime-check.json'
        if runtime_report.exists():
            runtime_report.unlink()
        completed = subprocess.run([str(app / 'Castwell.exe'), '--check-runtime',
                                    '--report', str(runtime_report)], cwd=app,
                                   env=env, timeout=180, check=False)
        report = json.loads(runtime_report.read_text(encoding='utf-8')) if runtime_report.is_file() else {}
        if completed.returncode or not report.get('ready') or not report.get('frozen'):
            raise ValueError('Frozen NVIDIA runtime validation failed: ' + '; '.join(report.get('errors', ['No successful runtime report'])))
        runtime_verified = True
    if source_fingerprint() != source_hash:
        raise ValueError('Application source changed after freeze; rebuild before creating release artifacts')
    clean_source_revision(ROOT, expected=source_revision)
    dependency_sources = None
    if args.dependency_source_bundle:
        dependency_sources = verify_dependency_sources(args.dependency_source_bundle, app,
                                                       require_complete=args.require_dependency_sources)
        target = args.output_dir / args.dependency_source_bundle.name
        if target.resolve() != args.dependency_source_bundle.resolve():
            shutil.copy2(args.dependency_source_bundle, target)
        shutil.copy2(ROOT / 'packaging/windows-sources.lock.json', notices / 'dependency-sources.lock.json')
        source_text = ('Dependency sources and notices: ' + target.name + '\n'
                       'SHA256: ' + dependency_sources['sha256'] + '\n'
                       'Obtain this separate source archive alongside the matching application downloads.\n'
                       'Collection review complete: ' + str(dependency_sources['source_requirements_complete']) + '\n'
                       'See THIRD-PARTY-NOTICES/dependency-sources.lock.json for component licenses and remaining work.\n')
        (app / 'DEPENDENCY-SOURCES.txt').write_text(source_text, encoding='utf-8')
    manifest = {'schema_version': 1, 'application': 'Castwell', 'version': version,
                'platform': 'windows-x64', 'python': platform.python_version(),
                'inference': 'nvidia-cuda-only', 'speech_and_classifier_weights_bundled': False,
                'ad_removal_acceptance': 'not_verified_by_build',
                'production_go_live_confirmed': False,
                'llama_host_support': 'Generic x64 buffer registration library; all model layers must offload to NVIDIA CUDA',
                'builtin_vad_model_bundled': True,
                'builtin_vad_license': runtime_metadata.get('silero-vad-license'),
                'frozen_gpu_runtime_verified': runtime_verified,
                'full_distribution_smoke_required': True,
                'deduplicated_cuda_dlls': deduplicated_dlls,
                'normalized_compiler_runtime_dlls': normalized_vc_dlls,
                'inherited_path_excluded_from_freeze': True,
                'canonical_cuda_directory': '_internal/cuda',
                'dependency_metadata_exception': 'faster-whisper declares onnxruntime; onnxruntime-gpu supplies the GPU-only runtime namespace instead',
                'dependency_lock_sha256': digest(LOCK), 'native_inputs': inputs,
                'source_built_pyav': ({'name': pyav_build['name'], 'version': pyav_build['version'],
                                      'wheel': pyav_build['wheel'],
                                      'lock_sha256': pyav_build['lock_sha256'],
                                      'recipe_sha256': pyav_build['recipe_sha256'],
                                      'media_lock_sha256': pyav_build['media_lock_sha256'],
                                      'media_recipe_sha256': pyav_build['media_recipe_sha256']}
                                     if pyav_build else None),
                'source_tree_sha256': source_hash,
                'application_source_revision': source_revision,
                'application_source_url': 'https://github.com/mgelsinger/castwell/tree/' + source_revision,
                'third_party_corresponding_source_bundle_complete': bool(dependency_sources and dependency_sources['source_requirements_complete']),
                'dependency_source_bundle': dependency_sources,
                'python_dependencies': dependencies,
                'files': [{'path': path.relative_to(app).as_posix(), 'sha256': digest(path),
                           'bytes': path.stat().st_size} for path in sorted(app.rglob('*')) if path.is_file()]}
    (app / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    portable = args.output_dir / f'Castwell-{version}-Windows-x64-Portable.zip'
    archive_portable(app, portable)
    if portable.stat().st_size >= 2 * 1024**3:
        raise ValueError('Portable ZIP exceeds the GitHub 2 GiB release asset limit; reduce bundled duplicates before publishing')
    artifacts = [portable]
    if not args.skip_installer:
        run([args.inno_path, f'/DAppVersion={version}', f'/DSourceDir={app}',
             f'/DOutputDir={args.output_dir.resolve()}', ROOT / 'packaging' / 'castwell.iss'])
        artifacts.append(args.output_dir / f'Castwell-{version}-Windows-x64-Setup.exe')
    clean_source_revision(ROOT, expected=source_revision)
    manifest['artifacts'] = [{'name': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size} for path in artifacts]
    report = args.output_dir / f'Castwell-{version}-Windows-x64-manifest.json'
    report.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    source_artifacts = [args.output_dir / dependency_sources['archive']] if dependency_sources else []
    sums = ''.join(f'{digest(path)}  {path.name}\n' for path in artifacts + [report] + source_artifacts)
    (args.output_dir / 'SHA256SUMS.txt').write_text(sums, encoding='ascii')
    print(json.dumps({'version': version, 'artifacts': [str(path.resolve()) for path in artifacts],
                      'manifest': str(report.resolve()), 'installer_input': str(app)}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f'Windows build failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
