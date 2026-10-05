"""Metric semantics and network guards; no external or paid model calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from castwell.evaluation import evaluate_dataset, evaluate_file, interval_metrics, validate_endpoint


def span(start, end, **kwargs):
    return {"start": start, "end": end, **kwargs}


def case(identifier="example", *, category="straightad", expected=None, expect_review=False):
    return {
        "id": identifier, "split": "eval", "group": "synthetic-" + identifier, "category": category,
        "description": "Synthetic evaluator fixture", "expect_review": expect_review,
        "transcript": {"language": "en", "duration": 10, "segments": [
            {"id": 0, "start": 0, "end": 10, "text": "A test segment."}]},
        "expected_intervals": [span(2, 8)] if expected is None else expected,
    }


def dataset(*cases):
    return {"schema_version": 1, "scope": "Synthetic tests, not an accuracy benchmark", "cases": list(cases or [case()])}


def classify(cuts=(), review=False):
    return {"cuts": list(cuts), "review": review}


def test_union_duration_errors_and_matched_boundaries():
    scored = interval_metrics([span(5, 15), span(12, 22), span(35, 37)], [span(10, 20), span(30, 40)], 50)
    assert scored["editorial_seconds_wrongly_cut"] == 7
    assert scored["ad_seconds_missed"] == 8
    assert scored["ad_seconds_selected"] == 12
    assert scored["total_ad_seconds"] == 20
    assert scored["selected_seconds"] == 19
    assert scored["matched_spans"] == 2
    assert scored["mean_boundary_error_seconds"] == 3.75
    assert scored["exact_match"] is False


def test_empty_truth_and_unmatched_spans_have_no_fabricated_boundary_error():
    scored = interval_metrics([span(1, 3)], [], 10)
    assert scored["editorial_seconds_wrongly_cut"] == 2
    assert scored["ad_seconds_missed"] == 0
    assert scored["unmatched_predicted_spans"] == 1
    assert scored["mean_boundary_error_seconds"] is None
    assert interval_metrics([], [], 10)["exact_match"] is True
    split = interval_metrics([span(2, 4), span(6, 8)], [span(2, 8)], 10)
    assert split["matched_spans"] == 1
    assert split["unmatched_predicted_spans"] == 1
    assert split["ad_seconds_missed"] == 2
    assert split["boundary_matches"][0]["predicted_index"] == 0


def test_touching_and_overlapping_intervals_can_match_exactly():
    scored = interval_metrics([span(1, 3), span(2, 5), span(5, 7)], [span(1, 7)], 10)
    assert scored["exact_match"] is True
    assert scored["selected_seconds"] == 6
    with pytest.raises(ValueError):
        interval_metrics([span(0, 11)], [], 10)


def test_ambiguous_cases_only_affect_review_accounting():
    fixtures = dataset(case("certain"), case("uncertain", category="ambiguous", expected=[], expect_review=True))
    answers = [classify([span(2, 8, approved=True, confidence=.95)]), classify([span(0, 10, approved=False, confidence=.6)], review=True)]
    with patch("castwell.evaluation._run_backend", side_effect=answers):
        result = evaluate_dataset(fixtures)
    backend = result["backends"][0]
    summary = backend["summary"]
    assert summary["accuracy_scored_cases"] == 1
    assert summary["ambiguous_cases"] == 1
    assert summary["approved"]["total_ad_seconds"] == 6
    assert summary["proposed"]["editorial_seconds_wrongly_cut"] == 0
    assert summary["review_true_positive_cases"] == 1
    assert summary["manual_review_cases"] == 1
    assert backend["cases"][1]["metrics"] is None


def test_ambiguous_automatic_cut_harm_is_visible_without_accuracy_labels():
    fixtures = dataset(case("unsupported", category="ambiguous", expected=[], expect_review=True),
                       case("failed", category="ambiguous", expected=[], expect_review=True))
    cuts = [span(1, 5, approved=True, confidence=.95), span(3, 7, approved=True, confidence=.95),
            span(8, 10, approved=False, confidence=.6)]
    with patch("castwell.evaluation._run_backend", side_effect=[classify(cuts, review=True), RuntimeError("failure")]):
        result = evaluate_dataset(fixtures)
    summary = result["backends"][0]["summary"]
    assert summary["accuracy_scored_cases"] == 0
    assert summary["approved"]["total_ad_seconds"] == 0
    safety = summary["ambiguous_safety"]
    assert (safety["attempted_cases"], safety["successful_cases"], safety["failed_cases"]) == (2, 1, 1)
    assert safety["eligible_duration_seconds_including_failed_cases"] == 20
    assert safety["scored_duration_seconds"] == 10
    assert safety["proposed"] == {"selected_seconds": 8, "union_spans": 2, "cases_with_cuts": 1}
    assert safety["approved"] == {"selected_seconds": 6, "union_spans": 1, "cases_with_cuts": 1}
    assert safety["threshold_shadow"] == safety["approved"]


def test_groups_cannot_leak_between_dev_and_eval():
    fixtures = dataset(case("evaluation"), dict(case("development"), split="dev", group="synthetic-evaluation"))
    with patch("castwell.evaluation._run_backend") as run, pytest.raises(ValueError, match="groups must not cross"):
        evaluate_dataset(fixtures)
    run.assert_not_called()


@pytest.mark.parametrize("changes", [{"expect_review": False}, {"expected_intervals": [span(2, 8)]}])
def test_ambiguous_contract_requires_review_without_asserted_truth(changes):
    fixture = case("uncertain", category="ambiguous", expected=[], expect_review=True)
    fixture.update(changes)
    with patch("castwell.evaluation._run_backend") as run, pytest.raises(ValueError, match="Ambiguous cases require"):
        evaluate_dataset(dataset(fixture))
    run.assert_not_called()


def test_failures_are_not_counted_as_successful_empty_predictions():
    fixtures = dataset(case("fails"), case("negative", expected=[]))
    with patch("castwell.evaluation._run_backend", side_effect=[RuntimeError("private credential must not appear"), classify()]):
        result = evaluate_dataset(fixtures)
    backend = result["backends"][0]
    summary = backend["summary"]
    assert summary["accuracy_eligible_cases"] == 2
    assert summary["accuracy_scored_cases"] == 1
    assert summary["accuracy_failed_cases"] == 1
    assert summary["eligible_ad_seconds_including_failed_cases"] == 6
    assert summary["approved"]["total_ad_seconds"] == 0
    assert summary["approved"]["exact_match_cases"] == 1
    assert backend["cases"][0]["cuts"] is None
    assert backend["cases"][0]["metrics"] is None
    assert "private credential" not in json.dumps(result)


@pytest.mark.parametrize("address", ["http://127.0.0.1:8081/v1", "http://localhost/v1", "http://[::1]:8081/v1"])
def test_loopback_literal_allowlist(address):
    assert validate_endpoint(address) == address


@pytest.mark.parametrize("address", ["http://127.1/v1", "http://127.0.0.2/v1", "https://provider.example/v1", "http://localhost.example/v1", "http://localhost./v1", "http://2130706433/v1", "http://user:key@localhost/v1", "http://localhost/v1?key=secret", "http://localhost:invalid/v1"])
def test_remote_or_credential_endpoints_refused_before_detector_call(address):
    with patch("castwell.evaluation._run_backend") as run:
        with pytest.raises(ValueError):
            evaluate_dataset(dataset(), backends=["heuristic", "local-ai"], base_url=address)
    run.assert_not_called()


def test_remote_endpoint_needs_explicit_override():
    with patch("castwell.evaluation._run_backend", return_value=classify()) as run:
        evaluate_dataset(dataset(), backends=["local-ai"], base_url="https://provider.example/v1", allow_remote=True)
    run.assert_called_once()


@pytest.mark.parametrize("options", [{}, {"jev_api_key": "fake-key"}, {"allow_paid_api": True}, {"allow_paid_api": True, "jev_api_key": " "}, {"allow_paid_api": True, "jev_api_key": "fake-key", "jev_base_url": "https://other.example"}])
def test_paid_api_requires_opt_in_and_explicit_key_before_any_calls(options):
    with patch("castwell.evaluation._run_backend") as run:
        with pytest.raises(ValueError):
            evaluate_dataset(dataset(), backends=["heuristic", "jev"], **options)
    run.assert_not_called()


def test_jev_actual_approval_is_distinct_from_hypothetical_threshold():
    answer = {"cuts": [span(2, 8, approved=False, confidence=.97, source="jev")], "review": False,
              "usage": {"input_tokens": 10}, "model": "jev-test", "request_count": 1, "policy_version": "test",
              "decisions": [{"segment_id": 0, "choice": "commercial", "confidence": .97}]}
    with patch("castwell.jev.classify_transcript", return_value=answer) as provider:
        result = evaluate_dataset(dataset(), backends=["jev"], allow_paid_api=True, jev_api_key="fake-key")
    assert provider.call_args.kwargs["allow_paid_api"] is True
    summary = result["backends"][0]["summary"]
    assert summary["proposed"]["ad_seconds_missed"] == 0
    assert summary["approved"]["ad_seconds_missed"] == 6
    assert summary["threshold_shadow"]["ad_seconds_missed"] == 0
    record = result["backends"][0]["cases"][0]
    assert record["provider_metadata"]["policy_version"] == "test"
    assert record["decisions"] == answer["decisions"]
    assert "fake-key" not in json.dumps(result)


def test_production_detector_receives_frozen_approval_policy():
    with patch("castwell.evaluation.processing.detect_ads", return_value=[]) as detector:
        evaluate_dataset(dataset(), backends=["local-ai"], model="local-test", model_label="Local test Q4")
    assert detector.call_args.args[1] == "ai"
    config = detector.call_args.kwargs["config"]
    assert config["auto_approve_threshold"] == .90
    assert config["review_only"] is False
    assert config["ai_model"] == "local-test"
    assert config["ai_key"] == ""
    assert config["ai_allow_redirects"] is False
    assert config["ai_trust_env"] is False


def test_local_request_rejects_redirect_and_ignores_ambient_credentials(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Proxy-Authorization")))
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirect-destination")
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1 login inherited-user password inherited-secret\n", encoding="utf-8")
    try:
        ambient = {"CASTWELL_AI_KEY": "inherited-key", "NETRC": str(netrc), "HTTP_PROXY": "http://user:secret@127.0.0.1:1",
                   "HTTPS_PROXY": "http://user:secret@127.0.0.1:1", "ALL_PROXY": "http://user:secret@127.0.0.1:1", "NO_PROXY": ""}
        with patch.dict(os.environ, ambient):
            result = evaluate_dataset(dataset(), backends=["local-ai"], base_url=f"http://127.0.0.1:{server.server_port}/v1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert seen == [("/v1/chat/completions", None, None)]
    record = result["backends"][0]["cases"][0]
    assert record["status"] == "error"
    assert "redirect refused" in record["error"]["message"].lower()
    assert result["backends"][0]["summary"]["failed_cases"] == 1
    assert "inherited-key" not in json.dumps(result)


def test_fixture_file_hash_uses_original_bytes_and_split(tmp_path):
    fixture = dataset(case("holdout"), dict(case("development"), split="dev"))
    path = tmp_path / "challenge.json"
    raw = json.dumps(fixture, indent=4).encode()
    path.write_bytes(raw)
    with patch("castwell.evaluation._run_backend", return_value=classify()):
        result = evaluate_file(path, split="dev")
    assert result["dataset_sha256"] == hashlib.sha256(raw).hexdigest()
    assert [row["id"] for row in result["backends"][0]["cases"]] == ["development"]


def test_script_runs_from_checkout_outside_repository_without_network(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(dataset()), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_detectors.py"
    result = subprocess.run([sys.executable, "-B", str(script), "--fixtures", str(fixture)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [item["config"]["backend"] for item in report["backends"]] == ["heuristic"]
    assert report["settings"]["allow_paid_api"] is False
    assert report["provenance"]["package_versions"]["castwell"]
