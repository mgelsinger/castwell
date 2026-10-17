import json
from pathlib import Path

import pytest

from castwell.ad_scope import REMOVAL_SCOPE
from castwell import speed_benchmark as bench


def test_budget_uses_total_wall_time_and_rejects_cached_or_incomplete_claims():
    result = bench.budget_result(4500, 375, complete=True, uncached=True)
    assert result["observed_seconds_per_audio_hour"] == 300
    assert result["timing_target_met"] is True
    assert not bench.budget_result(4500, 375.001, complete=True, uncached=True)["timing_target_met"]
    assert not bench.budget_result(4500, 10, complete=True, uncached=False)["timing_target_met"]
    assert not bench.budget_result(4500, 10, complete=False, uncached=True)["timing_target_met"]
    with pytest.raises(ValueError):
        bench.budget_result(0, 10, complete=True, uncached=True)


def test_word_score_unions_cuts_preserves_partial_and_zero_duration_errors():
    reference = {"duration": 10, "words": [
        {"category": "commercial", "word": "ad", "start": 0, "end": 2, "global_word_index": 0},
        {"category": "commercial", "word": "point", "start": 3, "end": 3, "global_word_index": 1},
        {"category": "protected", "word": "show", "start": 4, "end": 6, "global_word_index": 2},
        {"category": "protected", "word": "excluded", "start": 7, "end": 8, "global_word_index": 3, "scored": False},
    ]}
    result = bench.score_words([{"start": 0, "end": 1}, {"start": .5, "end": 3},
                                {"start": 5, "end": 8}], reference)
    assert result["categories"]["commercial"]["word_seconds_selected"] == 2
    assert result["categories"]["commercial"]["zero_duration_points_selected"] == 0
    assert result["categories"]["protected"]["word_seconds_selected"] == 1
    assert result["categories"]["protected"]["word_entries"] == 1
    assert result["errors"]["commercial_missed"][0]["global_word_index"] == 1
    assert result["errors"]["protected_selected"][0]["global_word_index"] == 2
    assert not result["zero_reference_word_errors"]
    assert not result["acoustic_accuracy_verified"]


@pytest.fixture
def reference_files(tmp_path):
    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"synthetic fixture bytes, not real audio")
    transcript = {"duration": 3600, "language": "en", "segments": [
        {"id": 0, "start": 0, "end": 2, "text": "ad show", "words": [
            {"word": "ad", "start": 0, "end": 1}, {"word": "show", "start": 1, "end": 2}]}]}
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript))
    value = {"detector_predictions_seen": True, "acceptance_eligible": False,
             "removal_scope": REMOVAL_SCOPE, "source_sha256": bench.sha256(audio),
             "transcript": str(transcript_path), "transcript_sha256": bench.sha256(transcript_path),
             "audio_seconds": 3600,
             "commercial_units": [{"id": "ad", "global_word_index_range_inclusive": [0, 0]}],
             "protected": [{"id": "show", "global_word_index_range_inclusive": [1, 1]}],
             "ambiguous_promotional_units": []}
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps(value))
    return audio, transcript_path, reference, value


def test_reference_adapter_keeps_consumption_and_rejects_changed_inputs(reference_files):
    audio, transcript, path, raw = reference_files
    result = bench.load_consumed_reference(path, audio)
    assert result["provenance"]["consumed_development"] is True
    assert result["provenance"]["acceptance_eligible"] is False
    assert [row["category"] for row in result["words"]] == ["commercial", "protected"]
    assert path.read_text() == json.dumps(raw)
    raw["detector_predictions_seen"] = False
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="consumed"):
        bench.load_consumed_reference(path, audio)
    raw["detector_predictions_seen"] = True
    path.write_text(json.dumps(raw))
    transcript.write_text("{}")
    with pytest.raises(ValueError, match="transcript changed"):
        bench.load_consumed_reference(path, audio)


def test_request_telemetry_does_not_turn_unknown_usage_into_zero():
    telemetry = bench.RequestTelemetry()
    telemetry.record_request(elapsed_seconds=2, succeeded=False)
    telemetry.record_request(elapsed_seconds=1, succeeded=True, input_tokens=10, output_tokens=4, cached=True)
    assert telemetry.summary() == {"requests": 2, "failures": 1, "cache_hits": 1, "paid_requests": 0,
        "request_wall_seconds_sum": 3, "token_usage_complete": False, "known_input_tokens": 10, "known_output_tokens": 4}


