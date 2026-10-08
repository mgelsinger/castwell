#!/usr/bin/env python3
"""Install the optional Kev comparison runtime into .local, separate from Castwell.

Windows: py -3.12 scripts/setup_kev_local.py
Requires Git, Python 3.12 or 3.13, an NVIDIA CUDA-compatible GPU, and about 20 GB
of free disk for 4B, or about 35 GB for 9B, including the isolated environment.
Use --size 9b to prepare the larger comparison model. Downloads
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
            "adapter_config.json": "8a05dfd6c5e7f61a62e6db5d8de8093a8dbbd105aa7ee7b5e7b73f6249f45617",
            "adapter_model.safetensors": "90e817356246e7f18bfa7ca3d31794cd4fbeb3332a66a84cb51d9ceae925f2b2",
            "added_tokens.json": "c0284b582e14987fbd3d5a2cb2bd139084371ed9acbae488829a1c900833c680",
            "head.pt": "dd633435998ecc751ac538717a3742e32149500fabf7d7276287dbf0693f347c",
            "merges.txt": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
            "special_tokens_map.json": "6676f091c8bc4d1b50146427cfde92073402866b87b6e39223227931b70083e9",
            "tokenizer.json": "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523",
            "tokenizer_config.json": "8671bed7c852ce9e661be94f179a7b4ffd091c2a65aea0363e5501c20318ee45",
            "vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
        },
    },
    {
        "repository": "Qwen/Qwen3.5-4B-Base",
        "revision": "1001bb4d826a52d1f399e183466143f4da7b741b",
        "files": {
            "config.json": "ddc63e1c717afa86c865bb5e01313d89d72bb53b97ad4a8a03ba8510c0621670",
            "merges.txt": "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d",
            "model.safetensors-00001-of-00002.safetensors": "df547074dce70532a0493e5433152bd17a65efb89088cfabc2e7e2371a93d712",
            "model.safetensors-00002-of-00002.safetensors": "590fbaac095dd31db886c322d9d2f7df47777966391acf306ddddc3e4e3a15ef",
            "model.safetensors.index.json": "eae340074abb0a5f31a6621f7ae8e8248a7c1790df04a722c4e4b70c2a6d1dbb",
            "tokenizer.json": "fe000e3ed39ed12b8d2481d527d44f93c65d37e87645d2dcc80d1bf9d50d2927",
            "tokenizer_config.json": "3891e840d7dc5fca0af33d3a25083a735e36fe06214e3f707024820cb6b9f89c",
            "vocab.json": "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003",
        },
    },
]


MODELS_9B = [{'repository': 'jaredpalmer/kev-9b',
  'revision': 'db029f08b290afd9fee4aa4bbcd9ae48602d1eb0',
  'files': {'adapter_config.json': '2b2563a94d1bc1065653bec4d296f5a1a6287ab895b14d23e5997333503ecd97',
            'adapter_model.safetensors': '2b2a70cf4ef4440b6c22899e1f72c2f8ea5c6f65b19aa344539b4b8971d1f13d',
            'chat_template.jinja': 'a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715',
            'head.pt': '8e1dab2c8e3664f6fee843e0257d57946c25e0210c61901ea77e71761f4244e1',
            'tokenizer.json': '06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523',
            'tokenizer_config.json': '8671bed7c852ce9e661be94f179a7b4ffd091c2a65aea0363e5501c20318ee45'}},
 {'repository': 'Qwen/Qwen3.5-9B-Base',
  'revision': '68c46c4b3498877f3ef123c856ecfde50c39f404',
  'files': {'config.json': 'd0883072e01861ed0b2d47be3c16c36a8e81c224c7ffaa310c6558fb3f932b05',
            'merges.txt': 'a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d',
            'model.safetensors-00001-of-00004.safetensors': '862bf7bba8a50145d19d0ae463931fae515284024736592a73a336bc4dfa54ee',
            'model.safetensors-00002-of-00004.safetensors': 'bace8e115e11ca93c22f0352a60d2fb0c76ac6d7d1c2993c143b7ad2b6c8868c',
            'model.safetensors-00003-of-00004.safetensors': '63a021ac0011cbfc66166e77103327a8b45dee95832e36551f6b4c3337448959',
            'model.safetensors-00004-of-00004.safetensors': '1a643bbed669266917b5058b5d3f660c03233599249ff7d8fd083decfe662ae0',
            'model.safetensors.index.json': '026b9d9fe03f19fd065f2a2f56a332c67640878106c0ca6be2f60c655ed5a8c1',
            'tokenizer.json': 'fe000e3ed39ed12b8d2481d527d44f93c65d37e87645d2dcc80d1bf9d50d2927',
            'tokenizer_config.json': '3891e840d7dc5fca0af33d3a25083a735e36fe06214e3f707024820cb6b9f89c',
            'vocab.json': 'ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003'}}]

MODEL_SETS = {"4b": MODELS, "9b": MODELS_9B}


def checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def download_models(directory: Path, *, offline: bool = False, size: str = "4b") -> None:
    models = MODEL_SETS[size]
    os.environ["HF_HOME"] = str(directory / "models" / "huggingface")
    os.environ["HF_XET_CACHE"] = str(directory / "models" / "xet")
    os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
    from huggingface_hub import snapshot_download

    records = []
    for model in models:
        path = Path(snapshot_download(
            model["repository"], revision=model["revision"], token=False,
            cache_dir=str(directory / "models" / "huggingface" / "hub"),
            local_files_only=offline,
            allow_patterns=list(model["files"]),
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
    manifest = directory / ("kev-model-provenance.json" if size == "4b" else "kev9-model-provenance.json")
    if offline:
        # Old manifests may list fewer loader files. Verify every current pin
        # above, then bind the recorded paths without rewriting prior evidence.
        if manifest.exists():
            recorded = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(recorded, list) or len(recorded) != len(records):
                raise ValueError("Cached Kev provenance has an unexpected model list.")
            for actual, expected in zip(recorded, records):
                if (not isinstance(actual, dict)
                        or actual.get("repository") != expected["repository"]
                        or actual.get("revision") != expected["revision"]
                        or not isinstance(actual.get("path"), str)
                        or Path(actual["path"]).resolve() != Path(expected["path"]).resolve()):
                    raise ValueError("Cached Kev provenance does not match the verified model paths.")
        return
    manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1] / ".local")
    parser.add_argument("--size", choices=tuple(MODEL_SETS), default="4b", help="Pinned optional model size; preserves the other cached model")
    parser.add_argument("--download-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify-only", action="store_true", help="Verify cached model hashes and recorded paths offline; do not install or rewrite provenance")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.download_only or args.verify_only:
        download_models(directory, offline=args.verify_only, size=args.size)
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
    checked([str(python), str(Path(__file__).resolve()), "--download-only", "--directory", str(directory), "--size", args.size])
    print(f"Ready. Stop other GPU model servers, then run scripts/start_kev_local.ps1 -Size {args.size}.")


if __name__ == "__main__":
    main()
