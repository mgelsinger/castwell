import json
from pathlib import Path

import pytest

from castwell import recording_evaluation as evaluation


def test_partial_annotation_scoring_masks_uncertainty_and_does_not_invent_editorial():
    reference = {
        "commercial_units": [{"start": 2, "end": 6}],
        "protected_units": [{"start": 8, "end": 12}],
        "unverified_intervals": [{"start": 4, "end": 5}, {"start": 10, "end": 11}],
    }
    selected = [{"start": 3, "end": 5.5}, {"start": 9, "end": 11.5}, {"start": 15, "end": 17}]
    result = evaluation.score_intervals(selected, reference, 20)
    assert result["annotated_commercial_seconds"] == 3
    assert result["annotated_protected_seconds"] == 3
    assert result["commercial_seconds_selected"] == 1.5
    assert result["commercial_seconds_missed"] == 1.5
    assert result["protected_seconds_selected"] == 1.5
    assert result["unverified_seconds_selected"] == 2
    assert result["unlabeled_seconds_selected"] == 2
    assert result["commercial_units"][0]["coverage"] == .5
    assert result["commercial_units_any_selected"] == 1
    assert result["commercial_units_fully_selected"] == 0


def test_candidate_verified_and_approved_views_are_distinct():
    reference = {"commercial_units": [{"start": 0, "end": 8}], "protected_units": [{"start": 8, "end": 10}]}
    cuts = [
        {"start": 0, "end": 4, "label": "commercial", "confidence": .99, "requires_review": False, "approved": False},
        {"start": 4, "end": 10, "label": "uncertain", "confidence": .99, "requires_review": True, "approved": False},
    ]
    result = evaluation.score_cuts(cuts, reference, 10)
    assert result["candidates"]["protected_seconds_selected"] == 2
    assert result["verified"]["commercial_seconds_selected"] == 4
    assert result["verified"]["commercial_units_fully_selected"] == 0
    assert result["approved"]["selected_seconds"] == 0
    assert result["approved"]["commercial_seconds_missed"] == 8


def test_conflicting_reference_intervals_fail():
    with pytest.raises(ValueError, match="overlap"):
        evaluation.annotation_intervals({"commercial_units": [{"start": 0, "end": 5}],
                                         "protected_units": [{"start": 4, "end": 8}]}, 10)


@pytest.fixture
def recording(tmp_path):
    audio = tmp_path / "show.first-10m.wav"
    audio.write_bytes(b"synthetic audio identity only; no decoder or model invoked")
    source = tmp_path / "show.mp3"
    source.write_bytes(b"synthetic full recording identity")
    transcript = tmp_path / "show.first-10m.transcript.json"
    transcript.write_text(json.dumps({"language": "en", "duration": 10,
        "segments": [{"id": 0, "start": 0, "end": 10, "text": "PRIVATE TRANSCRIPT SENTINEL"}]}))
    reference = tmp_path / "show.first-10m.reference-v3.json"
    reference.write_text(json.dumps({"detector_predictions_seen": False,
        "transcript": str(transcript), "transcript_sha256": evaluation.sha256(transcript),
        "commercial_units": [{"start": 0, "end": 5, "reason": "PRIVATE ANNOTATION SENTINEL"}],
        "protected_units": [{"start": 5, "end": 10}], "unverified_intervals": []}))
    (tmp_path / "source-manifest.json").write_text(json.dumps({"clips": [{"path": str(audio),
        "sha256": evaluation.sha256(audio), "source_offset_seconds": 0}]}))
    (tmp_path / "show.metadata.json").write_text(json.dumps({"local_path": str(source),
        "sha256": evaluation.sha256(source), "episode_page": "https://publisher.invalid/episode",
        "downloaded_at_utc": "2026-10-07T00:00:00+00:00"}))
    model = tmp_path / "model.gguf"
    model.write_bytes(b"mock artifact; never loaded")
    return {"references": [reference], "output": tmp_path / "private.json",
            "summary_output": tmp_path / "summary.json", "base_url": "http://127.0.0.1:8081/v1",
            "model": "mock", "model_label": "test artifact", "model_files": [model]}


