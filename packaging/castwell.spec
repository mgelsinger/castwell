# -*- mode: python ; coding: utf-8 -*-
"""Explicit application assets and runtime inputs. No library or model cache."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(os.environ['CASTWELL_BUILD_ROOT'])
datas = [(str(root / 'castwell' / 'static'), 'castwell/static')]
binaries = []
hiddenimports = ['castwell.app', 'castwell.gpu', 'uvicorn.logging', 'uvicorn.loops.auto',
                 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto',
                 'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto',
                 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'tkinter']
for package in ('faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
hiddenimports += collect_submodules('huggingface_hub', filter=lambda name: '.tests' not in name)
for distribution in ('castwell', 'faster-whisper', 'ctranslate2', 'av', 'onnxruntime-gpu',
                     'tokenizers', 'huggingface-hub', 'fastapi', 'uvicorn', 'requests',
                     'feedparser', 'tqdm', 'python-multipart'):
    datas += copy_metadata(distribution)
for variable, destination, executable_names in (
    ('CASTWELL_FFMPEG_DIR', 'bin', {'ffmpeg.exe', 'ffprobe.exe'}),
    ('CASTWELL_LLAMA_DIR', 'llama', {'llama-server.exe'}),
    ('CASTWELL_CUDA_DIR', 'cuda', set()),
):
    directory = Path(os.environ[variable])
    for path in sorted(directory.rglob('*')):
        # Stock CUDA llama.cpp requires the generic CPU buffer registration
        # library even when every model layer executes on CUDA. Optimized CPU
        # variants are omitted; desktop readiness prohibits model CPU fallback.
        cpu_allowed = not path.name.lower().startswith('ggml-cpu-') or path.name.lower() == 'ggml-cpu-x64.dll'
        if path.is_file() and cpu_allowed and (path.name.lower() in executable_names or path.suffix.lower() == '.dll'):
            # Native libraries stay beside their tool executables for DLL discovery.
            relative_parent = path.relative_to(directory).parent.as_posix()
            target = destination if relative_parent == '.' else destination + '/' + relative_parent
            binaries.append((str(path), target))

a = Analysis(
    [str(root / 'scripts' / 'desktop_entry.py')], pathex=[str(root)],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['torch', 'tensorflow', 'transformers', 'playwright', 'pytest',
              'llama_cpp', 'PIL', 'IPython', 'matplotlib', 'pandas', 'scipy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='Castwell', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, icon=os.environ['CASTWELL_BUILD_ICON'],
    version=os.environ['CASTWELL_BUILD_VERSION_FILE'], contents_directory='_internal',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Castwell')
