"""Fetch hash-pinned public build dependencies, without installing anything."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download(url: str, target: Path, expected: str | None = None) -> str:
    if target.exists() and expected and file_sha256(target) == expected:
        return expected
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "Castwell-Windows-build/0.5.1"})
    with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    digest = file_sha256(partial)
    if expected and digest != expected:
        partial.unlink()
        raise RuntimeError(f"Checksum mismatch for {target.name}")
    partial.replace(target)
    return digest


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = (destination / member.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise RuntimeError("Archive contains a path outside its destination")
        source.extractall(destination)


def write_native_notices(notices: Path, metadata: dict) -> None:
    (notices / "THIRD-PARTY-NOTICES.txt").write_text(
        "Castwell Windows distributions\n\n"
        "Python and Python dependencies retain their bundled licenses.\n"
        "FFmpeg and llama.cpp are separate third-party executables; see their pinned build provenance.\n"
        "Speech and classifier weights are not included and retain their publishers' terms.\n"
        "The built-in Silero VAD model is included under the Silero Team MIT license.\n\n"
        + "\n\n".join(f"{name}\nLicense: {item['license']}\nArtifact: {item['url']}\n"
                        f"SHA256: {item['sha256']}\nSource: {item['source']}" for name, item in metadata.items())
        + "\n\nFFmpeg and PyAV are built from the pinned Castwell audio source recipes.\n"
          "See the matching dependency-source archive for source, patches and rebuild instructions.\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_media_build(media: Path, root: Path = ROOT) -> dict:
    """Check every consumed file, including required entries omitted from a receipt."""
    media = media.resolve()
    lock_path = root / 'packaging/windows-media.lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    data = json.loads((media / 'build-manifest.json').read_text(encoding='utf-8'))
    if (data.get('schema_version') != 1 or data.get('profile') != lock['profile']
            or data.get('lock_sha256') != file_sha256(lock_path)
            or data.get('recipe_sha256') != file_sha256(root / 'scripts/build_windows_media.py')):
        raise RuntimeError('Media build receipt does not match the pinned recipe')
    for field, relative in (('ffmpeg_dir', 'prefix/bin'), ('shared_prefix', 'shared-prefix')):
        if Path(data.get(field, '')).resolve() != (media / relative).resolve():
            raise RuntimeError('Media build receipt names a different output directory')
    groups = {}
    seen = set()
    for group in ('files', 'shared_development_files', 'source_license_files'):
        rows = data.get(group)
        if not isinstance(rows, list):
            raise RuntimeError('Media build receipt omits a required file inventory')
        groups[group] = {}
        for item in rows:
            relative = item.get('relative_path', '')
            if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
                    or relative.startswith('/') or any(part in {'', '.', '..'} for part in relative.split('/'))
                    or relative.casefold() in seen):
                raise RuntimeError('Media build receipt has an invalid or duplicate file path')
            seen.add(relative.casefold())
            path = (media / relative).resolve()
            if (not path.is_relative_to(media) or not path.is_file()
                    or path.stat().st_size != item.get('bytes') or file_sha256(path) != item.get('sha256')):
                raise RuntimeError('Media build receipt file is missing or changed')
            groups[group][relative] = item
    binaries = groups['files']
    for name in ('ffmpeg', 'ffprobe'):
        if binaries.get(f'prefix/bin/{name}.exe', {}).get('kind') != 'static':
            raise RuntimeError('Media build receipt omits a required static audio tool')
    libraries = {'avcodec': 62, 'avdevice': 62, 'avfilter': 11, 'avformat': 62,
                 'avutil': 60, 'swresample': 6, 'swscale': 9}
    development = groups['shared_development_files']
    for name, major in libraries.items():
        if (binaries.get(f'shared-prefix/bin/{name}-{major}.dll', {}).get('kind') != 'shared'
                or f'shared-prefix/lib/{name}.lib' not in development
                or not any(path.startswith(f'shared-prefix/include/lib{name}/') for path in development)):
            raise RuntimeError('Media build receipt omits required shared library inputs')
    actual_development = {path.relative_to(media).as_posix()
                          for pattern in ('include/**/*.h', 'lib/*.lib')
                          for path in (media / 'shared-prefix').glob(pattern) if path.is_file()}
    if set(development) != actual_development:
        raise RuntimeError('Media build receipt does not cover all shared development inputs')
    required_licenses = {f'sources/ffmpeg-{lock["ffmpeg_version"]}/COPYING.LGPLv2.1',
                        'sources/lame-3.100/COPYING', 'sources/libogg-1.3.6/COPYING',
                        'sources/libvorbis-1.3.7/COPYING', 'sources/opus-1.6/COPYING',
                        'toolchain/w64devkit/COPYING.MinGW-w64-runtime.txt'}
    if not required_licenses <= groups['source_license_files'].keys():
        raise RuntimeError('Media build receipt omits required source licenses')
    return data


def prepare_source_builds(output: Path, media_build: Path | None, pyav_build: Path | None) -> tuple[Path, Path, dict]:
    """Execute pinned recipes, or validate receipts from our prior local builds."""
    output.mkdir(parents=True, exist_ok=True)
    media = (media_build or output / 'media-build').resolve()
    if media_build is None:
        log = output / 'media-build.log'
        with log.open('wb') as stream:
            subprocess.run([sys.executable, str(ROOT / 'scripts/build_windows_media.py'), '--output', str(media)],
                           cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
    media_receipt = media / 'build-manifest.json'
    data = verify_media_build(media)
    ffmpeg = (media / 'prefix/bin').resolve()
    shared = (media / 'shared-prefix').resolve()
    if pyav_build is None:
        pyav_output = output / 'pyav-build'
        log = output / 'pyav-build.log'
        with log.open('wb') as stream:
            subprocess.run([sys.executable, str(ROOT / 'scripts/build_windows_pyav.py'),
                            '--ffmpeg-prefix', str(shared), '--media-manifest', str(media_receipt),
                            '--output', str(pyav_output)], cwd=ROOT,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        pyav_build = pyav_output / 'build-manifest.json'
    # Use the same source/wheel checks as the application freezer.
    import importlib.util
    spec = importlib.util.spec_from_file_location('castwell_source_build_checks', ROOT / 'scripts/build_windows.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    pyav, _ = checks.verify_pyav_build(pyav_build)
    if pyav.get('media_manifest_sha256') != file_sha256(media_receipt):
        raise RuntimeError('PyAV was built against a different shared media receipt')
    notices = output / 'notices'
    notices.mkdir(parents=True, exist_ok=True)
    for item in data['source_license_files']:
        source = media / item['relative_path']
        name = 'controlled-media-' + source.parent.name + '-' + source.name
        shutil.copy2(source, notices / name)
    return ffmpeg, pyav_build.resolve(), dict(data, receipt_sha256=file_sha256(media_receipt))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "windows-vendor")
    parser.add_argument('--media-build', type=Path, help='Reuse a verified local pinned media build directory')
    parser.add_argument('--pyav-build-manifest', type=Path, help='Reuse its verified local PyAV build receipt')
    args = parser.parse_args()
    output = args.output.resolve()
    metadata = json.loads((ROOT / "packaging" / "windows-runtime.json").read_text())
    notices = output / "notices"
    notices.mkdir(parents=True, exist_ok=True)
    for name, item in metadata.items():
        if item.get('kind') == 'source-build':
            continue
        if item.get("kind") == "notice":
            target = notices / item["filename"]
            if item.get("repository_path"):
                source = (ROOT / item['repository_path']).resolve()
                if not source.is_relative_to((ROOT / 'packaging' / 'notices').resolve()) or not source.is_file():
                    raise RuntimeError('Invalid packaged notice path')
                if file_sha256(source) != item['sha256']:
                    raise RuntimeError('Packaged notice checksum mismatch: ' + source.name)
                shutil.copy2(source, target)
            else:
                download(item["url"], target, item["sha256"])
            item["download_path"] = str(target)
            continue
        archive = output / "downloads" / item["url"].rsplit("/", 1)[1]
        download(item["url"], archive, item["sha256"])
        if archive.suffix in {'.zip', '.vsix'}:
            extract(archive, output / name)
        item["download_path"] = str(archive)
    licenses = {
        "FFmpeg-LGPL-2.1.txt": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1.2/COPYING.LGPLv2.1",
        "llama.cpp-MIT.txt": "https://raw.githubusercontent.com/ggml-org/llama.cpp/b11146/LICENSE",
        "Inno-Setup.txt": "https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/license.txt",
        "CTranslate2-MIT.txt": "https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.2/LICENSE",
        "FlatBuffers-Apache-2.0.txt": "https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE",
        "Tokenizers-Apache-2.0.txt": "https://raw.githubusercontent.com/huggingface/tokenizers/v0.23.2/LICENSE",
    }
    for name, url in licenses.items():
        download(url, notices / name)
    for name in metadata:
        if name == "inno" or metadata[name].get("kind") in {'notice', 'source-build'}:
            continue
        for source in (output / name).rglob("*"):
            if source.is_file() and ("license" in source.name.lower() or source.name.lower().startswith("readme")):
                shutil.copy2(source, notices / f"{name}-{source.name}")
    write_native_notices(notices, metadata)
    ffmpeg_directory, pyav_receipt, media_receipt = prepare_source_builds(output, args.media_build, args.pyav_build_manifest)
    llama_directory = next((output / "llama").rglob("llama-server.exe")).parent
    cuda_directory = output / "cuda-runtime"
    cuda_directory.mkdir(parents=True, exist_ok=True)
    for name in ("cuda", "cudnn", "cufft", "curand", "nvrtc"):
        for binary in (output / name).rglob("*.dll"):
            shutil.copy2(binary, cuda_directory / binary.name)
    vc_directory = output / 'vc-runtime-bin'
    vc_directory.mkdir(parents=True, exist_ok=True)
    for item in metadata['vc-runtime']['binaries']:
        source = (output / 'vc-runtime' / item['archive_member']).resolve()
        if not source.is_relative_to((output / 'vc-runtime').resolve()) or file_sha256(source) != item['sha256']:
            raise RuntimeError('Microsoft runtime archive member differs from its pin')
        shutil.copy2(source, vc_directory / item['name'])
    download("https://developer.download.nvidia.com/compute/cuda/redist/libcublas/LICENSE.txt", notices / "NVIDIA-CUDA.txt")
    result = {"ffmpeg_dir": str(ffmpeg_directory), "llama_dir": str(llama_directory), "cuda_dir": str(cuda_directory),
              'vc_runtime_dir': str(vc_directory), 'pyav_build_manifest': str(pyav_receipt),
              'media_build_manifest_sha256': media_receipt['receipt_sha256'],
              "notices_dir": str(notices), "inno_installer": metadata["inno"]["download_path"],
              "dependencies": metadata}
    (output / "vendor-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