def test_checkpoint_and_summary_exclude_private_text(recording):
    calls = []
    def detector(transcript, method, *, config, progress):
        calls.append(config)
        return [{"start": 0, "end": 5, "confidence": .99, "approved": False,
                 "requires_review": False, "label": "commercial", "source": "ai",
                 "reason": "PRIVATE MODEL EVIDENCE SENTINEL"}]
    result = evaluation.evaluate_recordings(**recording, detector=detector)
    assert result["summary"]["successful_clips"] == 1
    assert calls[0]["review_only"] is True
    assert calls[0]["ai_key"] == ""
    assert calls[0]["ai_allow_redirects"] is False
    assert calls[0]["ai_trust_env"] is False
    private = recording["output"].read_text()
    public = recording["summary_output"].read_text()
    assert "PRIVATE MODEL EVIDENCE SENTINEL" in private
    assert "PRIVATE" not in public
    assert "TRANSCRIPT SENTINEL" not in private
    evaluation.evaluate_recordings(**recording, detector=lambda *args, **kwargs: pytest.fail("resume reran finished clip"), resume=True)


def test_failures_remain_in_eligible_denominators_without_error_text_leak(recording):
    def detector(*args, **kwargs):
        raise RuntimeError("PRIVATE FAILURE EVIDENCE")
    result = evaluation.evaluate_recordings(**recording, detector=detector)
    summary = result["summary"]
    assert summary["failed_clips"] == 1
    assert summary["eligible_commercial_seconds_including_failed_and_unfinished"] == 5
    assert summary["views"]["candidates"]["annotated_commercial_seconds"] == 0
    assert "PRIVATE FAILURE" in recording["output"].read_text()
    assert "PRIVATE" not in recording["summary_output"].read_text()


def test_remote_endpoint_rejected_before_any_work(recording):
    recording["base_url"] = "https://paid-provider.invalid/v1"
    with pytest.raises(ValueError, match="literal"):
        evaluation.evaluate_recordings(**recording, detector=lambda *args, **kwargs: pytest.fail("network called"))


def test_changed_transcript_is_rejected(recording):
    reference = json.loads(recording["references"][0].read_text())
    Path(reference["transcript"]).write_text("{}")
    with pytest.raises(ValueError, match="hash changed"):
        evaluation.evaluate_recordings(**recording)


def test_frozen_candidate_hash_mismatch_rejected_before_inference(recording, tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"source_sha256": {"processing.py": "0" * 64}}))
    with pytest.raises(ValueError, match="frozen candidate"):
        evaluation.evaluate_recordings(**recording, candidate_config=candidate,
                                      detector=lambda *args, **kwargs: pytest.fail("detector called"))


def test_reasoning_profile_is_recorded_and_cannot_change_on_resume(recording):
    from castwell.ad_review import inference_settings
    calls = []
    def detector(transcript, method, *, config, progress):
        calls.append(config)
        return []
    result = evaluation.evaluate_recordings(**recording, detector=detector, ai_reasoning=True)
    assert calls[0]["ai_reasoning"] is True
    assert result["configuration"]["request_profile"] == inference_settings(True)
    assert result["configuration"]["review_only"] is True
    with pytest.raises(ValueError, match="Checkpoint inputs or candidate changed"):
        evaluation.evaluate_recordings(**recording, detector=detector, ai_reasoning=False, resume=True)
    assert len(calls) == 1


def test_frozen_reasoning_profile_mismatch_rejected_before_inference(recording, tmp_path):
    from castwell.ad_review import inference_settings
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"detector_configuration": {"request_profile": inference_settings(True)}}))
    with pytest.raises(ValueError, match="Inference profile differs"):
        evaluation.evaluate_recordings(**recording, candidate_config=candidate,
                                      detector=lambda *args, **kwargs: pytest.fail("detector called"))


def test_public_url_strips_private_query_and_rejects_credentials():
    assert evaluation._public_url("https://publisher.invalid/episode?token=PRIVATE#PRIVATE") == "https://publisher.invalid/episode"
    assert evaluation._public_url("https://person:PRIVATE@publisher.invalid/episode") is None


