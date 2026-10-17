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
    reference.write_text(json.dumps({"detector_predictions_seen": False, "removal_scope": evaluation.REMOVAL_SCOPE,
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
    candidate.write_text(json.dumps({"removal_scope": evaluation.REMOVAL_SCOPE,
                                     "source_sha256": {"processing.py": "0" * 64}}))
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
    candidate.write_text(json.dumps({"detector_configuration": {"removal_scope": evaluation.REMOVAL_SCOPE,
                                     "request_profile": inference_settings(True)}}))
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


@pytest.mark.parametrize("backend,expected_url", [("verified-ai", "http://127.0.0.1:8081/v1"),
    ("scene-ai", "http://127.0.0.1:8081/v1"), ("kev", "http://127.0.0.1:8083")])
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
    if backend != "verified-ai":
        args.extend(["--backend", backend])
    assert main(args) == 0
    assert calls[0]["backend"] == backend
    assert calls[0]["base_url"] == expected_url


def install_scene_recording(recording, monkeypatch, *, all_editorial=False, unresolved=False):
    """Actual processing/composition with invented stage responses, no inference."""
    from copy import deepcopy
    import requests
    from castwell import scene_review
    reference = json.loads(recording["references"][0].read_text())
    transcript_path = Path(reference["transcript"])
    segments = [{"id": i, "start": i * 5, "end": (i + 1) * 5,
        "text": f"PRIVATE unit {i}.", "words": [{"word": f"PRIVATE unit {i}.",
        "start": i * 5, "end": (i + 1) * 5, "probability": .99}]} for i in range(2)]
    transcript_path.write_text(json.dumps({"duration": 10, "language": "en", "segments": segments}))
    reference.update(transcript_sha256=evaluation.sha256(transcript_path), human_listening_verified=True,
        payment_verified=False, verification_provenance={"reviewer": "PRIVATE reviewer declaration"})
    if all_editorial:
        reference.update(commercial_units=[], protected_units=[{"start": 0, "end": 10}])
    recording["references"][0].write_text(json.dumps(reference))
    calls = []
    def first_stage(source, **options):
        audit = {}
        for unit in source["segments"]:
            identifier = unit["id"]
            commercial = identifier == 0 and not all_editorial
            decision = {"id": identifier, "label": "commercial" if commercial else "editorial",
                "confidence": .99, "reason": "PRIVATE scene evidence", "evidence_id": identifier,
                "evidence": unit["text"], "source_range": {"first_id": identifier, "last_id": identifier}}
            boundary = deepcopy(decision) if commercial or unresolved else None
            if unresolved and identifier == 1:
                boundary["confidence"] = .8
            audit[identifier] = {"segment_id": identifier, "parent_segment_id": identifier,
                "intent": decision, "boundary": boundary, "boundary_audited": boundary is not None}
        options["decision_sink"](audit)
        if all_editorial:
            return []
        return [{"start": 0, "end": 5, "label": "commercial", "confidence": .99,
            "requires_review": True, "approved": False, "source": "ai", "verification": [audit[0]]}]
    class Response:
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"decisions": [{
                "id": 0, "label": "commercial", "role": "commercial_speech", "evidence_id": 0,
                "reason": "PRIVATE endpoint evidence"}]})}}]}
        def close(self):
            pass
    def request(url, headers, payload, *args, **options):
        calls.append((url, headers, payload, options))
        return Response()
    monkeypatch.setattr(scene_review, "classify_scenes", first_stage)
    monkeypatch.setattr(evaluation.processing, "_classifier_request", request)
    monkeypatch.setattr(requests.Session, "request", lambda *args, **kwargs: pytest.fail("network attempted"))
    return calls


