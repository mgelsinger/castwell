#!/usr/bin/env python3
"""Audit saved word decisions and cuts without inference or approving edits.

The output file is private. Console output contains only aggregate results.
Audio checks decode existing files; they do not classify audio or render cuts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

EPSILON = 1e-7
LABELS = {"commercial", "editorial", "uncertain"}
DECODE_TIMEOUT_SECONDS = 120


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Times must be finite numbers")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("Times must be finite numbers") from error
    if not math.isfinite(result):
        raise ValueError("Times must be finite numbers")
    return result


def positive_number(value, *, integer=False, allow_text=False):
    if allow_text and isinstance(value, str):
        value = float(value)
    value = number(value)
    if value <= 0 or (integer and not value.is_integer()):
        raise ValueError("Audio metadata requires finite positive values and integral sample counts")
    return int(value) if integer else value


def merged(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def remaining(start, end, spans):
    """Subtract a sorted cut union from one source interval."""
    result, cursor = [], start
    for left, right in spans:
        if right <= cursor:
            continue
        if left >= end:
            break
        if left > cursor:
            result.append([cursor, min(left, end)])
        cursor = max(cursor, right)
    if cursor < end:
        result.append([cursor, end])
    return result


def selected_seconds(start, end, spans):
    return sum(max(0.0, min(end, right) - max(start, left)) for left, right in spans)


def audit_words(transcript, decisions, cuts):
    if not isinstance(transcript, dict) or not isinstance(decisions, dict):
        raise ValueError("Transcript and decisions must be objects")
    duration = number(transcript["duration"])
    if (duration <= 0 or not isinstance(cuts, list)
            or not isinstance(transcript.get("segments"), list) or not transcript["segments"]):
        raise ValueError("Positive duration, source segments and a cut list are required")
    words = []
    for segment in transcript["segments"]:
        if (not isinstance(segment, dict) or not isinstance(segment.get("words"), list)
                or not segment["words"]):
            raise ValueError("Every source segment must have original word timestamps")
        for item in segment["words"]:
            if not isinstance(item, dict):
                raise ValueError("Source words must be objects")
            start, end = number(item["start"]), number(item["end"])
            if not isinstance(item.get("word"), str) or not item["word"].strip():
                raise ValueError("Source words require text")
            if not 0 <= start <= end <= duration or (words and start < words[-1]["end"] - EPSILON):
                raise ValueError("Source words must be ordered, nonoverlapping and source-bounded")
            words.append({"id": len(words), "start": start, "end": end})
    labels = decisions.get("word_decisions")
    if (not words or type(decisions.get("source_word_count")) is not int
            or decisions["source_word_count"] != len(words) or not isinstance(labels, dict)
            or set(labels) != {str(i) for i in range(len(words))}
            or any(not isinstance(label, str) or label not in LABELS for label in labels.values())):
        raise ValueError("Decision IDs must exactly cover the original source words")
    for word in words:
        word["label"] = labels[str(word["id"])]
    intervals = []
    for cut in cuts:
        if not isinstance(cut, dict) or not isinstance(cut.get("approval_metadata", {}), dict):
            raise ValueError("Cuts and approval metadata must be objects")
        start, end = number(cut["start"]), number(cut["end"])
        if not 0 <= start < end <= duration:
            raise ValueError("Cuts must be positive source-bounded intervals")
        intervals.append([start, end])
    spans = merged(intervals)
    commercial_missed, noncommercial_selected, zero_commercial_outside = [], [], []
    commercial_seconds = commercial_selected = 0.0
    for word in words:
        start, end = word["start"], word["end"]
        seconds = end - start
        selected = selected_seconds(start, end, spans)
        point_selected = any(left <= start < right for left, right in spans)
        evidence = {**word, "selected_seconds": selected}
        if word["label"] == "commercial":
            commercial_seconds += seconds
            commercial_selected += selected
            if seconds > 0 and selected < seconds - EPSILON:
                commercial_missed.append(evidence)
            if seconds == 0 and not point_selected:
                zero_commercial_outside.append(evidence)
        elif selected > EPSILON or (seconds == 0 and point_selected):
            noncommercial_selected.append(evidence)
    boundaries = []
    for index, cut in enumerate(cuts):
        metadata = cut.get("approval_metadata", {})
        first, last = metadata.get("first_word_id"), metadata.get("last_word_id")
        owned = (type(first) is int and type(last) is int and 0 <= first <= last < len(words))
        boundary = {"cut_index": index, "start": cut["start"], "end": cut["end"],
                    "first_word_id": first, "last_word_id": last, "declared_word_ownership_valid": owned}
        if owned:
            boundary.update(
                start_matches_word=abs(cut["start"] - words[first]["start"]) <= EPSILON,
                end_matches_word=abs(cut["end"] - words[last]["end"]) <= EPSILON,
                owned_word_labels_all_commercial=all(words[i]["label"] == "commercial" for i in range(first, last + 1)),
                first_word=words[first], last_word=words[last],
                preceding_word=words[first - 1] if first else None,
                following_word=words[last + 1] if last + 1 < len(words) else None,
            )
        boundary["valid"] = owned and all(boundary[key] for key in (
            "start_matches_word", "end_matches_word", "owned_word_labels_all_commercial"))
        boundaries.append(boundary)
    gaps = []
    for left, right in zip(words, words[1:]):
        if left["label"] != "commercial" or right["label"] != "commercial" or left["end"] >= right["start"]:
            continue
        retained = remaining(left["end"], right["start"], spans)
        if not retained:
            continue
        gaps.append({"left_word_id": left["id"], "right_word_id": right["id"],
                     "source_start": left["end"], "source_end": right["start"],
                     "retained_seconds": sum(b - a for a, b in retained),
                     "retained_intervals": [{"source_start": a, "source_end": b,
                         "preview_start": a - selected_seconds(0, a, spans),
                         "preview_end": b - selected_seconds(0, b, spans)} for a, b in retained],
                     "semantic_status": "unclassified_audio_requires_listening"})
    removed = sum(end - start for start, end in spans)
    summary = {"source_word_count": len(words), "cut_count": len(cuts), "endpoint_count": 2 * len(cuts),
               "invalid_cut_boundary_count": sum(not row["valid"] for row in boundaries),
               "positive_duration_commercial_words": sum(w["label"] == "commercial" and w["end"] > w["start"] for w in words),
               "commercial_word_seconds": commercial_seconds,
               "commercial_word_seconds_outside_cuts": max(0.0, commercial_seconds - commercial_selected),
               "commercial_word_entries_not_fully_selected": len(commercial_missed),
               "noncommercial_source_word_intersections": len(noncommercial_selected),
               "noncommercial_source_word_seconds_selected": sum(w["selected_seconds"] for w in noncommercial_selected),
               "zero_duration_commercial_points_outside_cuts": len(zero_commercial_outside),
               "retained_gaps_between_commercial_words": len(gaps),
               "retained_gap_seconds": sum(g["retained_seconds"] for g in gaps),
               "source_duration_seconds": duration, "cut_union_seconds": removed,
               "intended_retained_duration_seconds": duration - removed,
               "overlapping_cut_seconds_before_union": sum(b - a for a, b in intervals) - removed,
               "source_word_consistency_passed": not commercial_missed and not zero_commercial_outside
                   and not noncommercial_selected and all(b["valid"] for b in boundaries)}
    return {"summary": summary, "cut_union": spans, "boundaries": boundaries,
            "commercial_source_word_misses": commercial_missed,
            "noncommercial_source_word_intersections": noncommercial_selected,
            "zero_duration_commercial_points_outside_cuts": zero_commercial_outside,
            "retained_gaps": gaps}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def audio_metadata(metadata):
    if (not isinstance(metadata, dict) or not isinstance(metadata.get("streams"), list)
            or len(metadata["streams"]) != 1 or not isinstance(metadata["streams"][0], dict)
            or not isinstance(metadata.get("format"), dict)):
        raise ValueError("Audio probe must contain one selected stream and a format object")
    stream = metadata["streams"][0]
    if not isinstance(stream.get("codec_name"), str) or not stream["codec_name"].strip():
        raise ValueError("Audio probe must identify the codec")
    return {"codec": stream["codec_name"],
            "sample_rate": positive_number(stream["sample_rate"], integer=True, allow_text=True),
            "channels": positive_number(stream["channels"], integer=True, allow_text=True),
            "container_duration_seconds": positive_number(metadata["format"]["duration"], allow_text=True)}


def decoded_pcm_size(command, *, timeout=DECODE_TIMEOUT_SECONDS):
    """Disk-backed output avoids pipe deadlock and bounds process wall time and RAM.

    subprocess.run kills and waits for the child on timeout before the temporary
    files close. Neither decoded audio nor error text is retained after this call.
    """
    with tempfile.TemporaryFile() as pcm, tempfile.TemporaryFile() as errors:
        subprocess.run(command, stdout=pcm, stderr=errors, check=True, timeout=timeout)
        decoded_bytes = pcm.seek(0, 2)
        if errors.seek(0, 2):
            raise ValueError("Full audio decode reported an error")
    return decoded_bytes


def decode_evidence(path, ffmpeg, ffprobe):
    probe = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=sample_rate,channels,codec_name:format=duration", "-of", "json", str(path)],
                           capture_output=True, check=True, timeout=30)
    metadata = audio_metadata(json.loads(probe.stdout))
    sample_rate, channels = metadata["sample_rate"], metadata["channels"]
    decoded_bytes = decoded_pcm_size([str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                                     "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"])
    if decoded_bytes <= 0 or decoded_bytes % (channels * 2):
        raise ValueError("Full audio decode failed or returned an invalid sample count")
    frames = decoded_bytes // (channels * 2)
    return {"full_decode_passed": True, **metadata, "decoded_sample_frames": frames,
            "decoded_seconds": frames / sample_rate, "decode_wall_timeout_seconds": DECODE_TIMEOUT_SECONDS}


def compare_durations(summary, source, preview, benchmark_duration):
    if not all(isinstance(value, dict) for value in (summary, source, preview)):
        raise ValueError("Duration comparison requires objects")
    benchmark_duration = positive_number(benchmark_duration)
    for value in (source, preview):
        positive_number(value["sample_rate"], integer=True)
        positive_number(value["decoded_seconds"])
        positive_number(value["container_duration_seconds"])
    positive_number(summary["source_duration_seconds"])
    if (type(summary["cut_count"]) is not int or summary["cut_count"] < 0
            or number(summary["cut_union_seconds"]) < 0
            or number(summary["intended_retained_duration_seconds"]) < 0):
        raise ValueError("Cut counts and durations must be nonnegative")
    if source["sample_rate"] != preview["sample_rate"]:
        raise ValueError("Sample-rate conversion needs a separate duration interpretation")
    tolerance = (2 * summary["cut_count"] + 2) / source["sample_rate"]
    expected_decoded = source["decoded_seconds"] - summary["cut_union_seconds"]
    decoded_delta = preview["decoded_seconds"] - expected_decoded
    declared_delta = preview["decoded_seconds"] - summary["intended_retained_duration_seconds"]
    return {"source": source, "preview": preview,
        "expected_from_decoded_source_seconds": expected_decoded,
        "decoded_duration_error_seconds": decoded_delta,
        "source_decoded_minus_declared_duration_seconds": source["decoded_seconds"] - summary["source_duration_seconds"],
        "preview_decoded_minus_declared_edit_duration_seconds": declared_delta,
        "sample_boundary_tolerance_seconds": tolerance,
        "decoded_duration_within_sample_boundary_tolerance": abs(decoded_delta) <= tolerance,
        "preview_decode_matches_declared_edit_timeline": abs(declared_delta) <= tolerance,
        "container_duration_error_seconds": preview["container_duration_seconds"] - summary["intended_retained_duration_seconds"],
        "benchmark_render_duration_matches": abs(preview["container_duration_seconds"] - benchmark_duration) <= EPSILON,
        "interpretation": "The renderer caps edits at the declared source duration. Compare both that declared timeline and the entire decoded source. Codec framing/delay/padding can make them differ; this audit does not determine whether any excess decoded tail is audible. Tolerance allows two sample boundaries per cut plus two samples. Duration checks do not establish waveform identity."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("transcript", "decisions", "cuts", "audio", "preview", "benchmark-report", "ffmpeg", "ffprobe", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--listening-packet", type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    transcript, decisions, cuts = map(read, (args.transcript, args.decisions, args.cuts))
    benchmark = read(args.benchmark_report)
    if (not isinstance(benchmark, dict) or not isinstance(benchmark.get("reference"), dict)
            or not isinstance(benchmark.get("render"), dict)):
        raise ValueError("Benchmark report must contain reference and render objects")
    benchmark_duration = positive_number(benchmark["render"]["duration"])
    audit = audit_words(transcript, decisions, cuts)
    inputs = {name: {"sha256": digest(path), "bytes": path.stat().st_size} for name, path in (
        ("transcript", args.transcript), ("decisions", args.decisions), ("cuts", args.cuts), ("audio", args.audio),
        ("preview", args.preview), ("benchmark_report", args.benchmark_report), ("audit_script", Path(__file__)),
        ("ffmpeg", args.ffmpeg), ("ffprobe", args.ffprobe))}
    if inputs["audio"]["sha256"] != benchmark["reference"]["source_audio_sha256"]:
        raise ValueError("Original audio differs from the frozen benchmark")
    if inputs["preview"]["sha256"] != benchmark["output_sha256"]:
        raise ValueError("Rendered audio differs from the frozen benchmark")
    packet_coverage = None
    if args.listening_packet:
        packet = read(args.listening_packet)
        if not isinstance(packet, dict) or not isinstance(packet.get("items"), list):
            raise ValueError("Listening packet must contain an item list")
        packet_ids = set()
        for item in packet["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("Listening packet windows must have string IDs")
            if (item["id"] in packet_ids or not 0 <= number(item["source_start"])
                    < number(item["source_end"]) <= audit["summary"]["source_duration_seconds"]):
                raise ValueError("Listening packet windows must be unique and source-bounded")
            packet_ids.add(item["id"])
        inputs["listening_packet"] = {"sha256": digest(args.listening_packet), "bytes": args.listening_packet.stat().st_size}
        if packet["source_audio_sha256"] != inputs["audio"]["sha256"] or packet["source_cuts_sha256"] != inputs["cuts"]["sha256"]:
            raise ValueError("Listening packet belongs to different source audio or cuts")
        for gap in audit["retained_gaps"]:
            gap["existing_packet_windows"] = [item["id"] for item in packet["items"]
                if item["source_start"] <= gap["source_start"] and item["source_end"] >= gap["source_end"]]
        for boundary in audit["boundaries"]:
            for endpoint in ("start", "end"):
                boundary[endpoint + "_packet_windows"] = [item["id"] for item in packet["items"]
                    if item["source_start"] <= boundary[endpoint] <= item["source_end"]]
        packet_coverage = {"retained_gaps_fully_inside_existing_windows": sum(bool(g["existing_packet_windows"]) for g in audit["retained_gaps"]),
                           "cut_endpoints_inside_existing_windows": sum(bool(b[e + "_packet_windows"]) for b in audit["boundaries"] for e in ("start", "end"))}
    source_audio = decode_evidence(args.audio, args.ffmpeg, args.ffprobe)
    rendered_audio = decode_evidence(args.preview, args.ffmpeg, args.ffprobe)
    duration_check = compare_durations(audit["summary"], source_audio, rendered_audio, benchmark_duration)
    public = {**audit["summary"], **(packet_coverage or {}),
              "source_and_preview_full_decode_passed": True,
              "decoded_duration_within_sample_boundary_tolerance": duration_check["decoded_duration_within_sample_boundary_tolerance"],
              "decoded_duration_error_seconds": duration_check["decoded_duration_error_seconds"],
              "preview_decode_matches_declared_edit_timeline": duration_check["preview_decode_matches_declared_edit_timeline"],
              "source_decoded_minus_declared_duration_seconds": duration_check["source_decoded_minus_declared_duration_seconds"],
              "independent_listening_verified": False, "acceptance_eligible": False, "inference_requests": 0}
    report = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "inputs": inputs,
              "policy_version": decisions.get("policy_version"), "removal_scope": decisions.get("removal_scope"),
              "public_safe_aggregate": public, "audit": audit, "audio_validation": duration_check,
              "limits": ["Source word labels are saved model decisions, not independent truth or new reference labels.",
                         "Retained gaps contain unclassified audio; they can include silence, music or unrecognized speech. No semantic content is inferred.",
                         "Matching hashes, boundaries and decoded duration do not establish audible cut quality or complete ad removal.",
                         "Existing listening-window overlap does not establish that anyone listened.",
                         "No holdouts, models, library data, approvals or cut decisions were changed."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(report, destination, indent=2)
        destination.write("\n")
    print(json.dumps(public, indent=2))


def cli():
    try:
        main()
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError,
            OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "error": "Saved-cut audit input or media validation failed; inspect private inputs locally."}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
