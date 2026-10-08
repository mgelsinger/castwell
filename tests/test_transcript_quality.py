"""Gap accounting and bounded recovery without model downloads or live inference."""

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from castwell.processing import ProcessingCancelled, transcribe
from castwell.transcript_quality import analyze_transcript, recover_transcript_gaps


def sample():
    return {"duration": 20, "language": "en", "model": "small.en", "segments": [
        {"id": 7, "start": 0, "end": 4, "text": "Original opening."},
        {"id": 9, "start": 14, "end": 20, "text": "Original return."},
    ]}


def word(text, start, end, probability=.95):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def segment(words, **metrics):
    return SimpleNamespace(words=words, no_speech_prob=metrics.get("no_speech_prob", .1),
                           avg_logprob=metrics.get("avg_logprob", -.2),
                           compression_ratio=metrics.get("compression_ratio", 1.2))


def engine_for(monkeypatch, rows):
    crop = Mock()
    monkeypatch.setattr("castwell.transcript_quality._crop_audio", crop)
    engine = Mock()
    engine.transcribe.side_effect = lambda *args, **kwargs: (iter(rows), SimpleNamespace(language="en"))
    return engine, crop


def test_quality_accounts_for_prefix_suffix_and_word_gaps_without_claiming_silence():
    value = {"duration": 25, "segments": [{"id": 0, "start": 3, "end": 20, "text": "Two words.", "words": [
        {"word": "Two", "start": 3, "end": 5}, {"word": "words.", "start": 15, "end": 20}]}]}
    quality = analyze_transcript(value)
    assert quality["timestamp_basis"] == "words"
    assert [(gap["start"], gap["end"]) for gap in quality["gaps"]] == [(0, 3), (5, 15), (20, 25)]
    assert quality["untranscribed_seconds"] == 18
    assert quality["longest_gap_seconds"] == 10
    assert quality["requires_review"] is True
    assert "silence, music, or missed speech" in quality["warning"]


def test_empty_transcript_remains_unknown_audio():
    quality = analyze_transcript({"duration": 30, "segments": []})
    assert quality["gaps"] == [{"start": 0, "end": 30, "duration": 30}]
    assert quality["requires_review"] is True


def test_short_pauses_do_not_create_quality_warnings():
    quality = analyze_transcript({"duration": 5, "segments": [{"start": 1, "end": 4, "text": "Hello."}]})
    assert quality["gap_count"] == 0
    assert quality["requires_review"] is False
    assert quality["warning"] is None


def test_gap_recovery_preserves_originals_and_accepts_only_contained_positive_words(monkeypatch):
    original = sample()
    frozen = copy.deepcopy(original)
    # Crop begins at2, two seconds before the true gap4..14.
    rows = [segment([word(" old", 1, 2.5), word(" recovered", 2, 4), word(" speech", 4, 6),
                     word(" zero", 6, 6), word(" crosses", 11, 13)])]
    engine, crop = engine_for(monkeypatch, rows)
    result = recover_transcript_gaps(Path("audio.wav"), original, engine)
    assert original == frozen
    assert [s["text"] for s in result["segments"]] == ["Original opening.", "recovered speech", "Original return."]
    assert [(s["start"], s["end"]) for s in result["segments"]] == [(0, 4), (4, 8), (14, 20)]
    assert result["segments"][1]["asr_recovered"] is True
    assert result["segments"][1]["requires_review"] is True
    assert result["quality"]["recovery"]["candidates"][0]["start"] == 4
    assert result["quality"]["requires_review"] is True
    assert result["quality"]["gap_count"] == 1
    assert crop.call_args.args[2:4] == (2, 16)
    kwargs = engine.transcribe.call_args.kwargs
    assert kwargs["vad_filter"] is False
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["word_timestamps"] is True
    assert kwargs["language"] == "en"


@pytest.mark.parametrize("metrics", [
    {"no_speech_prob": .95}, {"avg_logprob": -2}, {"compression_ratio": 3},
    {"no_speech_prob": float("nan")}, {"avg_logprob": None},
])
def test_hallucination_signals_never_fill_unknown_audio(monkeypatch, metrics):
    engine, _ = engine_for(monkeypatch, [segment([word(" suspicious", 2, 7)], **metrics)])
    result = recover_transcript_gaps(Path("audio.wav"), sample(), engine)
    assert len(result["segments"]) == 2
    quality = result["quality"]
    assert quality["untranscribed_seconds"] == 10
    assert quality["requires_review"] is True
    assert quality["recovery"]["attempts"][0]["rejected_segments"] == 1
    assert quality["recovery"]["attempts"][0]["status"] == "empty"


def test_bad_word_confidence_or_overlap_does_not_overwrite_prior_recovery(monkeypatch):
    rows = [segment([word(" kept", 2, 4), word(" overlap", 3, 5),
                     word(" low", 6, 7, .2), word(" nan", 7, 8, float("nan"))])]
    engine, _ = engine_for(monkeypatch, rows)
    result = recover_transcript_gaps(Path("audio.wav"), sample(), engine)
    assert [s["text"] for s in result["segments"] if s.get("asr_recovered")] == ["kept"]


