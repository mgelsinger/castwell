"""Mocked Jev contract checks. No paid requests are made by these tests."""

import json
from unittest.mock import Mock

import pytest
import requests

from castwell.jev import classify_local_transcript, classify_transcript
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


def mock_local_api(monkeypatch, *, status=200, corrupt=None, **options):
    """Exercise real request preparation/session transport, replace only I/O."""
    sent, responses = [], []

    def send(adapter, request, **kwargs):
        sent.append((request, kwargs))
        payload = json.loads(request.body)
        answer = response_for(payload, **options)
        answer["model"] = payload["model"]
        if corrupt:
            corrupt(answer, payload)
        response = requests.Response()
        response.status_code = status
        response.request = request
        response.url = request.url
        response._content = json.dumps(answer).encode()
        response.headers["Content-Type"] = "application/json"
        if 300 <= status < 400:
            response.headers["Location"] = "https://remote.example/v1/systemone"
        responses.append(response)
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent, responses


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8083", "http://127.0.0.1:8083/v1",
    "http://localhost:8083/", "http://[::1]:8083/v1/",
    "https://localhost:8083/v1",
])
def test_local_accepts_only_explicit_loopback_hosts(monkeypatch, url):
    sent, _ = mock_local_api(monkeypatch)
    result = classify_local_transcript(transcript(), base_url=url)
    assert len(sent) == result["request_count"] == 1
    assert sent[0][0].url.endswith("/v1/systemone")
    assert result["model"] == result["requested_model"] == "kev-latest"
    assert result["provider"] == "kev-local"


@pytest.mark.parametrize("url", [
    "https://api.typesafe.ai", "http://remote.example", "http://localhost.remote.example",
    "http://127.0.0.2:8083", "http://2130706433:8083", "http://127.1:8083",
    "http://[::ffff:127.0.0.1]:8083", "http://localhost.:8083", "http://LOCALHOST@remote.example",
    "http://secret@localhost:8083", "http://:secret@localhost:8083",
    "http://@localhost:8083", "http://localhost:8083/v1?secret=key",
    "http://localhost:8083/?", "http://localhost:8083/#",
    "http://localhost:8083/#fragment", "http://localhost:8083/other",
    "http://localhost:8083/v1/systemone", "ftp://localhost:8083",
    "http://localhost:0", "http://localhost:99999", "http://localhost:invalid",
    " http://localhost:8083", "http://local\nhost:8083", "http://localhost\\@remote.example",
    "", None,
])
def test_invalid_local_endpoint_fails_before_constructing_transport(monkeypatch, url):
    session = Mock()
    monkeypatch.setattr(requests, "Session", session)
    with pytest.raises(ValueError, match="literal"):
        classify_local_transcript(transcript(), base_url=url)
    session.assert_not_called()


@pytest.mark.parametrize("settings", [
    {"model": ""}, {"model": None}, {"timeout": 0}, {"timeout": 301},
    {"timeout": True}, {"timeout": float("nan")}, {"timeout": float("inf")},
])
def test_invalid_local_configuration_fails_before_network(monkeypatch, settings):
    session = Mock()
    monkeypatch.setattr(requests, "Session", session)
    with pytest.raises(ValueError):
        classify_local_transcript(transcript(), **settings)
    session.assert_not_called()


