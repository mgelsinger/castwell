"""Verify source-built media staging without downloads or compilation."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_fetch', ROOT / 'scripts/fetch_windows_dependencies.py')
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


@pytest.fixture
def media_build(tmp_path):
    root, media = tmp_path / 'repo', tmp_path / 'media'
    (root / 'packaging').mkdir(parents=True)
    (root / 'scripts').mkdir()
    lock = root / 'packaging/windows-media.lock.json'
    lock.write_text(json.dumps({'profile': 'castwell-audio1', 'ffmpeg_version': '8.1.2'}))
    recipe = root / 'scripts/build_windows_media.py'
    recipe.write_text('# pinned recipe\n')
    data = {'schema_version': 1, 'profile': 'castwell-audio1', 'lock_sha256': fetch.file_sha256(lock),
            'recipe_sha256': fetch.file_sha256(recipe), 'ffmpeg_dir': str(media / 'prefix/bin'),
            'shared_prefix': str(media / 'shared-prefix'), 'files': [],
            'shared_development_files': [], 'source_license_files': []}

    def add(group, relative, **extra):
        path = media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
        data[group].append(dict(relative_path=relative, sha256=fetch.file_sha256(path),
                                bytes=path.stat().st_size, **extra))

    for name in ('ffmpeg', 'ffprobe'):
        add('files', f'prefix/bin/{name}.exe', kind='static')
    for name, major in {'avcodec': 62, 'avdevice': 62, 'avfilter': 11, 'avformat': 62,
                        'avutil': 60, 'swresample': 6, 'swscale': 9}.items():
        add('files', f'shared-prefix/bin/{name}-{major}.dll', kind='shared')
        add('shared_development_files', f'shared-prefix/lib/{name}.lib')
        add('shared_development_files', f'shared-prefix/include/lib{name}/{name}.h')
    for relative in ('sources/ffmpeg-8.1.2/COPYING.LGPLv2.1', 'sources/lame-3.100/COPYING',
                     'sources/libogg-1.3.6/COPYING', 'sources/libvorbis-1.3.7/COPYING',
                     'sources/opus-1.6/COPYING', 'toolchain/w64devkit/COPYING.MinGW-w64-runtime.txt'):
        add('source_license_files', relative)
    return root, media, data


def verify(fixture):
    root, media, data = fixture
    (media / 'build-manifest.json').write_text(json.dumps(data))
    return fetch.verify_media_build(media, root)


def test_complete_receipt_and_download_cache_require_exact_bytes(media_build, monkeypatch):
    assert verify(media_build) == media_build[2]
    path = media_build[1] / 'prefix/bin/ffmpeg.exe'
    monkeypatch.setattr(fetch.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('Cache unexpectedly fetched'))
    assert fetch.download('https://example.invalid/file', path, fetch.file_sha256(path)) == fetch.file_sha256(path)


@pytest.mark.parametrize('omission', ['tool', 'library', 'header', 'license'])
def test_missing_required_receipt_entries_fail_even_when_files_exist(media_build, omission):
    group = {'tool': 'files', 'library': 'shared_development_files',
             'header': 'shared_development_files', 'license': 'source_license_files'}[omission]
    index = 1 if omission == 'header' else 0
    media_build[2][group].pop(index)
    with pytest.raises(RuntimeError, match='omits|cover'):
        verify(media_build)


@pytest.mark.parametrize('mutation', ['changed', 'unrecorded', 'escape', 'duplicate', 'different_recipe'])
def test_mutated_or_unsafe_media_inputs_fail(media_build, mutation):
    root, media, data = media_build
    if mutation == 'changed':
        (media / data['shared_development_files'][1]['relative_path']).write_text('changed header')
    elif mutation == 'unrecorded':
        (media / 'shared-prefix/include/libavcodec/unrecorded.h').write_text('unrecorded header')
    elif mutation == 'escape':
        data['files'][0]['relative_path'] = '../outside.exe'
    elif mutation == 'duplicate':
        data['files'].append(dict(data['files'][0]))
    else:
        (root / 'scripts/build_windows_media.py').write_text('different recipe')
    with pytest.raises(RuntimeError, match='changed|cover|invalid|recipe'):
        verify(media_build)
