"""CUDA coverage contracts, with no real model, network or decoder calls."""
from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from castwell import audio_coverage as coverage, gpu
from castwell.processing import ProcessingCancelled, ProcessingError
from castwell.transcript_quality import analyze_transcript


def source():
    return {"duration": 20, "language": "en", "segments": [
        {"id": 0, "start": 0, "end": 4, "text": "Opening."},
        {"id": 1, "start": 14, "end": 20, "text": "Return."}]}


@pytest.fixture
def runtime(monkeypatch):
    def decode(audio, start, end, **options):
        return np.zeros(round((end - start) * coverage.SAMPLE_RATE), dtype="float32")
    model = Mock(side_effect=lambda samples: np.zeros((len(samples) // coverage.FRAME_SAMPLES, 1), dtype="float32"))
    loader = Mock(return_value=(model, {"provider": "CUDAExecutionProvider", "cpu_fallback_disabled": True}))
    decoder = Mock(side_effect=decode)
    probe = Mock(return_value=20)
    monkeypatch.setattr(coverage, "_probe_audio", probe)
    monkeypatch.setattr(coverage, "_decode_gap", decoder)
    monkeypatch.setattr(coverage, "_load_vad", loader)
    return SimpleNamespace(model=model, loader=loader, decoder=decoder, probe=probe)


def assess(value=None, **options):
    return coverage.assess_audio_coverage("local-recording.mp3", value or source(), **options)


def test_all_checked_negative_gaps_clear_only_generic_review_and_preserve_originals(runtime):
    original = source()
    original["quality"] = analyze_transcript(original)
    original["quality"]["recovery"] = {"failed_attempts": 0, "candidates": [], "recovered_segments": 0}
    frozen = deepcopy(original)
    result = assess(original)
    evidence = result["quality"]["audio_coverage"]
    assert original == frozen and result["segments"] == original["segments"]
    assert result["quality"]["requires_review"] is False
    assert result["quality"]["warning"] == original["quality"]["warning"]
    assert result["quality"]["gaps"] == original["quality"]["gaps"]
    assert result["quality"]["recovery"] == original["quality"]["recovery"]
    assert evidence["original_requires_review"] is True
    assert evidence["original_warning"] == original["quality"]["warning"]
    assert evidence["gaps"][0]["status"] == "no_speech_detected"
    assert evidence["gaps"][0]["checked_full_gap"] is True
    assert evidence["options"]["threshold"] == .35 and evidence["options"]["speech_pad_ms"] == 0
    assert evidence["human_listening_verified"] is False
    assert "not proof" in evidence["interpretation"]
    assert runtime.loader.call_count == 1 and runtime.model.call_count == 1


def test_short_leading_and_trailing_edges_are_checked_even_below_gap_threshold(runtime):
    value = source()
    value["segments"][0]["start"] = .25
    value["segments"][1]["end"] = 19.5
    evidence = assess(value)["quality"]["audio_coverage"]
    assert [(gap["start"], gap["end"]) for gap in evidence["gaps"]] == [(0, .25), (4, 14), (19.5, 20)]
    assert all(gap["status"] == "no_speech_detected" for gap in evidence["gaps"])
    assert runtime.model.call_count == 3 and runtime.loader.call_count == 1


def test_negative_vad_cannot_clear_an_incomplete_recovery_budget(runtime):
    value = source()
    value["quality"] = analyze_transcript(value)
    value["quality"]["recovery"] = {"budget_exhausted": True, "failed_attempts": 0,
                                     "candidates": [], "recovered_segments": 0, "attempts": []}
    result = assess(value)
    evidence = result["quality"]["audio_coverage"]
    assert evidence["complete_assessment"] is True
    assert evidence["prior_recovery_budget_exhausted"] is True
    assert evidence["generic_gap_review_cleared"] is False
    assert result["quality"]["requires_review"] is True


def test_no_long_gaps_cannot_hide_words_dropped_during_asr_normalization(runtime):
    value = {"duration": 20, "segments": [{"id": 0, "start": 0, "end": 20, "text": "Represented speech."}],
             "asr_execution": {"timestamp_normalization": {"dropped_words": 1, "dropped_segments": 0}}}
    result = assess(value)
    evidence = result["quality"]["audio_coverage"]
    assert evidence["complete_assessment"] is True
    assert evidence["asr_normalization_dropped_content"] is True
    assert evidence["generic_gap_review_cleared"] is False
    assert result["quality"]["requires_review"] is True


def test_one_positive_frame_keeps_review_and_never_claims_silence(runtime):
    def positive(samples):
        rows = np.zeros(len(samples) // coverage.FRAME_SAMPLES, dtype="float32")
        rows[2] = .35
        return rows
    runtime.model.side_effect = positive
    result = assess()
    gap = result["quality"]["audio_coverage"]["gaps"][0]
    assert gap["status"] == "speech_detected" and gap["chunks"][0]["positive_frames"] == 1
    assert gap["chunks"][0]["speech_spans"] == [{"start": 4.064, "end": 4.096}]
    assert result["quality"]["requires_review"] is True


def test_chunks_overlap_only_inside_the_gap_and_do_not_allocate_more_sessions(runtime):
    evidence = assess(chunk_seconds=4)["quality"]["audio_coverage"]
    crops = [call.args[1:3] for call in runtime.decoder.call_args_list]
    assert crops == [(4, 8.064), (7.936, 12.064), (11.936, 14)]
    assert all(4 <= start < end <= 14 for start, end in crops)
    assert evidence["gaps"][0]["checked_full_gap"] is True
    assert runtime.loader.call_count == 1 and runtime.model.call_count == 3


def test_audio_budget_retains_partial_negative_evidence_and_unchecked_gaps(runtime):
    result = assess(chunk_seconds=4, max_audio_seconds=4.1)
    evidence = result["quality"]["audio_coverage"]
    gap = evidence["gaps"][0]
    assert len(gap["chunks"]) == 1 and gap["status"] == "not_checked"
    assert gap["reason"] == "budget_exhausted" and gap["checked_full_gap"] is False
    assert evidence["budget_exhausted"] is True and evidence["complete_assessment"] is False
    assert result["quality"]["requires_review"] is True


def test_gap_count_budget_marks_every_remaining_gap_not_checked(runtime):
    value = source()
    value["segments"][0]["start"] = .25
    evidence = assess(value, max_gaps=1)["quality"]["audio_coverage"]
    assert [gap["status"] for gap in evidence["gaps"]] == ["no_speech_detected", "not_checked"]
    assert evidence["generic_gap_review_cleared"] is False


def test_wall_budget_after_decode_cannot_be_reported_as_negative_vad(runtime, monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(coverage.time, "monotonic", lambda: clock.now)
    def decode(*args, **options):
        clock.now = .02
        return np.zeros(160000, dtype="float32")
    runtime.decoder.side_effect = decode
    evidence = assess(max_wall_seconds=.01)["quality"]["audio_coverage"]
    assert evidence["gaps"][0]["status"] == "not_checked"
    assert evidence["budget_exhausted"] is True
    runtime.model.assert_not_called()


def test_cancellation_during_decode_returns_unresolved_evidence_and_never_calls_model(runtime):
    runtime.decoder.side_effect = ProcessingCancelled("Cancelled")
    result = assess()
    evidence = result["quality"]["audio_coverage"]
    assert evidence["cancelled"] is True
    assert evidence["gaps"][0]["status"] == "not_checked"
    assert result["quality"]["requires_review"] is True
    runtime.loader.assert_not_called()


def test_failed_cuda_initialization_is_recorded_once_without_fallback(runtime):
    value = source()
    value["segments"][0]["start"] = .25
    runtime.loader.side_effect = ProcessingError("CUDA unavailable")
    evidence = assess(value)["quality"]["audio_coverage"]
    assert [gap["status"] for gap in evidence["gaps"]] == ["failed", "failed"]
    assert runtime.loader.call_count == 1
    runtime.model.assert_not_called()
    assert evidence["generic_gap_review_cleared"] is False


@pytest.mark.parametrize("change", ["recovered_segment", "recovery_candidate", "recovery_failure", "unknown_warning"])
def test_provisional_recovery_failures_and_other_warnings_cannot_be_cleared(runtime, change):
    value = source()
    value["quality"] = analyze_transcript(value)
    if change == "recovered_segment":
        value["segments"][0]["asr_recovered"] = True
    elif change == "recovery_candidate":
        value["quality"]["recovery"] = {"candidates": [{"text": "Provisional words"}]}
    elif change == "recovery_failure":
        value["quality"]["recovery"] = {"failed_attempts": 1}
    else:
        value["quality"]["method"] = "unrelated-review-warning"
    result = assess(value)
    assert result["quality"]["audio_coverage"]["gaps"][0]["status"] == "no_speech_detected"
    assert result["quality"]["requires_review"] is True


def test_tail_only_transcript_is_not_full_episode_readiness_even_with_negative_vad(runtime):
    value = {"duration": 120, "segments": [{"id": 0, "start": 119, "end": 120, "text": "Goodbye."}]}
    runtime.probe.return_value = 120
    result = assess(value, max_audio_seconds=180)
    evidence = result["quality"]["audio_coverage"]
    assert evidence["tail_only_transcript"] is True and evidence["sparse_transcript"] is True
    assert evidence["gaps"][0]["status"] == "no_speech_detected"
    assert evidence["complete_assessment"] is False and result["quality"]["requires_review"] is True


@pytest.mark.parametrize("declaration", [
    {"full_episode_coverage": False},
    {"assessed_audio_scope": {"start": 5, "end": 20}},
    {"assessed_audio_scope": {"start": 0, "end": 18}},
])
def test_declared_partial_scope_cannot_clear_even_with_every_gap_checked_negative(runtime, declaration):
    value = source()
    value["quality"] = {**analyze_transcript(value), **declaration}
    result = assess(value)
    evidence = result["quality"]["audio_coverage"]
    assert evidence["gaps"][0]["status"] == "no_speech_detected"
    assert evidence["gaps"][0]["checked_full_gap"] is True
    assert evidence["sparse_transcript"] is False and evidence["tail_only_transcript"] is False
    assert evidence["declared_partial_scope"] is True
    assert evidence["generic_gap_review_cleared"] is False
    assert result["quality"]["requires_review"] is True


def test_duration_mismatch_and_invalid_gap_metadata_prevent_automatic_readiness(runtime):
    runtime.probe.return_value = 21
    result = assess()
    assert result["quality"]["audio_coverage"]["duration_status"] == "mismatch"
    assert result["quality"]["requires_review"] is True
    runtime.model.assert_not_called()
    runtime.probe.return_value = 20
    value = source()
    value["quality"] = {**analyze_transcript(value), "gaps": [{"start": -1, "end": 5}]}
    evidence = assess(value)["quality"]["audio_coverage"]
    assert evidence["valid_gap_metadata"] is False and evidence["generic_gap_review_cleared"] is False


def test_cuda_loader_configures_factory_before_using_shared_model_and_rejects_cpu(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(gpu, "configure_cuda_vad", lambda: calls.append("configure"))
    session = SimpleNamespace(get_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
                              get_session_options=lambda: SimpleNamespace(get_session_config_entry=lambda name: "1"))
    model = SimpleNamespace(session=session)
    monkeypatch.setattr(gpu, "cuda_vad_model", lambda: calls.append("cached_model") or model)
    whisper = ModuleType("faster_whisper")
    whisper.__path__ = []
    utils = ModuleType("faster_whisper.utils")
    utils.get_assets_path = lambda: str(tmp_path)
    whisper.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    (tmp_path / "silero_vad_v6.onnx").write_bytes(b"mock ONNX artifact; not a model")
    selected, evidence = coverage._load_vad()
    assert selected is model and calls == ["configure", "cached_model"]
    assert evidence["provider"] == "CUDAExecutionProvider" and evidence["cpu_fallback_disabled"] is True
    assert len(evidence["model_sha256"]) == 64
    session.get_providers = lambda: ["CPUExecutionProvider"]
    with pytest.raises(ProcessingError, match="CUDA"):
        coverage._load_vad()
    session.get_providers = lambda: ["CUDAExecutionProvider"]
    session.get_session_options = lambda: SimpleNamespace(get_session_config_entry=lambda name: "0")
    with pytest.raises(ProcessingError, match="CPU fallback disabled"):
        coverage._load_vad()


def test_decoder_rejects_incomplete_samples_without_using_any_model(monkeypatch):
    monkeypatch.setattr(coverage, "_binary", lambda name: name)
    monkeypatch.setattr(coverage, "_native_output", lambda *args, **kwargs: b"\0" * 40)
    with pytest.raises(ProcessingError, match="cover"):
        coverage._decode_gap("local.mp3", 4, 5, timeout=1, should_cancel=None)


def test_native_decode_cancellation_reaps_only_its_owned_child(monkeypatch):
    child = SimpleNamespace(poll=lambda: None)
    created, stopped = [], []
    monkeypatch.setattr(coverage.subprocess, "Popen", lambda *args, **kwargs: created.append((args, kwargs)) or child)
    monkeypatch.setattr(coverage, "_stop_process", lambda process: stopped.append(process))
    checks = iter([False, True])
    with pytest.raises(ProcessingCancelled):
        coverage._native_output(["ffmpeg", "local.mp3"], limit=100, timeout=1, should_cancel=lambda: next(checks))
    assert len(created) == 1 and stopped == [child]


def test_single_sample_edge_cannot_be_certified_without_inference(runtime):
    value = source()
    value["segments"][1]["end"] = 20 - 1 / coverage.SAMPLE_RATE
    evidence = assess(value)["quality"]["audio_coverage"]
    assert len(evidence["gaps"]) == 2
    assert runtime.model.call_count == 2
    assert evidence["gaps"][-1]["chunks"][0]["decoded_samples"] == 1