def test_local_request_has_no_inherited_credentials_proxy_or_netrc(monkeypatch, tmp_path):
    for name in ("TYPESAFE_API_KEY", "CASTWELL_AI_KEY", "KEV_API_KEY"):
        monkeypatch.setenv(name, "private-environment-secret")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy-user:proxy-secret@proxy.invalid:9000")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-user:proxy-secret@proxy.invalid:9000")
    monkeypatch.setenv("ALL_PROXY", "http://proxy-user:proxy-secret@proxy.invalid:9000")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "missing-ca.pem"))
    netrc = tmp_path / "netrc"
    netrc.write_text("machine localhost login netrc-user password netrc-secret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    netrc_lookup = Mock(side_effect=AssertionError("Local adapter must not read netrc"))
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", netrc_lookup)
    sent, _ = mock_local_api(monkeypatch)
    result = classify_local_transcript(transcript(), base_url="http://localhost:8083",
                                       model="kev-custom", timeout=21)
    request, settings = sent[0]
    assert "Authorization" not in request.headers
    assert "Proxy-Authorization" not in request.headers
    assert settings["proxies"] == {}
    assert settings["verify"] is True
    assert settings["timeout"] == (10, 21)
    netrc_lookup.assert_not_called()
    assert json.loads(request.body)["model"] == "kev-custom"
    assert "secret" not in request.body.decode()
    assert "secret" not in json.dumps(result)
    assert result["cuts"][0]["approved"] is False
    assert result["cuts"][0]["provider"] == result["cuts"][0]["source"] == "kev-local"
    assert result["cuts"][0]["policy_version"] == result["policy_version"] == "kev-context-v2"
    assert result["cuts"][0]["model"] == result["model"] == "kev-custom"
    assert result["cuts"][0]["segment_ids"] == [0]
    assert result["decisions"][0]["start"] == 0
    assert result["decisions"][0]["end"] == 5


@pytest.mark.parametrize("status", [301, 302, 307, 308, 429, 500])
def test_local_rejects_redirect_without_sending_destination_or_retry(monkeypatch, status):
    sent, _ = mock_local_api(monkeypatch, status=status)
    with pytest.raises(ProcessingError, match=f"HTTP {status}"):
        classify_local_transcript(transcript())
    # Requests handles redirects above HTTPAdapter.send, so a mistaken redirect
    # setting would make a second intercepted request to the remote destination.
    assert len(sent) == 1
    assert sent[0][0].url == "http://127.0.0.1:8083/v1/systemone"


def test_local_transport_failure_is_safe_and_never_retried(monkeypatch):
    send = Mock(side_effect=requests.ConnectionError("private-url private-secret"))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with pytest.raises(ProcessingError, match="Local Kev request failed") as error:
        classify_local_transcript(transcript())
    assert "private" not in str(error.value)
    send.assert_called_once()


@pytest.mark.parametrize("kind,parody,confidence,review,count", [
    ("commercial", .01, .99, False, 1),
    ("commercial", .9, .99, True, 1),
    ("commercial", .01, .7, False, 1),
    ("editorial", .99, .99, False, 0),
    ("uncertain", .4, .99, True, 0),
])
def test_local_keeps_uncertainty_separate_from_manual_approval(monkeypatch, kind, parody, confidence, review, count):
    mock_local_api(monkeypatch, kind=kind, parody=parody, confidence=confidence)
    result = classify_local_transcript(transcript())
    assert result["review"] is review
    assert len(result["cuts"]) == count
    assert all(cut["approved"] is False for cut in result["cuts"])
    if result["cuts"]:
        assert result["cuts"][0]["confidence"] == confidence
        assert result["cuts"][0]["commercial_probability"] == .98


@pytest.mark.parametrize("probability,review", [(.89, True), (.90, False), (.91, False)])
def test_local_review_uses_probability_not_normalized_margin(monkeypatch, probability, review):
    margin = (probability - 1 / 3) / (1 - 1 / 3)

    def probabilities(answer, payload):
        choice = answer["answers"]["kind_0"]
        choice["probabilities"] = {"commercial": probability, "editorial": (1 - probability) / 2,
                                   "uncertain": (1 - probability) / 2}
        choice["confidence"] = margin

    mock_local_api(monkeypatch, corrupt=probabilities)
    result = classify_local_transcript(transcript())
    assert result["review"] is review
    assert result["decisions"][0]["selected_probability"] == probability
    cut = result["cuts"][0]
    assert cut["confidence"] == margin < .90
    assert cut["selected_probability"] == probability
    assert cut["confidence_kind"] == "normalized_probability_margin"
    assert cut["review_score"] == "selected_probability"
    assert cut["requires_review"] is review
    assert cut["approved"] is False
    assert result["confidence_semantics"]["review_score"] == "selected_probability"


def test_local_windows_bound_targets_and_each_side_of_context(monkeypatch):
    sample = transcript(25)
    for segment in sample["segments"]:
        segment["text"] = "x" * 700
    sent, _ = mock_local_api(monkeypatch)
    result = classify_local_transcript(sample)
    seen = []
    for request, _ in sent:
        payload = json.loads(request.body)
        ids = [int(key.split("_")[1]) for key in payload["questions"] if key.startswith("kind_")]
        assert len(ids) <= 12
        assert len(ids) * 700 <= 3000
        seen.extend(ids)
        before = [s for s in payload["state"]["segments"] if s["id"] < min(ids)]
        after = [s for s in payload["state"]["segments"] if s["id"] > max(ids)]
        assert sum(len(s["text"]) for s in before) <= 1500
        assert sum(len(s["text"]) for s in after) <= 1500
    assert seen == list(range(25))
    assert result["request_count"] == len(sent) == 7
    assert result["usage"] == {"input_tokens": 700, "output_tokens": 210}
    assert result["cuts"][0]["segment_ids"] == seen


def test_local_windows_enforce_unit_limit_with_short_transcripts(monkeypatch):
    sent, _ = mock_local_api(monkeypatch)
    result = classify_local_transcript(transcript(25))
    targets = [len(json.loads(request.body)["questions"]) // 3 for request, _ in sent]
    assert targets == [12, 12, 1]
    assert len(result["decisions"]) == 25


def test_local_all_windows_are_validated_before_any_request(monkeypatch):
    sample = transcript(25)
    sample["segments"][-1]["text"] = "x" * 3001
    session = Mock()
    monkeypatch.setattr(requests, "Session", session)
    with pytest.raises(ValueError, match="3000 characters"):
        classify_local_transcript(sample)
    session.assert_not_called()


@pytest.mark.parametrize("corruption", ["missing", "unexpected", "probability", "confidence", "noul", "usage", "model", "choice"])
def test_local_invalid_typed_answers_fail_whole_run(monkeypatch, corruption):
    def corrupt(answer, payload):
        # Corrupt only the later window to prove earlier proposals cannot leak
        # into a successful partial result when a later response is malformed.
        if "kind_12" not in payload["questions"]:
            return
        if corruption == "missing":
            del answer["answers"]["kind_12"]
        elif corruption == "unexpected":
            answer["answers"]["kind_999"] = answer["answers"]["kind_12"]
        elif corruption == "probability":
            answer["answers"]["kind_12"]["probabilities"]["commercial"] = .2
        elif corruption == "confidence":
            answer["answers"]["kind_12"]["confidence"] = float("nan")
        elif corruption == "noul":
            answer["answers"]["humor_12"]["noul"] = True
        elif corruption == "usage":
            answer["usage"]["input_tokens"] = -1
        elif corruption == "choice":
            answer["answers"]["kind_12"] = "bad-answer"
        else:
            answer["model"] = "changed-mid-run"

    sent, _ = mock_local_api(monkeypatch, corrupt=corrupt)
    with pytest.raises(ProcessingError, match="invalid typed decisions"):
        classify_local_transcript(transcript(13))
    assert len(sent) == 2


@pytest.mark.parametrize("local", [False, True])
def test_typed_cuts_do_not_bridge_unobserved_gaps(monkeypatch, local):
    sample = transcript(2)
    sample["segments"][1]["start"] = 6
    if local:
        mock_local_api(monkeypatch)
        result = classify_local_transcript(sample)
    else:
        mock_api(monkeypatch)
        result = classify_transcript(sample, api_key="test-secret", allow_paid_api=True)
    assert [(cut["start"], cut["end"]) for cut in result["cuts"]] == [(0, 5), (6, 10)]


def test_local_editorial_segment_prevents_merging_commercial_neighbors(monkeypatch):
    def editorial_middle(answer, payload):
        answer["answers"]["kind_1"] = {
            "type": "choice", "choice": "editorial", "confidence": .99,
            "probabilities": {"commercial": .01, "editorial": .98, "uncertain": .01},
        }

    mock_local_api(monkeypatch, corrupt=editorial_middle)
    result = classify_local_transcript(transcript(3))
    assert [(cut["start"], cut["end"]) for cut in result["cuts"]] == [(0, 5), (10, 15)]
    assert [cut["segment_ids"] for cut in result["cuts"]] == [[0], [2]]
