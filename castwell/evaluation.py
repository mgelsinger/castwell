"""Read-only detector comparison on authored, time-aligned challenge cases.

This module never opens a library or renders audio. A classifier's confidence
is an approval input, not an estimate of measured accuracy.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
from urllib.parse import urlsplit

from . import __version__, processing


APPROVAL_THRESHOLD = 0.90
DEFAULT_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_MODEL = "castwell-local"
BACKENDS = ("heuristic", "local-ai", "jev")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Interval endpoints must be finite numbers")
    return float(value)


def union_intervals(intervals, duration):
    """Normalize overlapping or touching spans without counting time twice."""
    normalized = []
    for span in intervals:
        start, end = _number(span["start"]), _number(span["end"])
        if not 0 <= start < end <= duration:
            raise ValueError("Intervals must lie within the transcript duration")
        normalized.append((start, end))
    merged = []
    for start, end in sorted(normalized):
        if merged and start <= merged[-1]["end"]:
            merged[-1]["end"] = max(end, merged[-1]["end"])
        else:
            merged.append({"start": start, "end": end})
    return merged


def _seconds(spans):
    return sum(span["end"] - span["start"] for span in spans)


def interval_metrics(proposed, expected, duration):
    """Score union durations and deterministic one-to-one overlapping matches.

    Boundary matching greedily takes the greatest positive intersection-over-
    union, then greatest overlap, then earlier span indices. Unmatched spans
    have counts, not an invented boundary error. Duration errors score all
    spans regardless of matching. Exact match means equal normalized unions.
    """
    predicted = union_intervals(proposed, duration)
    truth = union_intervals(expected, duration)
    pairs = []
    overlap_seconds = 0.0
    for p_index, prediction in enumerate(predicted):
        for t_index, target in enumerate(truth):
            overlap = max(0.0, min(prediction["end"], target["end"]) - max(prediction["start"], target["start"]))
            overlap_seconds += overlap
            if overlap:
                combined = _seconds([prediction, target]) - overlap
                pairs.append((overlap / combined, overlap, p_index, t_index))
    used_predicted, used_truth, matches = set(), set(), []
    for iou, overlap, p_index, t_index in sorted(pairs, key=lambda row: (-row[0], -row[1], row[2], row[3])):
        if p_index in used_predicted or t_index in used_truth:
            continue
        used_predicted.add(p_index)
        used_truth.add(t_index)
        prediction, target = predicted[p_index], truth[t_index]
        matches.append({
            "predicted_index": p_index, "expected_index": t_index,
            "intersection_seconds": overlap, "intersection_over_union": iou,
            "start_error_seconds": abs(prediction["start"] - target["start"]),
            "end_error_seconds": abs(prediction["end"] - target["end"]),
        })
    total_error = sum(row["start_error_seconds"] + row["end_error_seconds"] for row in matches)
    exact = len(predicted) == len(truth) and all(
        math.isclose(a[edge], b[edge], abs_tol=1e-7, rel_tol=0)
        for a, b in zip(predicted, truth) for edge in ("start", "end"))
    return {
        "editorial_seconds_wrongly_cut": max(0.0, _seconds(predicted) - overlap_seconds),
        "ad_seconds_missed": max(0.0, _seconds(truth) - overlap_seconds),
        "total_ad_seconds": _seconds(truth),
        "total_editorial_seconds": duration - _seconds(truth),
        "ad_seconds_selected": overlap_seconds,
        "selected_seconds": _seconds(predicted),
        "exact_match": exact,
        "boundary_absolute_error_seconds": total_error,
        "mean_boundary_error_seconds": total_error / (2 * len(matches)) if matches else None,
        "matched_spans": len(matches),
        "unmatched_predicted_spans": len(predicted) - len(matches),
        "unmatched_expected_spans": len(truth) - len(matches),
        "boundary_matches": matches,
    }


def validate_dataset(dataset):
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1 or not isinstance(dataset.get("cases"), list):
        raise ValueError("Expected a schema_version 1 challenge with a cases list")
    if not isinstance(dataset.get("scope"), str) or not dataset["scope"].strip():
        raise ValueError("The challenge must describe its scope")
    identifiers = set()
    group_splits = {}
    cases = []
    for case in dataset["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Challenge cases must be objects")
        for field in ("id", "group", "category", "description"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"Each case needs a nonempty {field}")
        if case["id"] in identifiers:
            raise ValueError("Challenge case IDs must be unique")
        identifiers.add(case["id"])
        if case.get("split") not in ("dev", "eval") or not isinstance(case.get("expect_review"), bool):
            raise ValueError("Each case needs a dev/eval split and boolean expect_review")
        if case["group"] in group_splits and group_splits[case["group"]] != case["split"]:
            raise ValueError("Podcast/host groups must not cross development and evaluation splits")
        group_splits[case["group"]] = case["split"]
        if not isinstance(case.get("expected_intervals"), list):
            raise ValueError("Each case needs expected_intervals")
        if case["category"] == "ambiguous" and (not case["expect_review"] or case["expected_intervals"]):
            raise ValueError("Ambiguous cases require expect_review=true and empty expected_intervals")
        transcript = processing.validate_transcript(case.get("transcript"))
        expected = union_intervals(case["expected_intervals"], transcript["duration"])
        cases.append(dict(case, transcript=transcript, expected_intervals=expected))
    return dict(dataset, cases=cases)


def validate_endpoint(base_url, *, allow_remote=False):
    """Default permit list is literal loopback hosts, without DNS resolution."""
    if not isinstance(base_url, str) or any(char.isspace() for char in base_url) or "\\" in base_url:
        raise ValueError("Classifier endpoint must be an HTTP(S) base URL")
    try:
        parsed = urlsplit(base_url)
        parsed.port
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "?" in base_url or "#" in base_url):
            raise ValueError
    except ValueError:
        raise ValueError("Classifier endpoint must be HTTP(S), without credentials, query, or fragment") from None
    if not allow_remote and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("local-ai requires literal 127.0.0.1, localhost, or ::1; remote use requires --allow-remote")
    return base_url.rstrip("/")


def validate_backends(backends, *, base_url, allow_remote=False, allow_paid_api=False, jev_api_key=None, jev_base_url="https://api.typesafe.ai"):
    selected = list(dict.fromkeys(backends or ["heuristic"]))
    if any(name not in BACKENDS for name in selected):
        raise ValueError("Unknown comparison backend")
    # Validate every requested provider before calling any detector.
    if "jev" in selected:
        if not allow_paid_api:
            raise ValueError("Jev requests are disabled; explicit --allow-paid-api is required")
        if not isinstance(jev_api_key, str) or not jev_api_key.strip():
            raise ValueError("Jev requires an explicitly supplied API key")
        validate_endpoint(jev_base_url, allow_remote=True)
        if jev_base_url.rstrip("/") not in ("https://api.typesafe.ai", "https://api.typesafe.ai/v1"):
            raise ValueError("Jev credentials may only be sent to the official https://api.typesafe.ai endpoint")
    if "local-ai" in selected:
        validate_endpoint(base_url, allow_remote=allow_remote)
    return selected


def _provenance():
    root = Path(__file__).resolve().parents[1]
    revision, dirty = None, None
    try:
        revision_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
        if revision_result.returncode == 0:
            revision = revision_result.stdout.strip()
            status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=5)
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    versions = {"castwell": __version__}
    for package in ("requests", "llama-cpp-python"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    sources = {}
    for name in ("processing.py", "evaluation.py", "jev.py"):
        path = Path(__file__).parent / name
        if path.is_file():
            sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"git_revision": revision, "git_dirty": dirty, "python": platform.python_version(),
            "package_versions": versions, "source_sha256": sources}


def _run_backend(name, transcript, *, base_url, model, jev_api_key, jev_base_url, jev_model):
    if name == "jev":
        from .jev import classify_transcript
        return classify_transcript(transcript, base_url=jev_base_url, model=jev_model, api_key=jev_api_key, allow_paid_api=True)
    cuts = processing.detect_ads(transcript, "heuristic" if name == "heuristic" else "ai", config={
        "ai_base_url": base_url if name == "local-ai" else "",
        "ai_model": model if name == "local-ai" else "",
        "ai_key": "", "ai_allow_redirects": False, "ai_trust_env": False,
        "review_only": False, "auto_approve_threshold": APPROVAL_THRESHOLD,
    })
    return {"cuts": cuts, "review": any(not cut["approved"] or cut.get("requires_review", False) for cut in cuts)}


def summarize(records):
    scored = [row for row in records if row["status"] == "ok" and not row["ambiguous"]]
    succeeded = [row for row in records if row["status"] == "ok"]
    eligible = [row for row in records if not row["ambiguous"]]
    ambiguous = [row for row in records if row["ambiguous"]]
    ambiguous_succeeded = [row for row in ambiguous if row["status"] == "ok"]
    result = {
        "attempted_cases": len(records), "successful_cases": len(succeeded),
        "failed_cases": sum(row["status"] == "error" for row in records),
        "ambiguous_cases": sum(row["ambiguous"] for row in records),
        "accuracy_eligible_cases": len(eligible), "accuracy_scored_cases": len(scored),
        "accuracy_failed_cases": len(eligible) - len(scored),
        "eligible_ad_seconds_including_failed_cases": sum(_seconds(row["expected_intervals"]) for row in eligible),
        "eligible_duration_seconds_including_failed_cases": sum(row["duration"] for row in eligible),
        "review_scored_cases": len(succeeded),
        "review_expected_cases": sum(row["expect_review"] for row in succeeded),
        "review_observed_cases": sum(row["review"] for row in succeeded),
        "manual_review_cases": sum(any(not cut["approved"] for cut in row["cuts"]) for row in succeeded),
        "review_true_positive_cases": sum(row["expect_review"] and row["review"] for row in succeeded),
        "review_false_positive_cases": sum(not row["expect_review"] and row["review"] for row in succeeded),
        "review_false_negative_cases": sum(row["expect_review"] and not row["review"] for row in succeeded),
    }
    result["ambiguous_safety"] = {
        "attempted_cases": len(ambiguous), "successful_cases": len(ambiguous_succeeded),
        "failed_cases": len(ambiguous) - len(ambiguous_succeeded),
        "eligible_duration_seconds_including_failed_cases": sum(row["duration"] for row in ambiguous),
        "scored_duration_seconds": sum(row["duration"] for row in ambiguous_succeeded),
    }
    for view in ("proposed", "approved", "threshold_shadow"):
        ambiguous_spans = [row["intervals"][view] for row in ambiguous_succeeded]
        result["ambiguous_safety"][view] = {
            "selected_seconds": sum(_seconds(spans) for spans in ambiguous_spans),
            "union_spans": sum(len(spans) for spans in ambiguous_spans),
            "cases_with_cuts": sum(bool(spans) for spans in ambiguous_spans),
        }
        metrics = [row["metrics"][view] for row in scored]
        keys = ("editorial_seconds_wrongly_cut", "ad_seconds_missed", "total_ad_seconds", "total_editorial_seconds",
                "ad_seconds_selected", "selected_seconds", "boundary_absolute_error_seconds", "matched_spans",
                "unmatched_predicted_spans", "unmatched_expected_spans")
        total = {key: sum(item[key] for item in metrics) for key in keys}
        total["exact_match_cases"] = sum(item["exact_match"] for item in metrics)
        total["exact_match_rate"] = total["exact_match_cases"] / len(metrics) if metrics else None
        total["mean_boundary_error_seconds"] = (total["boundary_absolute_error_seconds"] / (2 * total["matched_spans"])
                                                  if total["matched_spans"] else None)
        result[view] = total
    return result


def evaluate_dataset(dataset, *, backends=None, base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL,
                     model_label=None, split="eval", allow_remote=False, allow_paid_api=False,
                     jev_api_key=None, jev_base_url="https://api.typesafe.ai", jev_model="jev-1.13.0",
                     dataset_sha256=None, on_case=None):
    selected = validate_backends(backends, base_url=base_url, allow_remote=allow_remote,
                                 allow_paid_api=allow_paid_api, jev_api_key=jev_api_key, jev_base_url=jev_base_url)
    if split not in ("dev", "eval", "all"):
        raise ValueError("Split must be dev, eval, or all")
    validated = validate_dataset(dataset)
    cases = [case for case in validated["cases"] if split == "all" or case["split"] == split]
    if not cases:
        raise ValueError("The requested split contains no cases")
    if dataset_sha256 is None:
        dataset_sha256 = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        hash_basis = "canonical JSON, sorted keys, UTF-8"
    else:
        hash_basis = "original fixture file bytes"
    result = {
        "schema_version": 1, "scope": validated["scope"], "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": dataset_sha256, "dataset_hash_basis": hash_basis, "provenance": _provenance(),
        "settings": {"split": split, "approval_threshold": APPROVAL_THRESHOLD, "shadow_only": True,
                     "allow_remote": allow_remote, "allow_paid_api": allow_paid_api,
                     "local_ai_inherited_credentials": False, "local_ai_redirects": False,
                     "local_ai_environment_proxies_and_netrc": False,
                     "ai_window_chars": os.environ.get("CASTWELL_AI_WINDOW_CHARS", "18000"),
                     "ai_context_segments": os.environ.get("CASTWELL_AI_CONTEXT_SEGMENTS", "12"),
                     "ai_timeout_seconds": os.environ.get("CASTWELL_AI_TIMEOUT", "180")},
        "metric_definitions": {
            "primary": "Approved editorial seconds wrongly cut; lower is better. No audio is modified.",
            "duration": "Union intersection of authored intervals; all remaining transcript time is non-ad time.",
            "boundary": "Greedy one-to-one positive-overlap matching ranked by IoU, overlap, then indices; report absolute start/end errors. Unmatched spans have counts, no fabricated boundary error.",
            "ambiguity": "category=ambiguous cases are excluded from accuracy denominators. ambiguous_safety reports union seconds/spans and case counts separately; approved selections are unsupported automatic cuts, not established editorial or ad errors. Failed cases remain explicit.",
            "review_signal": "review_observed is any unapproved/requires_review cut for heuristic/local-ai, but Jev's explicit uncertainty/conflict signal. manual_review_cases separately counts cases containing unapproved cuts for every backend.",
            "failure": "Errors are excluded from scored metrics, never counted as successful no-ad predictions; eligible totals include failed cases.",
            "proposed": "All candidate intervals, including unapproved review suggestions.",
            "approved": "Classifier-approved intervals with the production approval threshold frozen at 0.90. Jev's adapter never auto-approves.",
            "threshold_shadow": "Hypothetical intervals at confidence >= 0.90, excluding explicit requires_review spans and Jev responses requesting review. Separate from actual approval; provider scores are not interchangeable.",
            "confidence": "Reported confidence is not measured accuracy or a calibrated probability.",
        },
        "backends": [],
    }
    for backend in selected:
        config = {"backend": backend, "model": model if backend == "local-ai" else jev_model if backend == "jev" else "cue-rules",
                  "model_label": model_label if backend == "local-ai" and model_label else model if backend == "local-ai" else jev_model if backend == "jev" else "Castwell heuristic",
                  "base_url": base_url if backend == "local-ai" else jev_base_url if backend == "jev" else None}
        records = []
        for case in cases:
            began = time.perf_counter()
            record = {key: case[key] for key in ("id", "split", "group", "category", "description", "expected_intervals", "expect_review")}
            record.update(duration=case["transcript"]["duration"], ambiguous=case["category"] == "ambiguous",
                          status="error", cuts=None, review=None, metrics=None)
            try:
                answer = _run_backend(backend, case["transcript"], base_url=base_url, model=model,
                                      jev_api_key=jev_api_key, jev_base_url=jev_base_url, jev_model=jev_model)
                cuts = processing.validate_cuts(answer["cuts"], record["duration"])
                review = answer.get("review", False)
                if not isinstance(review, bool):
                    raise ValueError("Backend review flag must be boolean")
                views = {
                    "proposed": cuts,
                    "approved": [cut for cut in cuts if cut["approved"]],
                    "threshold_shadow": [cut for cut in cuts if cut["confidence"] >= APPROVAL_THRESHOLD
                                         and not cut.get("requires_review", False) and not (backend == "jev" and review)],
                }
                record.update(status="ok", cuts=cuts, review=review, intervals={key: union_intervals(value, record["duration"]) for key, value in views.items()},
                              usage=answer.get("usage", {}))
                if not record["ambiguous"]:
                    record["metrics"] = {key: interval_metrics(value, case["expected_intervals"], record["duration"]) for key, value in views.items()}
                # Retain only explicitly public provider identity fields, never raw responses or keys.
                record["provider_metadata"] = {key: answer[key] for key in ("model", "provider", "request_count", "policy_version") if key in answer}
                if backend == "jev":
                    record["decisions"] = answer.get("decisions", [])
            except Exception as exc:
                message = str(exc) if isinstance(exc, processing.ProcessingError) else "Classification failed; this case is excluded from accuracy metrics."
                record.update(status="error", error={"type": type(exc).__name__, "message": message},
                              cuts=None, review=None, metrics=None)
            record["seconds"] = round(time.perf_counter() - began, 6)
            records.append(record)
            if on_case:
                on_case(backend, record)
        result["backends"].append({"config": config, "cases": records, "summary": summarize(records)})
    return result


def evaluate_file(path, **kwargs):
    raw = Path(path).read_bytes()
    return evaluate_dataset(json.loads(raw.decode("utf-8")), dataset_sha256=hashlib.sha256(raw).hexdigest(), **kwargs)
