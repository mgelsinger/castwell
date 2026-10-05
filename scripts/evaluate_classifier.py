#!/usr/bin/env python3
"""Run a small, reproducible semantic sanity check against a live classifier.

This is six handcrafted cases, not a representative accuracy benchmark. It
downloads no models and sends only the included synthetic text to the selected
endpoint. API authentication uses CASTWELL_AI_KEY without writing it to results.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from castwell.processing import detect_ads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("CASTWELL_AI_BASE_URL", "http://127.0.0.1:8081/v1"))
    parser.add_argument("--model", default=os.environ.get("CASTWELL_AI_MODEL", "castwell-local"))
    parser.add_argument("--model-label", help="Human-readable model/version name for the result record")
    parser.add_argument("--output", type=Path, help="Also save the JSON result to this path")
    parser.add_argument("--fixtures", type=Path,
                        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "classifier_cases.json")
    args = parser.parse_args()
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    result = {
        "scope": "Six handcrafted semantic sanity checks; not a general accuracy benchmark.",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "model_label": args.model_label or args.model,
        "castwell_version": importlib.metadata.version("castwell"),
        "cases": [],
    }
    for fixture in fixtures:
        # Fixed five-second slots make expected segment selection reproducible;
        # these timestamps are not measurements of recorded speech.
        transcript = {
            "language": "en", "duration": len(fixture["texts"]) * 5,
            "segments": [{"id": index, "start": index * 5, "end": (index + 1) * 5, "text": text}
                         for index, text in enumerate(fixture["texts"])],
        }
        began = time.monotonic()
        record = {"name": fixture["name"], "expected_ids": fixture["expected_ids"]}
        try:
            cuts = detect_ads(transcript, "ai", config={
                "ai_base_url": args.base_url, "ai_model": args.model, "review_only": False,
            })
            predicted = [segment["id"] for segment in transcript["segments"] if any(
                cut["source"] == "ai" and cut["start"] <= segment["start"] and cut["end"] >= segment["end"]
                for cut in cuts)]
            approved = [segment["id"] for segment in transcript["segments"] if any(
                cut["approved"] and cut["start"] <= segment["start"] and cut["end"] >= segment["end"]
                for cut in cuts)]
            record.update(predicted_ids=predicted, approved_ids=approved, cuts=cuts,
                          passed=(predicted == fixture["expected_ids"] and approved == fixture["expected_ids"]))
        except Exception as exc:
            # ProcessingError deliberately avoids endpoint credentials/raw API
            # responses. Keep unknown exceptions limited to the type name.
            from castwell.processing import ProcessingError
            record.update(passed=False, error=str(exc) if isinstance(exc, ProcessingError) else type(exc).__name__)
        record["seconds"] = round(time.monotonic() - began, 2)
        result["cases"].append(record)
        print(f"{record['name']}: {'pass' if record['passed'] else 'FAIL'} ({record['seconds']}s)", file=sys.stderr)
    result["passed"] = sum(item["passed"] for item in result["cases"])
    result["total"] = len(result["cases"])
    serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