@pytest.mark.parametrize("cached,preview,model_cold,expected", [
    (False, False, True, True), (True, False, True, False), (False, True, False, False)])
def test_runner_times_setup_and_renders_only_explicit_view(reference_files, monkeypatch, tmp_path,
                                                         cached, preview, model_cold, expected):
    audio, transcript_path, reference, _ = reference_files
    now = [0.]
    monkeypatch.setattr(bench.processing, "probe_duration", lambda path: 3600)
    cuts_seen = []
    class Adapter:
        def prepare_episode(self, audio):
            now[0] += 5
            return {"waveform_created": True, "database_scope": "isolated_fixture"}
        def prepare(self):
            now[0] += 20
        def transcribe(self, path):
            now[0] += 100
            return json.loads(transcript_path.read_text())
        def detect(self, transcript):
            now[0] += 50
            return [{"start": 0, "end": 1, "approved": False, "confidence": .9,
                     "reason": "fixture", "source": "ai"}]
        def render(self, source, output, cuts):
            cuts_seen.extend(cuts)
            now[0] += 30
            output.write_bytes(b"output fixture")
            return {"duration": 3599 if preview else 3600}
        def evidence(self):
            return {"transcript_cache_hits": int(cached), "prediction_cache_hits": 0,
                    "models_cold_at_start": model_cold, "cuda_only_verified": True,
                    "asset_downloads": 0, "request_telemetry_complete": True}
        def close(self):
            now[0] += 1000
    def factory(context):
        now[0] += 10
        assert "reference" not in context
        context["telemetry"].record_request(elapsed_seconds=50, succeeded=True, input_tokens=3, output_tokens=2)
        return Adapter()
    report = bench.run_benchmark(audio=audio, reference=reference, configuration={}, output_dir=tmp_path / "result",
        adapter_factory=factory, transcript_path=transcript_path if cached else None,
        candidate_preview=preview, clock=lambda: now[0])
    assert report["elapsed_seconds"] == (115 if cached else 215)
    assert report["phases"]["episode_preparation"]["seconds"] == 5
    assert report["episode_preparation_included"] is True
    assert report["phases"]["cleanup_excluded"]["seconds"] == 1000
    assert report["cold_cuda_timing_target_met"] is expected
    assert cuts_seen[0]["approved"] is preview
    assert report["scores"]["candidates"]["zero_reference_word_errors"]
    assert report["scores"]["rendered"]["zero_reference_word_errors"] is preview
    assert report["production_accuracy_passed"] is False
    assert report["original_preserved"] is True


def test_failed_pipeline_keeps_failure_report_and_closes_adapter(reference_files, monkeypatch, tmp_path):
    audio, _, reference, _ = reference_files
    monkeypatch.setattr(bench.processing, "probe_duration", lambda path: 3600)
    closed = []
    class Adapter:
        def prepare(self):
            raise RuntimeError("model could not load")
        def evidence(self):
            return {}
        def close(self):
            closed.append(True)
    output = tmp_path / "result"
    report = bench.run_benchmark(audio=audio, reference=reference, configuration={}, output_dir=output,
                                adapter_factory=lambda context: Adapter())
    assert report["status"] == "failed"
    assert report["phases"]["model_prepare"]["complete"] is False
    assert report["cold_cuda_timing_target_met"] is False
    assert report["error"]["message"] == "model could not load"
    assert "scores" not in report and closed == [True]
    assert json.loads((output / "report.private.json").read_text())["status"] == "failed"
    with pytest.raises(ValueError, match="must be new"):
        bench.run_benchmark(audio=audio, reference=reference, configuration={}, output_dir=output,
                            adapter_factory=lambda context: Adapter())


def test_credentials_rejected_before_output_or_inference(reference_files, tmp_path):
    audio, _, reference, _ = reference_files
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="credentials"):
        bench.run_benchmark(audio=audio, reference=reference, configuration={"nested": {"ai_key": "private"}},
                            output_dir=output, adapter_factory=lambda context: pytest.fail("must not start"))
    assert not output.exists()
