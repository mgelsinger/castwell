"""Transcript coverage diagnostics and bounded local recovery of missed speech.

Timestamp gaps are unknown audio, not evidence of silence or editorial content.
Recovery uses the already loaded speech model and never replaces existing words.
"""

from __future__ import annotations

import math
from pathlib import Path
import subprocess
import tempfile

GAP_SECONDS = 3.0
MAX_CROPS = 8
MAX_CROP_SECONDS = 120.0
CHUNK_SECONDS = 30.0
CONTEXT_SECONDS = 2.0


def analyze_transcript(transcript, *, gap_threshold_seconds=GAP_SECONDS):
    """Describe substantial untranscribed intervals, without guessing their role."""
    from .processing import validate_transcript

    transcript = validate_transcript(transcript)
    if (isinstance(gap_threshold_seconds, bool) or not isinstance(gap_threshold_seconds, (int, float))
            or not math.isfinite(gap_threshold_seconds) or gap_threshold_seconds <= 0):
        raise ValueError("Gap threshold must be a positive finite number")
    spans, bases = [], set()
    for segment in transcript["segments"]:
        words = [word for word in segment.get("words", []) if word["end"] > word["start"]]
        bases.add("words" if words else "segments")
        if words:
            spans.extend((word["start"], word["end"]) for word in words)
        else:
            spans.append((segment["start"], segment["end"]))
    cursor, gaps = 0.0, []
    for start, end in [*spans, (transcript["duration"], transcript["duration"])]:
        if start - cursor >= gap_threshold_seconds - 1e-8:
            gaps.append({"start": cursor, "end": start, "duration": round(start - cursor, 3)})
        cursor = max(cursor, end)
    return {
        "version": 1, "method": "timestamp-gaps", "gap_threshold_seconds": gap_threshold_seconds,
        "timestamp_basis": next(iter(bases)) if len(bases) == 1 else "mixed" if bases else "none",
        "gap_count": len(gaps), "untranscribed_seconds": round(sum(gap["duration"] for gap in gaps), 3),
        "longest_gap_seconds": max((gap["duration"] for gap in gaps), default=0), "gaps": gaps,
        "requires_review": bool(gaps),
        "warning": ("Some audio has no transcript. These gaps may contain silence, music, or missed speech. "
                    "Listen to them before trusting ad coverage.") if gaps else None,
    }


def _crop_audio(audio, target, start, end, should_cancel):
    from .processing import ProcessingError, _binary, _check_cancel, _stop_process

    _check_cancel(should_cancel)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([
            _binary("ffmpeg"), "-nostdin", "-y", "-v", "error", "-ss", str(start),
            "-i", str(Path(audio).resolve()), "-t", str(end - start), "-ac", "1", "-ar", "16000", str(target),
        ], stdout=subprocess.DEVNULL, stderr=errors)
        try:
            while process.poll() is None:
                _check_cancel(should_cancel)
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            _check_cancel(should_cancel)
            if process.returncode:
                raise ProcessingError("Could not decode the audio gap")
        finally:
            _stop_process(process)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _candidates(segment, *, offset, start, end):
    """Keep only plausible positive-duration words fully inside this gap crop."""
    from .processing import _words_text

    metrics = [getattr(segment, name, None) for name in ("no_speech_prob", "avg_logprob", "compression_ratio")]
    if (not all(_finite(value) for value in metrics)
            or metrics[0] > .6 or metrics[1] < -1 or metrics[2] > 2.4):
        return []
    groups, group = [], []
    for word in getattr(segment, "words", None) or []:
        left, right, probability = getattr(word, "start", None), getattr(word, "end", None), getattr(word, "probability", None)
        text = getattr(word, "word", None)
        valid = (all(_finite(value) for value in (left, right, probability))
                 and isinstance(text, str) and text.strip() and .5 <= probability <= 1
                 and start <= left + offset < right + offset <= end)
        if not valid or (group and (left + offset < group[-1]["end"] or left + offset - group[-1]["end"] > 1)):
            if group:
                groups.append(group)
                group = []
            if not valid:
                continue
        group.append({"word": text, "start": left + offset, "end": right + offset, "probability": probability})
    if group:
        groups.append(group)
    return [{"start": words[0]["start"], "end": words[-1]["end"], "text": _words_text(words),
             "words": words, "asr_recovered": True, "requires_review": True} for words in groups]