def test_kev_calls_only_local_adapter_and_keeps_typed_details_private(recording, monkeypatch, tmp_path):
    from castwell import jev
    calls = []
    monkeypatch.setenv("CASTWELL_AI_KEY", "PRIVATE HOSTED KEY")
    monkeypatch.setenv("JEV_API_KEY", "PRIVATE PAID KEY")
    monkeypatch.setattr(jev, "classify_transcript", lambda *args, **kwargs: pytest.fail("paid adapter invoked"))
    monkeypatch.setattr(evaluation.processing, "detect_ads", lambda *args, **kwargs: pytest.fail("verified adapter invoked"))
    def local_adapter(transcript, **kwargs):
        calls.append(kwargs)
        return {
            "cuts": [{"start": 0, "end": 5, "confidence": .75, "selected_probability": .94,
                      "requires_review": False, "approved": False, "source": "kev-local",
                      "label": "commercial", "reason": "PRIVATE KEV CUT REASON"}],
            "provider": "kev-local", "model": "PRIVATE PROVIDER ALIAS",
            "decisions": [{"segment_id": 0, "choice": "commercial", "selected_probability": .94,
                           "confidence": .75, "evidence": "PRIVATE TYPED DECISION"}],
            "confidence_semantics": {"confidence": "normalized_probability_margin", "review_score": "selected_probability"},
            "usage": {"input_tokens": 42, "output_tokens": 0}, "request_count": 1,
        }
    monkeypatch.setattr(jev, "classify_local_transcript", local_adapter)
    for name in ("kev-head.safetensors", "base.safetensors"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        recording["model_files"].append(path)
    recording["base_url"] = "http://127.0.0.1:8083"
    result = evaluation.evaluate_recordings(**recording, backend="kev")
    assert calls == [{"base_url": "http://127.0.0.1:8083", "model": "mock"}]
    assert result["configuration"]["view_availability"]["verified"] is False
    assert result["configuration"]["confidence_semantics"]["review_score"] == "selected_probability"
    assert result["configuration"]["auto_approve_threshold"] is None
    assert result["summary"]["views"]["candidates"]["commercial_seconds_selected"] == 5
    assert result["summary"]["views"]["approved"]["selected_seconds"] == 0
    assert result["summary"]["views"]["verified"] is None
    assert result["clips"][0]["metrics"]["verified"] is None
    assert len(result["provenance"]["model_artifacts"]) == 3
    private = json.loads(recording["output"].read_text())
    assert private["clips"][0]["provider_result"]["decisions"][0]["selected_probability"] == .94
    assert "PRIVATE TYPED DECISION" in recording["output"].read_text()
    assert "PRIVATE" not in recording["summary_output"].read_text()


@pytest.mark.parametrize("base_url", ["https://paid-provider.invalid", "http://127.0.0.1:8083/not-the-api"])
def test_kev_rejects_remote_and_unsupported_paths_before_invoking_adapter(recording, base_url):
    recording["base_url"] = base_url
    with pytest.raises(ValueError):
        evaluation.evaluate_recordings(**recording, backend="kev",
                                      detector=lambda *args, **kwargs: pytest.fail("adapter invoked"))


@pytest.mark.parametrize("backend,expected_url", [("verified-ai", "http://127.0.0.1:8081/v1"), ("kev", "http://127.0.0.1:8083")])
def test_cli_selects_backend_specific_default_endpoint(recording, monkeypatch, backend, expected_url):
    import runpy
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return {"summary": {"successful_clips": 1, "failed_clips": 0, "unfinished_clips": 0}}
    monkeypatch.setattr(evaluation, "evaluate_recordings", run)
    main = runpy.run_path(str(evaluation.ROOT / "scripts/evaluate_recordings.py"))["main"]
    args = ["--reference", str(recording["references"][0]), "--output", str(recording["output"]),
            "--summary-output", str(recording["summary_output"]), "--model", "mock", "--model-label", "mock exact",
            "--model-file", str(recording["model_files"][0])]
    if backend == "kev":
        args.extend(["--backend", "kev"])
    assert main(args) == 0
    assert calls[0]["backend"] == backend
    assert calls[0]["base_url"] == expected_url
