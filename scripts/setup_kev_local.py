#!/usr/bin/env python3
"""Install the optional Kev comparison runtime into .local, separate from Castwell.

Windows: py -3.12 scripts/setup_kev_local.py
Requires Git, Python 3.12 or 3.13, an NVIDIA CUDA-compatible GPU, and about 20 GB
of free disk for the isolated environment and public model weights. Downloads
are free. This helper never starts inference or uses a hosted model API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE_URL = "https://github.com/jaredpalmer/kev.git"
SOURCE_REVISION = "5e42a7a03f28134853dd3ff77461457e921e5ec1"
MODELS = [
    {
        "repository": "jaredpalmer/kev-4b",
        "revision": "6cfce5c2fa4b4bd64026336ab649c5ca78857d52",
        "files": {
            "adapter_model.safetensors": "90e817356246e7f18bfa7ca3d31794cd4fbeb3332a66a84cb51d9ceae925f2b2",
            "head.pt": "dd633435998ecc751ac538717a3742e32149500fabf7d7276287dbf0693f347c",
            "tokenizer.json": "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523",
        },
    },
    {
        "repository": "Qwen/Qwen3.5-4B-Base",
        "revision": "1001bb4d826a52d1f399e183466143f4da7b741b",
        "files": {
            "model.safetensors-00001-of-00002.safetensors": "df547074dce70532a0493e5433152bd17a65efb89088cfabc2e7e2371a93d712",
            "model.safetensors-00002-of-00002.safetensors": "590fbaac095dd31db886c322d9d2f7df47777966391acf306ddddc3e4e3a15ef",
            "tokenizer.json": "fe000e3ed39ed12b8d2481d527d44f93c65d37e87645d2dcc80d1bf9d50d2927",
        },
    },
]


def checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def download_models(directory: Path, *, offline: bool = False) -> None:
    os.environ["HF_HOME"] = str(directory / "models" / "huggingface")
    os.environ["HF_XET_CACHE"] = str(directory / "models" / "xet")
    os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
    from huggingface_hub import snapshot_download

    records = []
    for model in MODELS:
        path = Path(snapshot_download(
            model["repository"], revision=model["revision"], token=False,
            local_files_only=offline,
            allow_patterns=["adapter_config.json", "*.safetensors", "head.pt", "config.json",
                            "generation_config.json", "model.safetensors.index.json", "*token*.json",
                            "vocab.json", "merges.txt", "*.jinja"],
        ))
        files = []
        for filename, expected in model["files"].items():
            digest = hashlib.sha256()
            with (path / filename).open("rb") as handle:
                for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {path / filename}. No server was started.")
            files.append({"name": filename, "sha256": expected, "sha256_verified": True,
                          "size": (path / filename).stat().st_size,
                          "url": f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{filename}"})
        records.append({"repository": model["repository"], "revision": model["revision"],
                        "path": str(path), "files": files})
        print(f"Verified {model['repository']} at {model['revision']}", flush=True)
    (directory / "kev-model-provenance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1] / ".local")
    parser.add_argument("--download-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify-only", action="store_true", help="Verify cached model hashes offline; do not install")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.download_only or args.verify_only:
        download_models(directory, offline=args.verify_only)
        return
    if os.name != "nt" or sys.version_info[:2] not in {(3, 12), (3, 13)}:
        parser.error("This tested setup requires Windows and Python 3.12 or 3.13. Use py -3.12.")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "kev"
    if not source.exists():
        checked(["git", "init", str(source)])
        checked(["git", "-C", str(source), "remote", "add", "origin", SOURCE_URL])
        checked(["git", "-C", str(source), "fetch", "--depth", "1", "origin", SOURCE_REVISION])
        checked(["git", "-C", str(source), "checkout", "--detach", SOURCE_REVISION])
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    changed = subprocess.run(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--"]).returncode
    if revision != SOURCE_REVISION or changed:
        parser.error(f"Existing {source} must have unchanged tracked files at {SOURCE_REVISION}; existing work was preserved.")
    # Package the pinned Git tree, never local untracked files. Building from
    # this archive also keeps setuptools build products out of the checkout.
    archive = directory / "downloads" / f"kev-{SOURCE_REVISION}.tar"
    archive.parent.mkdir(parents=True, exist_ok=True)
    checked(["git", "-C", str(source), "archive", "--format=tar", "--output", str(archive), SOURCE_REVISION])
    environment = directory / "kev-venv"
    python = environment / "Scripts" / "python.exe"
    if not python.exists():
        checked([sys.executable, "-m", "venv", str(environment)])
    checked([str(python), "-m", "ensurepip", "--upgrade"])
    checked([str(python), "-m", "pip", "install", "torch==2.8.0", "--index-url", "https://download.pytorch.org/whl/cu128"])
    checked([str(python), "-m", "pip", "install", "-r", str(Path(__file__).with_name("kev_requirements.txt"))])
    checked([str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", str(archive)])
    checked([str(python), str(Path(__file__).resolve()), "--download-only", "--directory", str(directory)])
    print("Ready. Stop other GPU model servers, then run scripts/start_kev_local.ps1.")


if __name__ == "__main__":
    main()
