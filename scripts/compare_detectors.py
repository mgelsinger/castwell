#!/usr/bin/env python3
"""Compare detectors without editing audio; defaults to offline heuristic only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# Allow an uninstalled source checkout to run this script directly.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from castwell.evaluation import DEFAULT_BASE_URL, DEFAULT_MODEL, evaluate_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="append", choices=("heuristic", "local-ai", "jev"),
                        help="Repeat to compare backends; default: heuristic only")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Local OpenAI-compatible API base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-label", help="Exact local model name, quantization, and version for provenance")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests" / "fixtures" / "ad_read_challenge.json")
    parser.add_argument("--split", choices=("dev", "eval", "all"), default="eval")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-remote", action="store_true", help="Explicitly allow a non-loopback local-ai endpoint")
    parser.add_argument("--allow-paid-api", action="store_true", help="Explicitly allow paid Jev requests; disabled by default")
    parser.add_argument("--jev-api-key-env", help="Name of an environment variable explicitly supplying a Jev API key")
    parser.add_argument("--jev-base-url", default="https://api.typesafe.ai")
    parser.add_argument("--jev-model", default="jev-1.13.0")
    args = parser.parse_args(argv)
    def progress(backend, record):
        print(f"{backend}: {record['id']}: {record['status']} ({record['seconds']:.2f}s)", file=sys.stderr, flush=True)
    try:
        result = evaluate_file(args.fixtures, backends=args.backend, base_url=args.base_url, model=args.model,
                               model_label=args.model_label, split=args.split, allow_remote=args.allow_remote,
                               allow_paid_api=args.allow_paid_api, jev_api_key=os.environ.get(args.jev_api_key_env) if args.jev_api_key_env else None,
                               jev_base_url=args.jev_base_url, jev_model=args.jev_model, on_case=progress)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    serialized = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 1 if any(backend["summary"]["failed_cases"] for backend in result["backends"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