def test_failed_recovery_keeps_original_and_records_safe_failure(monkeypatch):
    engine, _ = engine_for(monkeypatch, [])

    def failing_source():
        yield segment([word(" partial", 2, 4)])
        raise RuntimeError("private local path")

    engine.transcribe.return_value = None
    engine.transcribe.side_effect = lambda *args, **kwargs: (failing_source(), None)
    result = recover_transcript_gaps(Path("audio.wav"), sample(), engine)
    assert len(result["segments"]) == 2
    recovery = result["quality"]["recovery"]
    assert recovery["failed_attempts"] == 1
    assert recovery["candidates"] == []
    assert "private" not in str(recovery)
    assert result["quality"]["requires_review"] is True


def test_recovery_respects_total_audio_budget_and_never_downloads_a_model(monkeypatch):
    engine, crop = engine_for(monkeypatch, [])
    result = recover_transcript_gaps(Path("audio.wav"), {"duration": 600, "segments": []}, engine)
    recovery = result["quality"]["recovery"]
    assert recovery["processed_audio_seconds"] <= 120
    assert len(recovery["attempts"]) <= 8
    assert recovery["budget_exhausted"] is True
    assert recovery["attempts"]
    assert all(a["end"] - a["start"] <= 30 for a in recovery["attempts"])
    assert crop.call_count == engine.transcribe.call_count == len(recovery["attempts"])
    assert result["quality"]["untranscribed_seconds"] == 600


def test_word_gap_inside_original_segment_is_diagnostic_without_replacing_text(monkeypatch):
    original = {"duration": 20, "segments": [{"id": 5, "start": 0, "end": 20, "text": "Keep original text.",
        "words": [{"word": "Keep", "start": 0, "end": 1}, {"word": "text.", "start": 19, "end": 20}]}]}
    engine, crop = engine_for(monkeypatch, [])
    result = recover_transcript_gaps(Path("audio.wav"), original, engine)
    assert result["segments"][0]["text"] == "Keep original text."
    assert result["quality"]["gap_count"] == 1
    engine.transcribe.assert_not_called()
    crop.assert_not_called()


def test_cancellation_closes_recovery_generator_and_publishes_no_partial_transcript(monkeypatch):
    engine, _ = engine_for(monkeypatch, [])
    state = {"cancelled": False, "closed": False}

    def source():
        try:
            state["cancelled"] = True
            yield segment([word(" recovered", 2, 4)])
        finally:
            state["closed"] = True

    engine.transcribe.side_effect = lambda *args, **kwargs: (source(), None)
    with pytest.raises(ProcessingCancelled):
        recover_transcript_gaps(Path("audio.wav"), sample(), engine, should_cancel=lambda: state["cancelled"])
    assert state["closed"] is True
    engine.transcribe.assert_called_once()


def test_cancellation_before_recovery_sends_no_inference(monkeypatch):
    engine, crop = engine_for(monkeypatch, [])
    with pytest.raises(ProcessingCancelled):
        recover_transcript_gaps(Path("audio.wav"), sample(), engine, should_cancel=lambda: True)
    engine.transcribe.assert_not_called()
    crop.assert_not_called()


def test_crop_cancellation_reaps_decoder(monkeypatch, tmp_path):
    from castwell.transcript_quality import _crop_audio

    child = Mock()
    child.poll.return_value = None
    monkeypatch.setattr("castwell.transcript_quality.subprocess.Popen", Mock(return_value=child))
    monkeypatch.setattr("castwell.processing._binary", lambda name: name)
    checks = iter([False, True])
    with pytest.raises(ProcessingCancelled):
        _crop_audio(Path("audio.wav"), tmp_path / "crop.wav", 0, 5, lambda: next(checks))
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=2)


def test_transcribe_adds_diagnostics_and_recovery_defaults_on(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", Mock())
    monkeypatch.setattr("castwell.processing.probe_duration", lambda audio: 20)
    opening = SimpleNamespace(start=0, end=4, text="Opening.", words=[])
    returning = SimpleNamespace(start=14, end=20, text="Return.", words=[])
    engine, _ = engine_for(monkeypatch, [])
    engine.transcribe.side_effect = [(iter([opening, returning]), SimpleNamespace(language="en")),
                                     (iter([segment([word(" recovered", 2, 4)])]), None)]
    monkeypatch.setattr("castwell.processing._cached_model", lambda *args: engine)
    result = transcribe(Path("audio.wav"), model="small.en")
    assert result["quality"]["recovery"]["enabled"] is True
    assert result["quality"]["recovery"]["recovered_segments"] == 1
    assert engine.transcribe.call_count == 2


def test_transcribe_can_disable_gap_recovery_but_keeps_diagnostics(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", Mock())
    monkeypatch.setattr("castwell.processing.probe_duration", lambda audio: 20)
    opening = SimpleNamespace(start=0, end=4, text="Opening.", words=[])
    engine, crop = engine_for(monkeypatch, [])
    engine.transcribe.side_effect = [(iter([opening]), SimpleNamespace(language="en"))]
    monkeypatch.setattr("castwell.processing._cached_model", lambda *args: engine)
    result = transcribe(Path("audio.wav"), recover_gaps=False)
    assert result["quality"]["recovery"]["enabled"] is False
    assert result["quality"]["requires_review"] is True
    engine.transcribe.assert_called_once()
    crop.assert_not_called()
