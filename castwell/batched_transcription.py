"""Bounded CUDA Whisper batching with upstream original-time alignment.

Only PCM decoding and feature extraction use the host. Whisper and Silero use
the CUDA model/provider already prepared by processing._cached_model. No model
or device fallback is attempted when batching fails.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time


VERSION = "cuda-batch-v4"
SAMPLE_RATE = 16000
MAX_DECODED_BYTES = 600 * 1024 * 1024
MAX_AUDIO_SECONDS = MAX_DECODED_BYTES / (SAMPLE_RATE * 4)
MAX_BATCH_SIZE = 16


def validate_batch_size(batch_size):
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"Speech batch size must be an integer from 1 to {MAX_BATCH_SIZE}")
    return batch_size


def validate_batched_duration(duration):
    from .processing import ProcessingError
    if duration > MAX_AUDIO_SECONDS:
        raise ProcessingError("Audio exceeds the bounded batched speech limit. Use speech batch size 1 for this episode.")


def partition_word_gaps(transcript):
    """Expose internal missing audio without changing or discarding any word.

    Native text-only batches can have a long wordless span inside a segment.
    Splitting its existing word list lets ordinary bounded recovery inspect that
    audio. The original segment and each group's source word range are retained.
    Isolated zero-duration words stay attached to a neighbor and remain visible.
    """
    from .processing import _words_text, validate_transcript
    from .transcript_quality import GAP_SECONDS

    source = validate_transcript(transcript)
    result = dict(source)
    segments, split_count, gap_count, unsplit_zero_groups = [], 0, 0, 0
    before = [word for segment in source["segments"] for word in segment.get("words", [])]
    for segment in source["segments"]:
        words = segment.get("words") or []
        boundaries = [0]
        for index in range(1, len(words)):
            if words[index]["start"] - words[index - 1]["end"] >= GAP_SECONDS - 1e-8:
                boundaries.append(index)
        boundaries.append(len(words))
        ranges = list(zip(boundaries[:-1], boundaries[1:]))
        # A zero-length segment is not valid. Do not drop or stretch its words
        # just to make an internal gap eligible for automatic recovery.
        index = 0
        while len(ranges) > 1 and index < len(ranges):
            left, right = ranges[index]
            if words[right - 1]["end"] > words[left]["start"]:
                index += 1
                continue
            unsplit_zero_groups += 1
            if index:
                ranges[index - 1] = (ranges[index - 1][0], right)
                del ranges[index]
                index -= 1
            else:
                ranges[1] = (left, ranges[1][1])
                del ranges[0]
        if len(ranges) <= 1:
            segments.append(dict(segment, asr_source_segment_id=segment["id"],
                                 asr_source_word_range=[0, len(words)]))
            continue
        split_count += 1
        gap_count += len(ranges) - 1
        origin = {key: segment[key] for key in ("id", "start", "end", "text")}
        for left, right in ranges:
            group = words[left:right]
            segments.append(dict(segment, start=group[0]["start"], end=group[-1]["end"],
                                 text=_words_text(group), words=group, asr_source_segment=origin,
                                 asr_source_segment_id=segment["id"], asr_source_word_range=[left, right]))
    result["segments"] = [dict(segment, id=index) for index, segment in enumerate(segments)]
    result = validate_transcript(result)
    after = [word for segment in result["segments"] for word in segment.get("words", [])]
    if before != after:
        raise ValueError("Word gap partition changed the original transcript words")
    result["asr_execution"] = dict(source.get("asr_execution") or {})
    result["asr_execution"]["word_gap_partition"] = {
        "version": "lossless-word-gaps-v1", "minimum_gap_seconds": GAP_SECONDS,
        "source_segments": len(source["segments"]), "partitioned_segments": len(segments),
        "split_source_segments": split_count, "opened_internal_gaps": gap_count,
        "unsplittable_zero_duration_groups": unsplit_zero_groups,
        "original_words": len(before), "retained_words": len(after), "word_values_and_order_preserved": True,
    }
    return result


def _decode_pcm(audio, target, should_cancel):
    from .processing import ProcessingError, _binary, _check_cancel, _stop_process

    _check_cancel(should_cancel)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([
            _binary("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(Path(audio).resolve()),
            "-map", "0:a:0", "-vn", "-t", str(MAX_AUDIO_SECONDS + 1),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-acodec", "pcm_f32le", "-f", "f32le", str(target),
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
                raise ProcessingError("Could not decode audio for batched speech recognition.")
        finally:
            _stop_process(process)
    size = target.stat().st_size
    if size <= 0 or size % 4:
        raise ProcessingError("The decoder did not produce valid speech audio.")
    if size > MAX_DECODED_BYTES:
        raise ProcessingError("Audio exceeds the bounded batched speech limit. Use speech batch size 1 for this episode.")
    return size


@contextmanager
def batched_source(engine, audio, *, duration, batch_size, language=None, should_cancel=None):
    """Yield native timed segments while bounding decoded and GPU working data.

    A memory-mapped PCM file avoids keeping a second full decoded waveform in
    RAM. The explicit cap also bounds the upstream feature/VAD preparation,
    which happens eagerly before its GPU batches are iterated. No arbitrary
    audio seams or synthetic timestamp offsets are introduced.
    """
    from .processing import _check_cancel
    from faster_whisper import BatchedInferencePipeline
    import numpy as np

    validate_batch_size(batch_size)
    validate_batched_duration(duration)
    _check_cancel(should_cancel)
    source = waveform = None
    with tempfile.TemporaryDirectory(prefix="castwell-speech-batch-") as directory:
        try:
            target = Path(directory) / "audio.f32"
            started = time.perf_counter()
            size = _decode_pcm(audio, target, should_cancel)
            decoded = time.perf_counter()
            _check_cancel(should_cancel)
            waveform = np.memmap(target, mode="r", dtype="<f4")
            pipeline = BatchedInferencePipeline(engine)
            source, info = pipeline.transcribe(
                waveform, beam_size=5, batch_size=batch_size, vad_filter=True,
                language=language, word_timestamps=True, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=True,
            )
            prepared = time.perf_counter()
            _check_cancel(should_cancel)
            metadata = {
                "version": VERSION, "mode": "batched", "device": "cuda", "compute_type": "float16",
                "batch_size": batch_size, "beam_size": 5, "temperature": 0.0,
                "condition_on_previous_text": False, "word_timestamps": True,
                "without_timestamps": True,
                "timestamp_mapping": "faster-whisper-original-audio",
                "max_decoded_bytes": MAX_DECODED_BYTES, "decoded_audio_seconds": size / (SAMPLE_RATE * 4),
                "decode_seconds": round(decoded - started, 6),
                "vad_and_features_seconds": round(prepared - decoded, 6),
            }

            def cancellable_segments():
                started_inference = time.perf_counter()
                try:
                    iterator = iter(source)
                    while True:
                        _check_cancel(should_cancel)
                        try:
                            segment = next(iterator)
                        except StopIteration:
                            return
                        _check_cancel(should_cancel)
                        yield segment
                finally:
                    metadata["decode_and_alignment_seconds"] = round(time.perf_counter() - started_inference, 6)

            yield cancellable_segments(), info, metadata
        finally:
            try:
                if source is not None and callable(getattr(source, "close", None)):
                    source.close()
            finally:
                if waveform is not None:
                    waveform._mmap.close()
