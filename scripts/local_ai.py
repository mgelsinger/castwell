#!/usr/bin/env python3
"""Run an optional private classifier compatible with Castwell.

Choose an existing GGUF with ``--model`` or download a pinned official Qwen 2.5
model with ``--download`` (requires huggingface-hub). Downloads are verified
against the publisher's SHA256 before use. The server listens only on loopback
and needs no API key. The default Python backend uses CPU inference and requires
the ``local-ai`` extra. Use ``--backend native`` and ``--server-binary`` for an
existing standalone llama.cpp server with GPU offloading and no Python inference
dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys

MODEL_PROFILES = {
    "3b": {
        "repository": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "revision": "7dabda4d13d513e3e842b20f0d435c732f172cbe",
        "directory": "qwen2.5-3b-instruct",
        "size_gb": 2.1,
        "files": [("qwen2.5-3b-instruct-q4_k_m.gguf",
                   "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d")],
    },
    "7b": {
        "repository": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "revision": "bb5d59e06d9551d752d08b292a50eb208b07ab1f",
        "directory": "qwen2.5-7b-instruct",
        "size_gb": 4.7,
        "files": [
            ("qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
             "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db"),
            ("qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
             "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a"),
        ],
    },
}


def verify_model(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"Model checksum verification failed: {path}. The model was not started.")


def download_model(cache_dir: Path, size: str = "7b") -> Path:
    # Native Xet downloads have an auxiliary cache independent of local_dir.
    # Keep it writable on cloud machines with a read-only home directory.
    os.environ.setdefault("HF_XET_CACHE", str(cache_dir.expanduser().resolve() / "xet"))
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Model downloads require huggingface-hub: pip install 'huggingface-hub>=0.34,<2'") from exc
    profile = MODEL_PROFILES[size]
    print(f"Downloading/verifying official Qwen 2.5 {size.upper()} (~{profile['size_gb']} GB).", file=sys.stderr)
    paths = []
    for filename, checksum in profile["files"]:
        path = Path(hf_hub_download(
            repo_id=profile["repository"], filename=filename, revision=profile["revision"],
            local_dir=str(cache_dir / profile["directory"]),
        ))
        verify_model(path, checksum)
        paths.append(path)
    # llama.cpp automatically loads the adjacent second shard of the 7B model.
    return paths[0]


def server_command(model: Path, *, port: int = 8081, threads: int = 4,
                   context: int = 8192, backend: str | None = None,
                   server_binary: Path | None = None, gpu_layers: int | None = None) -> list[str]:
    backend = backend or ("native" if server_binary else "python")
    if backend not in {"python", "native"}:
        raise ValueError("Backend must be python or native.")
    if not model.is_file():
        raise ValueError(f"Model file does not exist: {model}")
    if not 1 <= port <= 65535 or threads < 1 or context < 2048:
        raise ValueError("Port must be 1-65535, threads positive, and context at least 2048.")
    if gpu_layers is not None and gpu_layers < -1:
        raise ValueError("GPU layers must be -1 for all layers, zero for CPU, or positive.")
    if backend == "native":
        binary = server_binary or shutil.which("llama-server")
        binary = Path(binary).expanduser() if binary else None
        if not binary or not binary.is_file():
            raise ValueError("Native llama-server was not found. Pass --server-binary /path/to/llama-server.")
        layers = "all" if gpu_layers in (None, -1) else str(gpu_layers)
        return [
            str(binary.resolve()), "--model", str(model.resolve()),
            "--alias", "castwell-local", "--host", "127.0.0.1", "--port", str(port),
            "--threads", str(threads), "--threads-batch", str(threads),
            "--ctx-size", str(context), "--n-gpu-layers", layers, "--parallel", "1",
        ]
    if server_binary is not None:
        raise ValueError("--server-binary requires the native backend.")
    return [
        sys.executable, "-m", "llama_cpp.server", "--model", str(model.resolve()),
        "--model_alias", "castwell-local", "--host", "127.0.0.1", "--port", str(port),
        "--n_threads", str(threads), "--n_threads_batch", str(threads),
        "--n_ctx", str(context), "--n_gpu_layers", str(gpu_layers if gpu_layers is not None else 0),
        "--chat_format", "chatml",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", type=Path, help="Existing Qwen-compatible GGUF file")
    source.add_argument("--download", action="store_true", help="Download and verify a pinned official Qwen model")
    parser.add_argument("--size", choices=sorted(MODEL_PROFILES), default="7b",
                        help="Downloaded model size (default: 7b; 3b uses less memory and CPU)")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(os.environ.get("CASTWELL_MODEL_CACHE", "~/.cache/castwell")).expanduser())
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--backend", choices=("python", "native"),
                        help="Default: python; supplying --server-binary selects native")
    parser.add_argument("--server-binary", type=Path, help="Existing native llama-server executable")
    parser.add_argument("--gpu-layers", type=int,
                        help="Layers to offload: -1 for all, 0 for CPU; default native=all, python=0")
    args = parser.parse_args()
    try:
        model = download_model(args.cache_dir, args.size) if args.download else args.model.expanduser()
        backend = args.backend or ("native" if args.server_binary else "python")
        command = server_command(model, port=args.port, threads=args.threads, context=args.context,
                                 backend=backend, server_binary=args.server_binary,
                                 gpu_layers=args.gpu_layers)
        if backend == "python":
            try:
                import llama_cpp.server  # noqa: F401
            except ImportError as exc:
                raise RuntimeError("Install the local-ai extra first: pip install '.[local-ai]'") from exc
        print(f"Set CASTWELL_AI_BASE_URL=http://127.0.0.1:{args.port}/v1 and "
              "CASTWELL_AI_MODEL=castwell-local when starting Castwell.", file=sys.stderr)
        # Replace the launcher so termination reaches the server directly.
        os.execv(command[0], command)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
