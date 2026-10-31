"""Batched speech keeps native timestamps, CUDA-only failure, and cleanup."""
from pathlib import Path
from copy import deepcopy
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from castwell import audio_coverage, batched_transcription as batch, processing, transcript_quality


@pytest.fixture
def speech(monkeypatch):
    state = SimpleNamespace(paths=[], mappings=[], cancelled=False, calls=[], closed=0)
    rows = [
        SimpleNamespace(start=1.2, end=2.0, text=" First.",
                        words=[SimpleNamespace(start=1.2, end=2.0, word=" First.", probability=.97)]),
        SimpleNamespace(start=18.1, end=19.3, text=" Second.",
                        words=[SimpleNamespace(start=18.1, end=19.3, word=" Second.", probability=.98)]),
    ]

    def decode(audio, path, should_cancel):
        state.paths.append(path)
        np.zeros(20 * batch.SAMPLE_RATE, dtype="<f4").tofile(path)
        return path.stat().st_size

    class Source:
        def __init__(self):
            self.rows = iter(rows)
        def __iter__(self):
            return self
        def __next__(self):
            return next(self.rows)
        def close(self):
            state.closed += 1

    state.source = Source()
    state.engine = Mock()
    state.engine.transcribe.side_effect = AssertionError("Batched failure cannot fall back to serial or CPU")

    class Pipeline:
        def __init__(self, engine):
            assert engine is state.engine
        def transcribe(self, waveform, **options):
            state.mappings.append(waveform)
            state.calls.append(options)
            return state.source, SimpleNamespace(language="en")

    state.factory = Mock(side_effect=Pipeline)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(BatchedInferencePipeline=state.factory))
    monkeypatch.setattr(batch, "_decode_pcm", decode)
    monkeypatch.setattr(processing, "probe_duration", lambda _: 20)
    monkeypatch.setattr(processing, "_cached_model", Mock(return_value=state.engine))
    monkeypatch.setattr(audio_coverage, "assess_audio_coverage", lambda _, transcript, **kw: transcript)
    monkeypatch.delenv("CASTWELL_CLASSIFIER_SLEEP_ADDRESS", raising=False)
    return state


def test_batched_original_word_timestamps_survive_long_silence_and_cleanup(speech):
    result = processing.transcribe(Path("audio.wav"), model="large-v3", batch_size=8, recover_gaps=False)
    assert [(s["start"], s["end"]) for s in result["segments"]] == [(1.2, 2.0), (18.1, 19.3)]
    assert result["segments"][1]["words"] == [{"word": " Second.", "start": 18.1, "end": 19.3, "probability": .98}]
    assert speech.calls == [{"beam_size": 5, "batch_size": 8, "vad_filter": True, "language": None,
                             "word_timestamps": True, "temperature": 0.0, "condition_on_previous_text": False,
                             "without_timestamps": True}]
    assert result["asr_execution"]["mode"] == "batched"
    assert result["asr_execution"]["decoded_audio_seconds"] == 20
    assert result["asr_execution"]["decode_and_alignment_seconds"] >= 0
    assert result["asr_execution"]["timestamp_normalization"] == {
        "source_segments": 2, "retained_segments": 2, "dropped_segments": 0,
        "source_words": 2, "retained_words": 2, "dropped_words": 0,
        "source_zero_duration_words": 0, "retained_zero_duration_words": 0,
        "clamped_segment_starts": 0, "clamped_segment_ends": 0,
        "clamped_word_starts": 0, "clamped_word_ends": 0,
    }
    assert speech.closed == 1
    assert speech.mappings[0]._mmap.closed
    assert not speech.paths[0].exists()
    assert result["quality"]["requires_review"]
    speech.engine.transcribe.assert_not_called()


def test_batched_preserves_recovery_and_coverage_work(speech, monkeypatch):
    phases = []
    def recovery(audio, transcript, engine, **kwargs):
        assert engine is speech.engine
        assert {key: kwargs[key] for key in ("max_crops", "max_audio_seconds", "max_wall_seconds")} == {
            "max_crops": 32, "max_audio_seconds": 240, "max_wall_seconds": 60,
        }
        assert kwargs["reconcile_boundaries"] is True
        phases.append("recovery")
        transcript["quality"] = {"requires_review": True, "recovery": {"enabled": True}}
        return transcript
    def coverage(audio, transcript, **kwargs):
        assert speech.closed == 1
        assert not speech.paths[0].exists()
        assert processing._MODEL_LOCK.acquire(blocking=False)
        processing._MODEL_LOCK.release()
        phases.append("coverage")
        return transcript
    monkeypatch.setattr(transcript_quality, "recover_transcript_gaps", recovery)
    monkeypatch.setattr(audio_coverage, "assess_audio_coverage", coverage)
    result = processing.transcribe(Path("audio.wav"), batch_size=8)
    assert phases == ["recovery", "coverage"]
    assert result["quality"]["recovery"]["enabled"]


