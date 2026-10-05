"""Mocked Jev contract checks. No paid requests are made by these tests."""

import json
from unittest.mock import Mock

import pytest
import requests

from castwell.jev import classify_transcript
from castwell.processing import ProcessingError


def transcript(count=1):
    return {"duration": count * 5, "segments": [
        {"id": i, "start": i * 5, "end": (i + 1) * 5,
         "text": "A sponsored pitch delivered as a joke."} for i in range(count)
    ]}


def response_for(payload, *, kind="commercial", confidence=.96, parody=.02, humor=.99):
    answers = {}
    for name in payload["questions"]:
        if name.startswith("kind_"):
            answers[name] = {"type": "choice", "choice": kind, "confidence": confidence,
                             "probabilities": {label: .98 if label == kind else .01
                                               for label in ("commercial", "editorial", "uncertain")}}
        else:
            answers[name] = {"type": "noul", "noul": parody if name.startswith("parody_") else humor}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 30}}


def mock_api(monkeypatch, **options):
    responses = []

    def send(url, **kwargs):
        response = Mock(status_code=200)
        response.json.return_value = response_for(kwargs["json"], **options)
        responses.append(response)
        return response

    post = Mock(side_effect=send)
    monkeypatch.setattr(requests, "post", post)
    return post, responses


def test_paid_guard_precedes_network_even_with_environment_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    post, _ = mock_api(monkeypatch)
    with pytest.raises(ValueError, match="charges"):
        classify_transcript(transcript())
    post.assert_not_called()


@pytest.mark.parametrize("settings", [
    {"api_key": ""}, {"api_key": " "},
    {"base_url": "https://other.example", "api_key": "test-secret"},
    {"base_url": "https://api.typesafe.ai@other.example", "api_key": "test-secret"},
    {"base_url": "http://api.typesafe.ai", "api_key": "test-secret"},
    {"base_url": "https://api.typesafe.ai/v1?key=secret", "api_key": "test-secret"},
    {"timeout": float("nan"), "api_key": "test-secret"},
])
def test_invalid_paid_configuration_sends_nothing(monkeypatch, settings):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    post, _ = mock_api(monkeypatch)
    with pytest.raises(ValueError):
        classify_transcript(transcript(), allow_paid_api=True, **settings)
    post.assert_not_called()


def test_humorous_commercial_is_proposed_but_never_approved(monkeypatch):
    post, responses = mock_api(monkeypatch)
    result = classify_transcript(transcript(2), api_key="test-secret", allow_paid_api=True)
    assert len(result["cuts"]) == 1
    cut = result["cuts"][0]
    assert (cut["start"], cut["end"]) == (0, 10)
    assert cut["approved"] is False
    assert cut["confidence"] == .96
    assert cut["commercial_probability"] == .98
    assert result["review"] is False
    assert result["decisions"][0]["humor_probability"] == .99
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 30}
    assert "test-secret" not in json.dumps(result)
    url = post.call_args.args[0]
    arguments = post.call_args.kwargs
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert arguments["allow_redirects"] is False
    assert arguments["timeout"] == (10, 60)
    for name, question in arguments["json"]["questions"].items():
        assert f"segment id {name.split('_')[1]}" in question["instructions"]
    responses[0].close.assert_called_once()


@pytest.mark.parametrize("kind,parody,confidence,review,count", [
    ("commercial", .9, .99, True, 1),
    ("commercial", .01, .7, True, 1),
    ("editorial", .99, .99, False, 0),
    ("uncertain", .4, .99, True, 0),
])
def test_conflicts_uncertainty_and_editorial_are_distinct(monkeypatch, kind, parody, confidence, review, count):
    mock_api(monkeypatch, kind=kind, parody=parody, confidence=confidence)
    result = classify_transcript(transcript(), api_key="test-secret", allow_paid_api=True)
    assert result["review"] is review
    assert len(result["cuts"]) == count
    assert all(not cut["approved"] for cut in result["cuts"])


@pytest.mark.parametrize("corruption", ["missing", "probability", "confidence", "noul", "usage", "model"])
def test_invalid_typed_answers_are_errors_not_no_ad_results(monkeypatch, corruption):
    def send(url, **kwargs):
        answer = response_for(kwargs["json"])
        if corruption == "missing":
            del answer["answers"]["kind_0"]
        elif corruption == "probability":
            answer["answers"]["kind_0"]["probabilities"]["commercial"] = .2
        elif corruption == "confidence":
            answer["answers"]["kind_0"]["confidence"] = float("nan")
        elif corruption == "noul":
            answer["answers"]["humor_0"]["noul"] = True
        elif corruption == "usage":
            answer["usage"]["input_tokens"] = -1
        else:
            answer["model"] = None
        response.json.return_value = answer
        return response

    response = Mock(status_code=200)
    post = Mock(side_effect=send)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProcessingError, match="invalid typed decisions"):
        classify_transcript(transcript(), api_key="test-secret", allow_paid_api=True)
    post.assert_called_once()
    response.close.assert_called_once()


def test_long_transcript_uses_context_without_duplicate_target_decisions(monkeypatch):
    post, _ = mock_api(monkeypatch)
    result = classify_transcript(transcript(35), api_key="test-secret", allow_paid_api=True)
    assert post.call_count == result["request_count"] == 2
    assert len(result["decisions"]) == 35
    assert len(result["cuts"]) == 1
    assert result["cuts"][0]["end"] == 175
    second = post.call_args_list[1].kwargs["json"]
    assert len(second["state"]["segments"]) == 7
    assert set(second["questions"]) == {f"{kind}_{i}" for i in range(32, 35) for kind in ("kind", "parody", "humor")}
    assert result["usage"]["input_tokens"] == 200


def test_all_windows_validated_before_any_charge(monkeypatch):
    post, _ = mock_api(monkeypatch)
    sample = transcript(35)
    sample["segments"][-1]["text"] = "x" * 4001
    with pytest.raises(ValueError, match="4000 characters"):
        classify_transcript(sample, api_key="test-secret", allow_paid_api=True)
    post.assert_not_called()


@pytest.mark.parametrize("status", [307, 429, 500])
def test_http_errors_never_redirect_or_retry(monkeypatch, status):
    response = Mock(status_code=status)
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProcessingError, match=f"HTTP {status}"):
        classify_transcript(transcript(), api_key="test-secret", allow_paid_api=True)
    post.assert_called_once()
    response.close.assert_called_once()


def test_transport_error_does_not_expose_credentials_or_retry(monkeypatch):
    post = Mock(side_effect=requests.ConnectionError("private request test-secret"))
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProcessingError) as error:
        classify_transcript(transcript(), api_key="test-secret", allow_paid_api=True)
    assert "test-secret" not in str(error.value)
    post.assert_called_once()
