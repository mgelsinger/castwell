#!/usr/bin/env python3
"""Evaluate frozen private real-recording annotations against a local classifier."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from castwell.recording_evaluation import evaluate_recordings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("verified-ai", "kev"), default="verified-ai")
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Private full cuts/evidence checkpoint; keep outside published files")
    parser.add_argument("--summary-output", type=Path, required=True, help="Public-safe numeric summary without transcripts or model evidence")
    parser.add_argument("--base-url", help="Loopback server; defaults to http://127.0.0.1:8081/v1 for verified-ai or http://127.0.0.1:8083 for Kev")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-label", required=True)
    parser.add_argument("--model-file", type=Path, action="append", required=True, help="Actual local model artifacts for SHA256 provenance; repeat for shards, Kev heads and base weights")
    parser.add_argument("--candidate-config", type=Path, help="Frozen configuration; matching source hashes are checked")
    parser.add_argument("--resume", action="store_true", help="Continue unfinished clips only if all inputs and candidate hashes match")
    args = parser.parse_args(argv)
    try:
        result = evaluate_recordings(args.reference, output=args.output, summary_output=args.summary_output,
            backend=args.backend,
            base_url=args.base_url or ("http://127.0.0.1:8083" if args.backend == "kev" else "http://127.0.0.1:8081/v1"),
            model=args.model, model_label=args.model_label, model_files=args.model_file,
            candidate_config=args.candidate_config, resume=args.resume,
            on_progress=lambda clip, message: print(f"{clip}: {message}", file=sys.stderr, flush=True))
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    summary = result["summary"]
    print(f"Completed {summary['successful_clips']} clips; {summary['failed_clips']} failed. Summary: {args.summary_output}")
    return 1 if summary["failed_clips"] or summary["unfinished_clips"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
