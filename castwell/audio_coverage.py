"""Bounded CUDA speech evidence for audio missing from a transcript.

This checks reported timestamp gaps and every uncovered leading/trailing edge.
Short internal pauses below the ordinary timestamp-gap threshold are outside
this diagnostic's scope. A negative VAD result is model evidence, not listening
verification or a guarantee that the audio contains no speech.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from .processing import ProcessingCancelled, ProcessingError, _binary, _check_cancel, _stop_process, validate_transcript
from .transcript_quality import GAP_SECONDS, analyze_transcript


POLICY_VERSION = "cuda-gap-evidence-v2"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
THRESHOLD = .35
OVERLAP_SECONDS = .064
_VAD_LOCK = threading.Lock()


class _BudgetExhausted(Exception):
    pass


def _bounded(value, name, low, high, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not low <= value <= high or integer and not isinstance(value, int)):
        raise ValueError(f"Invalid {name}")
    return value


def _guard(should_cancel, deadline):
    _check_cancel(should_cancel)
    if time.monotonic() >= deadline:
        raise _BudgetExhausted


def _native_output(command, *, limit, timeout, should_cancel):
    """Bound a local decoder and reap only the child owned by this call."""
    _check_cancel(should_cancel)
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                _guard(should_cancel, deadline)
                if os.fstat(output.fileno()).st_size > limit:
                    raise ProcessingError("Audio inspection exceeded its output limit")
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            _check_cancel(should_cancel)
            if process.returncode:
                raise ProcessingError("Audio inspection could not decode the local recording")
            output.seek(0)
            data = output.read(limit + 1)
            if len(data) > limit:
                raise ProcessingError("Audio inspection exceeded its output limit")
            return data
        finally:
            _stop_process(process)


def _probe_audio(audio, *, timeout, should_cancel):
    path = Path(audio).resolve()
    if not path.is_file():
        raise ProcessingError("The local audio recording is unavailable")
    data = _native_output([_binary("ffprobe"), "-v", "error", "-select_streams", "a:0",
                           "-show_entries", "stream=codec_type,duration:format=duration", "-of", "json", str(path)],
                          limit=65536, timeout=timeout, should_cancel=should_cancel)
    try:
        value = json.loads(data)
        streams = value["streams"]
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise ValueError
        duration = float(streams[0].get("duration") or value["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return duration
    except (ValueError, TypeError, KeyError):
        raise ProcessingError("The local audio duration could not be verified") from None


def _decode_gap(audio, start, end, *, timeout, should_cancel):
    import numpy as np
    expected = round((end - start) * SAMPLE_RATE)
    data = _native_output([_binary("ffmpeg"), "-nostdin", "-v", "error", "-err_detect", "explode",
                           "-ss", f"{start:.9f}", "-i", str(Path(audio).resolve()), "-t", f"{end - start:.9f}",
                           "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-threads", "2",
                           "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"],
                          limit=(expected + SAMPLE_RATE) * 4, timeout=timeout, should_cancel=should_cancel)
    if len(data) % 4 or abs(len(data) // 4 - expected) > 1:
        raise ProcessingError("Decoded audio did not cover the requested gap")
    samples = np.frombuffer(data, dtype="<f4").copy()
    if not len(samples) or not np.isfinite(samples).all():
        raise ProcessingError("The gap did not contain valid decoded samples")
    return samples


@lru_cache(maxsize=4)
def _model_hash(path, size, modified):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_vad():
    from .gpu import configure_cuda_vad, cuda_vad_model
    from faster_whisper.utils import get_assets_path
    configure_cuda_vad()
    model = cuda_vad_model()
    session = model.session
    providers = session.get_providers()
    disabled = session.get_session_options().get_session_config_entry("session.disable_cpu_ep_fallback") == "1"
    if providers[:1] != ["CUDAExecutionProvider"] or not disabled:
        raise ProcessingError("Audio coverage requires CUDA with CPU fallback disabled")
    path = Path(get_assets_path()) / "silero_vad_v6.onnx"
    metadata = path.stat()
    return model, {"model": "silero_vad_v6.onnx", "model_sha256": _model_hash(str(path), metadata.st_size, metadata.st_mtime_ns),
                   "provider": providers[0], "cpu_fallback_disabled": True,
                   "session_source": "castwell.gpu.cuda_vad_model_cached"}


def _speech_frames(model, samples):
    """Retain any positive frame, without smoothing away short speech."""
    import numpy as np
    length = len(samples)
    # The upstream model needs complete 512-sample frames and mutates its last
    # context view. Keep a full zero frame at the end, outside the real audio.
    padding = FRAME_SAMPLES - length % FRAME_SAMPLES
    if padding < 64:
        padding += FRAME_SAMPLES
    padded = np.pad(samples, (0, padding))
    probabilities = np.asarray(model(padded)).reshape(-1)
    if (len(probabilities) != len(padded) // FRAME_SAMPLES or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()):
        raise ProcessingError("CUDA speech activity results were invalid")
    actual = probabilities[:math.ceil(length / FRAME_SAMPLES)]
    positive = np.flatnonzero(actual >= THRESHOLD)
    spans = []
    for frame in positive:
        start, end = int(frame) * FRAME_SAMPLES, min(length, (int(frame) + 1) * FRAME_SAMPLES)
        if spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return {"max_speech_probability": float(actual.max()), "positive_frames": len(positive),
            "speech_spans": [(start / SAMPLE_RATE, end / SAMPLE_RATE) for start, end in spans],
            "decoded_samples": length, "frame_padding_samples": padding}


def _coverage_spans(transcript, duration):
    spans = []
    for segment in transcript["segments"]:
        words = [word for word in segment.get("words", []) if word["end"] > word["start"]]
        spans.extend((word["start"], min(duration, word["end"])) for word in words)
        if not words:
            spans.append((segment["start"], min(duration, segment["end"])))
    return [(start, end) for start, end in spans if start < end]


def _gap_plan(transcript, quality, duration):
    current = analyze_transcript(transcript)
    candidates = [(gap["start"], min(gap["end"], duration), "timestamp_gap") for gap in current["gaps"]]
    valid_metadata = True
    original_gaps = quality.get("gaps", [])
    if not isinstance(original_gaps, list):
        original_gaps, valid_metadata = [], False
    for gap in original_gaps:
        try:
            start, end = gap["start"], gap["end"]
            if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)
                    or not 0 <= start < end <= transcript["duration"] + .05):
                raise ValueError
            candidates.append((start, min(end, duration), "original_quality_gap"))
        except (ValueError, TypeError, KeyError):
            valid_metadata = False
    spans = _coverage_spans(transcript, duration)
    if spans:
        if spans[0][0] >= 1 / SAMPLE_RATE - 1e-9:
            candidates.append((0, spans[0][0], "uncovered_leading_audio"))
        if duration - spans[-1][1] >= 1 / SAMPLE_RATE - 1e-9:
            candidates.append((spans[-1][1], duration, "uncovered_trailing_audio"))
    else:
        candidates.append((0, duration, "no_transcript_coverage"))
    gaps = []
    for start, end, origin in sorted(candidates):
        if end <= start:
            continue
        if gaps and start <= gaps[-1]["end"]:
            gaps[-1]["end"] = max(end, gaps[-1]["end"])
            gaps[-1]["origins"] = sorted(set([*gaps[-1]["origins"], origin]))
        else:
            gaps.append({"start": start, "end": end, "origins": [origin]})
    for gap in gaps:
        gap.update(duration=gap["end"] - gap["start"], status="not_checked", reason="pending", chunks=[])
    return gaps, spans, valid_metadata


def _declared_scope(transcript, quality):
    partial = quality.get("full_episode_coverage") is False or transcript.get("full_episode_coverage") is False
    declarations = []
    for owner, values in (("quality", quality), ("transcript", transcript)):
        if "assessed_audio_scope" not in values:
            continue
        scope = values["assessed_audio_scope"]
        declaration = {"source": owner, "scope": deepcopy(scope), "valid": False}
        try:
            start, end = scope["start"], scope["end"]
            if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)
                    or not 0 <= start < end <= transcript["duration"] + .05):
                raise ValueError
            declaration["valid"] = True
            partial |= start > 0 or end < transcript["duration"]
        except (ValueError, TypeError, KeyError):
            partial = True
        declarations.append(declaration)
    return bool(partial), declarations


def assess_audio_coverage(audio, transcript, *, max_gaps=32, max_audio_seconds=180, chunk_seconds=10,
                          max_wall_seconds=180, decode_timeout=20, progress=None, should_cancel=None):
    """Return a copy with auditable gap evidence; never alter transcript words.

    Cancellation and exhausted budgets return explicit unresolved evidence.
    Callers should honor ``audio_coverage.cancelled`` in their job lifecycle.
    ONNX inference cannot be interrupted mid-call; input chunks are bounded and
    cancellation/deadlines are checked before and after each native operation.
    Run after Whisper releases its processing lock. The cached Silero session
    uses independent per-call recurrent state; assessment calls share this lock.
    """
    _bounded(max_gaps, "maximum gaps", 1, 512, integer=True)
    _bounded(max_audio_seconds, "audio budget", .001, 3600)
    _bounded(chunk_seconds, "chunk duration", .1, 30)
    _bounded(max_wall_seconds, "wall budget", .01, 600)
    _bounded(decode_timeout, "decode timeout", .01, 60)
    source = validate_transcript(deepcopy(transcript))
    original = deepcopy(source.get("quality") or analyze_transcript(source))
    if not isinstance(original, dict):
        raise ValueError("Transcript quality must be an object")
    started = time.monotonic()
    deadline = started + max_wall_seconds
    duration, duration_status = source["duration"], "not_checked"
    cancelled = exhausted = False
    try:
        _guard(should_cancel, deadline)
        duration = _probe_audio(audio, timeout=min(decode_timeout, deadline - time.monotonic()), should_cancel=should_cancel)
        _guard(should_cancel, deadline)
        duration_status = "matched" if abs(duration - source["duration"]) <= .05 else "mismatch"
    except ProcessingCancelled:
        cancelled = True
    except _BudgetExhausted:
        exhausted = True
    except Exception:
        duration_status = "failed"
    gaps, spans, valid_metadata = _gap_plan(source, original, duration)
    represented = sum(end - start for start, end in spans)
    sparse = not spans or represented / duration < .10
    tail_only = bool(spans and duration >= 60 and spans[0][0] >= duration * .90)
    declared_partial, declared_scopes = _declared_scope(source, original)
    used_seconds, attempts = 0.0, 0
    model, model_metadata, model_failed = None, None, False
    for index, gap in enumerate(gaps):
        if cancelled or exhausted or duration_status != "matched" or index >= max_gaps:
            gap["reason"] = ("cancelled" if cancelled else "budget_exhausted" if exhausted or index >= max_gaps
                             else "audio_duration_unverified")
            exhausted |= index >= max_gaps
            continue
        if model_failed:
            gap.update(status="failed", reason="cuda_vad_unavailable", checked_full_gap=False)
            continue
        cursor = gap["start"]
        try:
            while cursor < gap["end"]:
                _guard(should_cancel, deadline)
                end = min(gap["end"], cursor + chunk_seconds)
                crop_start = max(gap["start"], cursor - OVERLAP_SECONDS)
                crop_end = min(gap["end"], end + OVERLAP_SECONDS)
                cost = crop_end - crop_start
                if used_seconds + cost > max_audio_seconds + 1e-8:
                    raise _BudgetExhausted
                used_seconds += cost
                attempts += 1
                if progress:
                    progress(f"Checking transcript coverage: passage {index + 1} of {len(gaps)}")
                samples = _decode_gap(audio, crop_start, crop_end,
                                      timeout=min(decode_timeout, deadline - time.monotonic()), should_cancel=should_cancel)
                _guard(should_cancel, deadline)
                while not _VAD_LOCK.acquire(timeout=.1):
                    _guard(should_cancel, deadline)
                try:
                    _guard(should_cancel, deadline)
                    if model is None:
                        try:
                            model, model_metadata = _load_vad()
                        except Exception:
                            model_failed = True
                            raise
                    _guard(should_cancel, deadline)
                    evidence = _speech_frames(model, samples)
                    _guard(should_cancel, deadline)
                finally:
                    _VAD_LOCK.release()
                chunk = {"start": crop_start, "end": crop_end, **evidence}
                chunk["speech_spans"] = [{"start": crop_start + left, "end": crop_start + right}
                                         for left, right in chunk["speech_spans"]]
                gap["chunks"].append(chunk)
                if evidence["positive_frames"]:
                    gap.update(status="speech_detected", reason="cuda_vad_positive_frame", checked_full_gap=end >= gap["end"])
                    break
                cursor = end
            else:
                gap.update(status="no_speech_detected", reason="cuda_vad_below_threshold", checked_full_gap=True)
        except ProcessingCancelled:
            cancelled = True
            gap.update(status="not_checked", reason="cancelled", checked_full_gap=False)
        except _BudgetExhausted:
            exhausted = True
            gap.update(status="not_checked", reason="budget_exhausted", checked_full_gap=False)
        except Exception:
            gap.update(status="failed", reason="decode_or_cuda_vad_failed", checked_full_gap=False)
    try:
        _guard(should_cancel, deadline)
    except ProcessingCancelled:
        cancelled = True
    except _BudgetExhausted:
        exhausted = True
    recovery = original.get("recovery") or {}
    valid_recovery = isinstance(recovery, dict)
    if not valid_recovery:
        recovery = {}
    recovery_attempts = recovery.get("attempts", [])
    valid_recovery &= isinstance(recovery_attempts, list) and all(isinstance(attempt, dict) for attempt in recovery_attempts)
    if not valid_recovery:
        recovery_attempts = []
    provisional = (any(segment.get("asr_recovered") for segment in source["segments"])
                   or bool(recovery.get("candidates")) or bool(recovery.get("recovered_segments")))
    recovery_failed = bool(not valid_recovery or recovery.get("failed_attempts")
                           or any(attempt.get("status") == "failed" for attempt in recovery_attempts))
    recovery_incomplete = bool(recovery.get("budget_exhausted")
                               or any(attempt.get("status") == "budget_exhausted" for attempt in recovery_attempts))
    execution = source.get("asr_execution") or {}
    normalization = (execution.get("timestamp_normalization") or {}) if isinstance(execution, dict) else {}
    normalization_dropped = False
    for key in ("dropped_words", "dropped_segments"):
        value = normalization.get(key, 0) if isinstance(normalization, dict) else None
        normalization_dropped |= isinstance(value, bool) or not isinstance(value, int) or value != 0
    unknown_prior_warning = bool(original.get("requires_review") and original.get("method") != "timestamp-gaps")
    complete = bool(duration_status == "matched" and valid_metadata and not sparse and not tail_only and not declared_partial
                    and not cancelled and not exhausted
                    and all(gap["status"] == "no_speech_detected" and gap.get("checked_full_gap") for gap in gaps))
    clear = (complete and not provisional and not recovery_failed and not recovery_incomplete
             and not normalization_dropped and not unknown_prior_warning)
    result = deepcopy(source)
    quality = deepcopy(original)
    quality["requires_review"] = not clear
    quality["audio_coverage"] = {
        "policy_version": POLICY_VERSION, "method": "bounded-cuda-silero-gap-frames", "model": model_metadata,
        "options": {"threshold": THRESHOLD, "speech_pad_ms": 0, "min_speech_duration_ms": 0,
                    "sampling_rate": SAMPLE_RATE, "frame_samples": FRAME_SAMPLES,
                    "chunk_seconds": chunk_seconds, "chunk_overlap_seconds": OVERLAP_SECONDS,
                    "max_gaps": max_gaps, "max_audio_seconds": max_audio_seconds, "max_wall_seconds": max_wall_seconds,
                    "decode_timeout_seconds": decode_timeout, "duration_tolerance_seconds": .05,
                    "decoded_sample_tolerance": 1, "minimum_edge_seconds": 1 / SAMPLE_RATE,
                    "minimum_represented_audio_fraction": .10},
        "scope": "Timestamp gaps at or above three seconds, original reported gaps, and uncovered leading/trailing audio. Short internal timing pauses are not independently certified.",
        "original_requires_review": original.get("requires_review"), "original_warning": original.get("warning"),
        "original_gaps": deepcopy(original.get("gaps", [])), "gaps": gaps,
        "audio_duration": duration, "transcript_duration": source["duration"], "duration_status": duration_status,
        "represented_audio_fraction": represented / duration, "sparse_transcript": sparse, "tail_only_transcript": tail_only,
        "declared_partial_scope": declared_partial, "declared_audio_scopes": declared_scopes,
        "full_episode_coverage_declared": original.get("full_episode_coverage", source.get("full_episode_coverage")),
        "provisional_recovered_speech": provisional, "prior_recovery_failure": recovery_failed,
        "prior_recovery_budget_exhausted": recovery_incomplete,
        "asr_normalization_dropped_content": normalization_dropped,
        "unrecognized_prior_warning": unknown_prior_warning, "valid_gap_metadata": valid_metadata,
        "valid_recovery_metadata": valid_recovery,
        "decode_attempts": attempts, "processed_audio_seconds": round(used_seconds, 6),
        "elapsed_seconds": round(time.monotonic() - started, 6), "cancelled": cancelled, "budget_exhausted": exhausted,
        "complete_assessment": complete, "generic_gap_review_cleared": bool(clear),
        "human_listening_verified": False,
        "interpretation": "No speech detected means every checked VAD frame stayed below 0.35. This is model evidence, not proof that speech or ads were absent.",
    }
    result["quality"] = quality
    return result