def test_scene_routes_actual_dispatch_and_records_complete_private_audits(recording, monkeypatch):
    from castwell import scene_review, scene_edges, scene_pipeline
    calls = install_scene_recording(recording, monkeypatch)
    monkeypatch.setenv("CASTWELL_AI_KEY", "PRIVATE ignored credential")
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai")
    assert result["summary"]["successful_clips"] == 1
    config = result["configuration"]
    assert config["backend"] == "scene-ai" and config["review_only"] is True
    assert config["request_profiles"] == {"intent": scene_review.inference_settings(False),
        "boundaries": scene_review.boundary_inference_settings(), "endpoints": scene_edges.inference_settings()}
    assert config["policy_versions"]["composition"] == scene_pipeline.POLICY_VERSION
    assert config["confidence_semantics"]["endpoint_confidence"] == "unscored_role_judgment"
    assert config["confidence_semantics"]["review_score"] == "composition_eligibility_with_global_unresolved_gate"
    assert "castwell/scene_pipeline.py" in result["provenance"]["source_sha256"]
    assert config["window_chars"] == 9000 and config["requested_window_chars"] == 18000
    assert calls[0][1] == {"Content-Type": "application/json"}
    assert calls[0][3] == {"allow_redirects": False, "trust_env": False}
    assert result["summary"]["views"]["approved"]["selected_seconds"] == 0
    assert result["summary"]["views"]["verified"]["commercial_seconds_selected"] == 5
    private = json.loads(recording["output"].read_text())
    row = private["clips"][0]
    assert row["scene_audit_complete"] and len(row["scene_audit"]) == 2 and len(row["endpoint_audit"]) == 1
    assert row["approval_audit"]["scene_audit"] == row["scene_audit"]
    assert row["reference_verification_provenance"] == {"reviewer": "PRIVATE reviewer declaration"}
    declared = result["clips"][0]["provenance"]["reference_verification"]
    assert declared["human_listening_verified"] and declared["human_listening_verified_status"] == "declared_verified"
    assert declared["payment_verified_status"] == "declared_unverified"
    assert "PRIVATE" not in recording["summary_output"].read_text()
    evaluation.evaluate_recordings(**recording, backend="scene-ai", resume=True,
        detector=lambda *args, **kwargs: pytest.fail("completed scene checkpoint reran"))


def test_scene_complete_editorial_audit_can_return_no_cuts(recording, monkeypatch):
    calls = install_scene_recording(recording, monkeypatch, all_editorial=True)
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai")
    assert result["summary"]["successful_clips"] == 1 and calls == []
    assert result["clips"][0]["scene_audit_complete"] is True
    assert result["clips"][0]["scene_unit_decisions"] == 2
    assert result["clips"][0]["endpoint_unit_decisions"] == 0
    private = json.loads(recording["output"].read_text())["clips"][0]
    assert private["cuts"] == [] and private["endpoint_audit"] == {}
    assert private["approval_audit"]["cut_approvals"] == []
    assert result["summary"]["views"]["candidates"]["commercial_seconds_missed"] == 0


def test_scene_unresolved_global_judgment_retains_completed_audit_and_blocks_verified_view(recording, monkeypatch):
    install_scene_recording(recording, monkeypatch, unresolved=True)
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai")
    assert result["summary"]["successful_clips"] == 1
    assert result["clips"][0]["scene_audit_complete"] is True
    assert result["clips"][0]["unresolved_unit_count"] == 1
    assert result["summary"]["views"]["candidates"]["commercial_seconds_selected"] == 5
    assert result["summary"]["views"]["verified"]["selected_seconds"] == 0
    assert result["summary"]["views"]["approved"]["selected_seconds"] == 0
    private = json.loads(recording["output"].read_text())["clips"][0]
    assert private["approval_audit"]["unresolved_unit_ids"] == [1]
    assert private["cuts"][0]["approval_metadata"]["eligible"] is True
    assert private["cuts"][0]["approval_metadata"]["global_requires_review"] is True


@pytest.mark.parametrize("partial", [False, True])
def test_scene_missing_or_failed_audit_is_not_a_successful_no_ad_result(recording, monkeypatch, partial):
    from castwell import scene_pipeline
    install_scene_recording(recording, monkeypatch)
    def broken(source, **options):
        if partial:
            options["decision_sink"]({0: {"segment_id": 0, "private": "PRIVATE partial judgment"}})
            raise evaluation.processing.ProcessingError("PRIVATE failed endpoint stage")
        return []
    monkeypatch.setattr(scene_pipeline, "classify_scene_pipeline", broken)
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai")
    assert result["summary"]["failed_clips"] == 1 and result["summary"]["successful_clips"] == 0
    assert result["summary"]["eligible_commercial_seconds_including_failed_and_unfinished"] == 5
    assert result["clips"][0]["scene_audit_complete"] is False
    private = json.loads(recording["output"].read_text())["clips"][0]
    assert private["metrics"] is None
    assert (private["scene_audit"] is not None) == partial
    assert "PRIVATE" not in recording["summary_output"].read_text()