def test_timestamp_normalization_reports_clamps_drops_and_zero_duration_words(speech):
    speech.source.rows = iter([
        SimpleNamespace(start=1, end=3, text="One zero overlap.", words=[
            SimpleNamespace(start=1, end=2, word=" One", probability=.9),
            SimpleNamespace(start=2, end=2, word=" zero", probability=.8),
            SimpleNamespace(start=1.8, end=1.9, word=" overlap", probability=.5),
        ]),
        SimpleNamespace(start=2.9, end=4, text="Two.", words=[
            SimpleNamespace(start=2.9, end=4, word=" Two.", probability=.9),
        ]),
    ])
    result = processing.transcribe(Path("audio.wav"), batch_size=8, recover_gaps=False)
    counts = result["asr_execution"]["timestamp_normalization"]
    assert counts["source_words"] == 4
    assert counts["retained_words"] == 3
    assert counts["dropped_words"] == 1
    assert counts["source_zero_duration_words"] == counts["retained_zero_duration_words"] == 1
    assert counts["clamped_segment_starts"] == 1
    assert counts["clamped_word_starts"] == 2
    assert [(w["start"], w["end"]) for s in result["segments"] for w in s["words"]] == [(1, 2), (2, 2), (3, 4)]


def test_batched_cuda_failure_is_visible_with_no_serial_retry(speech):
    speech.factory.side_effect = RuntimeError("CUDA out of memory")
    with pytest.raises(processing.ProcessingError, match="Local transcription failed"):
        processing.transcribe(Path("audio.wav"), batch_size=8)
    speech.engine.transcribe.assert_not_called()
    assert not speech.paths[0].exists()
    assert processing._MODEL_LOCK.acquire(blocking=False)
    processing._MODEL_LOCK.release()


def test_batched_cancellation_closes_native_source_and_mapping(speech):
    def progress(message):
        if message.startswith("Transcribing:"):
            speech.cancelled = True
    with pytest.raises(processing.ProcessingCancelled):
        processing.transcribe(Path("audio.wav"), batch_size=8, progress=progress,
                              should_cancel=lambda: speech.cancelled)
    assert speech.closed == 1
    assert speech.mappings[0]._mmap.closed
    assert not speech.paths[0].exists()
    assert processing._MODEL_LOCK.acquire(blocking=False)
    processing._MODEL_LOCK.release()


def test_batched_duration_limit_fails_before_model_load(speech, monkeypatch):
    monkeypatch.setattr(processing, "probe_duration", lambda _: batch.MAX_AUDIO_SECONDS + 1)
    with pytest.raises(processing.ProcessingError, match="bounded batched speech limit"):
        processing.transcribe(Path("audio.wav"), batch_size=8)
    processing._cached_model.assert_not_called()
    assert not speech.paths


@pytest.mark.parametrize("size", [0, 17, True, "8", 2.5])
def test_invalid_batch_size_fails_before_audio_or_model_work(size, monkeypatch):
    probe = Mock()
    monkeypatch.setattr(processing, "probe_duration", probe)
    with pytest.raises(ValueError, match="Speech batch size"):
        processing.transcribe(Path("audio.wav"), batch_size=size)
    probe.assert_not_called()


def test_word_gap_partition_is_lossless_and_preserves_source_ranges():
    words = [{"word": " One.", "start": 1., "end": 2., "probability": .91},
             {"word": " Two", "start": 7., "end": 8., "probability": .92},
             {"word": " words.", "start": 8., "end": 9., "probability": .93},
             {"word": " Three.", "start": 15., "end": 16., "probability": .94}]
    original = {"duration": 20., "segments": [{"id": 7, "start": 1., "end": 16.,
                 "text": "One. Two words. Three.", "words": words}]}
    frozen = deepcopy(original)
    result = batch.partition_word_gaps(original)
    assert original == frozen
    assert [w for s in result["segments"] for w in s["words"]] == words
    assert [(s["start"], s["end"], s["text"]) for s in result["segments"]] == [
        (1, 2, "One."), (7, 9, "Two words."), (15, 16, "Three."),
    ]
    assert [s["asr_source_word_range"] for s in result["segments"]] == [[0, 1], [1, 3], [3, 4]]
    assert all(s["asr_source_segment_id"] == 7 for s in result["segments"])
    assert all(s["asr_source_segment"]["text"] == original["segments"][0]["text"] for s in result["segments"])
    assert result["asr_execution"]["word_gap_partition"]["opened_internal_gaps"] == 2


def test_word_gap_partition_never_discards_or_invents_duration_for_zero_words():
    original = {"duration": 20., "segments": [{"id": 4, "start": 1., "end": 17., "text": "A B C", "words": [
        {"word": " A", "start": 1., "end": 1.}, {"word": " B", "start": 8., "end": 10.},
        {"word": " C", "start": 17., "end": 17.}]}]}
    result = batch.partition_word_gaps(original)
    assert result["segments"][0]["words"] == original["segments"][0]["words"]
    assert len(result["segments"]) == 1
    assert result["asr_execution"]["word_gap_partition"]["unsplittable_zero_duration_groups"] == 2
    assert transcript_quality.analyze_transcript(result)["requires_review"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_actual_pcm_decode_preserves_duration_and_enforces_size_limit(tmp_path, monkeypatch):
    original = tmp_path / "source.wav"
    with wave.open(str(original), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000)
    decoded = tmp_path / "audio.f32"
    assert batch._decode_pcm(original, decoded, None) == 64000
    assert decoded.stat().st_size == 64000
    monkeypatch.setattr(batch, "MAX_DECODED_BYTES", 32000)
    with pytest.raises(processing.ProcessingError, match="bounded batched speech limit"):
        batch._decode_pcm(original, decoded, None)