def recover_transcript_gaps(audio, transcript, engine, *, progress=None, should_cancel=None):
    """Retry bounded gaps with VAD off and preserve all original segment speech.

    Recovery is provisional. Confidence filters reduce obvious hallucinations;
    they do not establish correctness. Every inserted segment requires review.
    """
    from .processing import ProcessingCancelled, _check_cancel, validate_transcript

    transcript = validate_transcript(transcript)
    initial = analyze_transcript(transcript)
    attempts, candidates = [], []
    used_seconds, budget_exhausted = 0.0, False
    # Segment-internal word gaps remain diagnostic: inserting a new segment
    # inside an existing segment would change its text or violate its timeline.
    outer = analyze_transcript(dict(transcript, segments=[
        {key: value for key, value in segment.items() if key != "words"} for segment in transcript["segments"]
    ]))
    with tempfile.TemporaryDirectory(prefix="castwell-gap-") as directory:
        for gap in outer["gaps"]:
            start = gap["start"]
            while start < gap["end"]:
                _check_cancel(should_cancel)
                end = min(gap["end"], start + CHUNK_SECONDS)
                crop_start, crop_end = max(0, start - CONTEXT_SECONDS), min(transcript["duration"], end + CONTEXT_SECONDS)
                if len(attempts) >= MAX_CROPS or used_seconds + crop_end - crop_start > MAX_CROP_SECONDS:
                    budget_exhausted = True
                    break
                used_seconds += crop_end - crop_start
                attempt = {"start": start, "end": end, "crop_start": crop_start, "crop_end": crop_end,
                           "status": "empty", "accepted_segments": 0, "rejected_segments": 0}
                attempts.append(attempt)
                candidate_offset = len(candidates)
                source = None
                if progress:
                    progress(f"Checking missed speech: gap {len(attempts)} of at most {MAX_CROPS}")
                try:
                    target = Path(directory) / "gap.wav"
                    _crop_audio(audio, target, crop_start, crop_end, should_cancel)
                    _check_cancel(should_cancel)
                    source, _ = engine.transcribe(str(target), beam_size=5, vad_filter=False,
                                                 language=transcript["language"] if transcript["language"] != "unknown" else None,
                                                 word_timestamps=True, condition_on_previous_text=False)
                    source = iter(source)
                    while True:
                        _check_cancel(should_cancel)
                        try:
                            segment = next(source)
                        except StopIteration:
                            break
                        _check_cancel(should_cancel)
                        proposed = _candidates(segment, offset=crop_start, start=start, end=end)
                        for candidate in proposed:
                            if candidates and candidate["start"] < candidates[-1]["end"]:
                                attempt["rejected_segments"] += 1
                                continue
                            candidates.append(candidate)
                            attempt["accepted_segments"] += 1
                        if not proposed:
                            attempt["rejected_segments"] += 1
                    attempt["status"] = "recovered" if attempt["accepted_segments"] else "empty"
                except ProcessingCancelled:
                    raise
                except Exception:
                    # Failed recovery is visible, while the successful original
                    # transcript remains usable and untouched.
                    attempt["status"] = "failed"
                    del candidates[candidate_offset:]
                    attempt["accepted_segments"] = 0
                    attempt["error"] = "Gap transcription failed; this audio still needs listening."
                finally:
                    if source is not None and callable(getattr(source, "close", None)):
                        source.close()
                start = end
    _check_cancel(should_cancel)
    combined = sorted([*transcript["segments"], *candidates], key=lambda segment: (segment["start"], segment["end"]))
    result = validate_transcript(dict(transcript, segments=[dict(segment, id=index) for index, segment in enumerate(combined)]))
    quality = analyze_transcript(result)
    quality["recovery"] = {
        "enabled": True, "method": "bounded-gap-crops-no-vad", "model": transcript.get("model"),
        "initial_gap_count": initial["gap_count"], "initial_untranscribed_seconds": initial["untranscribed_seconds"],
        "attempts": attempts, "candidates": candidates, "recovered_segments": len(candidates),
        "failed_attempts": sum(attempt["status"] == "failed" for attempt in attempts),
        "processed_audio_seconds": round(used_seconds, 3),
        "budget_exhausted": budget_exhausted,
        "limits": {"max_crops": MAX_CROPS, "max_audio_seconds": MAX_CROP_SECONDS, "chunk_seconds": CHUNK_SECONDS},
    }
    if candidates or quality["recovery"]["failed_attempts"]:
        quality["requires_review"] = True
        quality["warning"] = "Missed speech was checked again. Recovered words are provisional; listen to recovered passages and remaining gaps before trusting ad coverage."
    result["quality"] = quality
    return result