def test_scene_evaluation_rejects_automatic_approval(recording, monkeypatch):
    from castwell import scene_pipeline
    install_scene_recording(recording, monkeypatch)
    original = scene_pipeline.classify_scene_pipeline
    def approve(source, **options):
        cuts = original(source, **options)
        cuts[0]["approved"] = True
        return cuts
    monkeypatch.setattr(scene_pipeline, "classify_scene_pipeline", approve)
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai")
    assert result["summary"]["failed_clips"] == 1
    assert result["summary"]["views"]["approved"]["selected_seconds"] == 0


@pytest.mark.parametrize("name,value", [("ai_model", "different-model"),
    ("ai_base_url", "http://127.0.0.1:8082/v1"), ("auto_approve_threshold", .5)])
def test_scene_dispatch_cannot_silently_change_recorded_identity(recording, monkeypatch, name, value):
    calls = install_scene_recording(recording, monkeypatch)
    def altered(source, method, *, config, progress):
        config = {**config, name: value}
        return evaluation.processing.detect_ads(source, method, config=config, progress=progress)
    result = evaluation.evaluate_recordings(**recording, backend="scene-ai", detector=altered)
    assert result["summary"]["failed_clips"] == 1 and calls == []
    assert result["clips"][0]["scene_audit_complete"] is False


def test_reference_verification_missing_stays_unknown_and_invalid_declaration_fails(recording):
    result = evaluation.evaluate_recordings(**recording, detector=lambda *args, **kwargs: [])
    declared = result["clips"][0]["provenance"]["reference_verification"]
    assert declared == {"human_listening_verified": False, "human_listening_verified_status": "unknown",
        "payment_verified": False, "payment_verified_status": "unknown"}
    reference = json.loads(recording["references"][0].read_text())
    reference["human_listening_verified"] = "yes"
    recording["references"][0].write_text(json.dumps(reference))
    with pytest.raises(ValueError, match="booleans"):
        evaluation.evaluate_recordings(**recording, resume=True)


def test_undeclared_reference_scope_remains_unknown_without_current_scope_acceptance(recording):
    path = recording["references"][0]
    reference = json.loads(path.read_text())
    reference.pop("removal_scope")
    path.write_text(json.dumps(reference))
    result = evaluation.evaluate_recordings(**recording, detector=lambda *args, **kwargs: [])
    assert result["configuration"]["removal_scope"] == evaluation.REMOVAL_SCOPE
    assert result["clips"][0]["provenance"]["reference_scope_status"] == "undeclared"
    assert result["clips"][0]["provenance"]["reference_scope_matches_current_policy"] is False
    assert any("not acceptance evidence" in value for value in result["limitations"])


@pytest.mark.parametrize("scope", ["all-promotions", "sponsor-product-reads-only-v1", True, {"scope": "sponsor-product"}])
def test_mismatched_reference_scope_fails_before_classification(recording, scope):
    reference = json.loads(recording["references"][0].read_text())
    reference["removal_scope"] = scope
    recording["references"][0].write_text(json.dumps(reference))
    with pytest.raises(ValueError, match="Reference removal scope"):
        evaluation.evaluate_recordings(**recording,
            detector=lambda *args, **kwargs: pytest.fail("mismatched scope reached classifier"))
    assert not recording["output"].exists()


def test_matching_reference_scope_is_recorded_without_changing_labels(recording):
    path = recording["references"][0]
    reference = json.loads(path.read_text())
    reference["removal_scope"] = evaluation.REMOVAL_SCOPE
    path.write_text(json.dumps(reference))
    before = path.read_bytes()
    result = evaluation.evaluate_recordings(**recording, detector=lambda *args, **kwargs: [])
    assert result["configuration"]["removal_scope"] == evaluation.REMOVAL_SCOPE
    assert "castwell/ad_scope.py" in result["provenance"]["source_sha256"]
    assert result["clips"][0]["provenance"]["reference_scope_status"] == "matches_current_policy"
    assert result["clips"][0]["provenance"]["reference_scope_matches_current_policy"] is True
    assert path.read_bytes() == before


def test_frozen_candidate_scope_mismatch_fails_before_classification(recording, tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"detector_configuration": {"removal_scope": "all-promotions"}}))
    with pytest.raises(ValueError, match="Removal scope differs from frozen"):
        evaluation.evaluate_recordings(**recording, candidate_config=candidate,
            detector=lambda *args, **kwargs: pytest.fail("candidate scope mismatch reached classifier"))
    assert not recording["output"].exists()
