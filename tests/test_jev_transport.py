"""Official Jev budget and transport checks, using no network or credentials."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest
import requests

from castwell import jev_transport as jt

KEY = "fake-credential-for-offline-tests"
SOURCE_HASH = "a" * 64


def payload():
    return {"model": jt.MODEL, "state": {"segments": [{"id": 1, "text": "An ordinary discussion."}]},
            "questions": {"kind_1": {"type": "choice", "criteria": {"editorial": "Editorial"}}}}


def answer(*, model=jt.MODEL, input_tokens=100, output_tokens=20):
    return {"model": model, "answers": {},
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


def response(body=None, status=200, *, raw=None):
    return Mock(status_code=status, text=raw if raw is not None else json.dumps(answer() if body is None else body))


@pytest.fixture(autouse=True)
def no_unmocked_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        pytest.fail("A test attempted unmocked network access")
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


def fake_session(monkeypatch, side_effect=None):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post = Mock(side_effect=side_effect, return_value=response())
    factory = Mock(return_value=session)
    monkeypatch.setattr(jt.requests, "Session", factory)
    return session, factory


def transport(tmp_path, **kwargs):
    return jt.JevTransport(api_key=KEY, ledger_path=tmp_path / "budget.jsonl",
                           request_source_sha256=SOURCE_HASH, **kwargs)


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "budget.jsonl").read_text().splitlines()]


def test_exact_official_transport_reserves_before_connection_and_settles_input_only(tmp_path, monkeypatch):
    def send(url, **kwargs):
        ledger = events(tmp_path)
        assert [item["event"] for item in ledger] == ["configuration", "reserved"]
        assert ledger[-1]["data"]["reserved_nano_usd"] == 2_752_512
        assert url == "https://api.typesafe.ai/v1/systemone"
        assert kwargs == {"json": payload(), "headers": {"Authorization": "Bearer " + KEY},
                          "timeout": (10, 180), "allow_redirects": False, "verify": True, "proxies": {}}
        return response(answer(input_tokens=100, output_tokens=65536))
    session, factory = fake_session(monkeypatch, send)
    result = transport(tmp_path, budget_usd=Decimal("0.50")).call("plan:0001", payload())
    assert result["status"] == "ok" and result["attempted"] is True
    assert result["provider"] == "official-typesafe-jev"
    receipt = result["budget_receipt"]
    assert receipt["reserved_micro_usd"] == "2752.512"
    assert receipt["settled_nano_usd"] == 4200
    assert receipt["settled_micro_usd"] == "4.200"
    assert receipt["ledger_sha256"] == hashlib.sha256((tmp_path / "budget.jsonl").read_bytes()).hexdigest()
    assert events(tmp_path)[0]["data"]["absolute_ceiling_usd"] == "5"
    assert session.trust_env is False and session.auth is None
    session.proxies.clear.assert_called_once()
    session.cookies.clear.assert_called_once()
    adapter = session.mount.call_args.args[1]
    assert adapter.max_retries.total == 0
    assert session.post.call_count == factory.call_count == 1
    assert KEY not in (tmp_path / "budget.jsonl").read_text()


@pytest.mark.parametrize("budget", [True, False, None, [], {}, "NaN", "sNaN", "Infinity", "-Infinity",
                                    float("nan"), float("inf"), Decimal("NaN"), "-0.001", -1, "5.000000001", 6])
def test_bad_budget_rejected_before_ledger_or_network(tmp_path, monkeypatch, budget):
    _, factory = fake_session(monkeypatch)
    with pytest.raises(jt.JevTransportError, match="^invalid_budget$"):
        transport(tmp_path, budget_usd=budget)
    assert not (tmp_path / "budget.jsonl").exists()
    factory.assert_not_called()


@pytest.mark.parametrize("budget", [0, "0", Decimal("0.002752511")])
def test_full_reservation_must_fit_before_call(tmp_path, monkeypatch, budget):
    _, factory = fake_session(monkeypatch)
    result = transport(tmp_path, budget_usd=budget).call("one", payload())
    assert result["status"] == "not_run" and result["attempted"] is False
    assert result["error_type"] == "budget_exhausted"
    assert result["budget_receipt"]["total_accounted_nano_usd"] == 0
    factory.assert_not_called()


def test_exact_reservation_boundary_and_missing_usage_holds_all(tmp_path, monkeypatch):
    session, _ = fake_session(monkeypatch, lambda *_args, **_kwargs: response({"model": jt.MODEL}))
    caller = transport(tmp_path, budget_usd="0.002752512")
    failed = caller.call("one", payload())
    blocked = caller.call("two", payload())
    assert failed["error_type"] == "invalid_usage"
    assert failed["budget_receipt"]["state"] == "held"
    assert blocked["error_type"] == "budget_exhausted"
    assert blocked["budget_receipt"]["total_accounted_nano_usd"] == 2_752_512
    assert session.post.call_count == 1


def test_crash_pending_request_is_never_resent_after_reopen(tmp_path, monkeypatch):
    session, _ = fake_session(monkeypatch, KeyboardInterrupt)
    caller = transport(tmp_path, budget_usd="0.50")
    with pytest.raises(KeyboardInterrupt):
        caller.call("interrupted", payload())
    assert events(tmp_path)[-1]["event"] == "reserved"
    result = transport(tmp_path, budget_usd="0.50").call("interrupted", payload())
    assert result["status"] == "not_run" and result["attempted"] is False
    assert result["error_type"] == "request_already_reserved"
    assert result["budget_receipt"]["state"] == "pending"
    assert result["budget_receipt"]["total_accounted_nano_usd"] == 2_752_512
    assert session.post.call_count == 1


def test_same_id_with_changed_payload_is_integrity_error(tmp_path, monkeypatch):
    session, _ = fake_session(monkeypatch)
    caller = transport(tmp_path)
    caller.call("one", payload())
    altered = payload()
    altered["state"]["segments"][0]["text"] = "Changed input."
    with pytest.raises(jt.JevTransportError, match="^request_id_payload_mismatch$"):
        caller.call("one", altered)
    assert session.post.call_count == 1


@pytest.mark.parametrize("field,value", [("input_tokens", True), ("input_tokens", -1),
                                         ("input_tokens", 65537), ("input_tokens", 1.0),
                                         ("input_tokens", "100"), ("output_tokens", False),
                                         ("output_tokens", 65537), ("output_tokens", None)])
def test_invalid_usage_cannot_release_reservation(tmp_path, monkeypatch, field, value):
    body = answer()
    body["usage"][field] = value
    fake_session(monkeypatch, lambda *_args, **_kwargs: response(body))
    result = transport(tmp_path).call("one", payload())
    assert result["status"] == "error" and result["error_type"] == "invalid_usage"
    assert result["usage"] is None
    assert result["budget_receipt"]["state"] == "held"
    assert result["budget_receipt"]["accounted_nano_usd"] == jt.RESERVATION_NANO_USD


def test_wrong_model_holds_full_reservation_even_with_valid_usage(tmp_path, monkeypatch):
    fake_session(monkeypatch, lambda *_args, **_kwargs: response(answer(model="jev-latest")))
    result = transport(tmp_path).call("one", payload())
    assert result["response_model"] == "jev-latest"
    assert result["error_type"] == "response_model_mismatch"
    assert result["budget_receipt"]["state"] == "held"


@pytest.mark.parametrize("code", [401, 402, 403])
def test_account_failure_stops_new_requests_durably(tmp_path, monkeypatch, code):
    session, _ = fake_session(monkeypatch, lambda *_args, **_kwargs: response(status=code))
    failed = transport(tmp_path).call("one", payload())
    blocked = transport(tmp_path).call("two", payload())
    assert failed["error_type"] == "account_blocked"
    assert blocked["error_type"] == "authentication_stopped"
    assert blocked["status"] == "not_run" and blocked["attempted"] is False
    assert session.post.call_count == 1
    assert blocked["budget_receipt"]["total_accounted_nano_usd"] == jt.RESERVATION_NANO_USD


@pytest.mark.parametrize("code", [301, 302, 307, 308, 429, 529])
def test_redirects_and_transient_errors_are_one_attempt_and_preserve_full_cost(tmp_path, monkeypatch, code):
    session, _ = fake_session(monkeypatch, lambda *_args, **_kwargs: response(status=code))
    result = transport(tmp_path).call("one", payload())
    assert result["status"] == "error" and result["error_type"] == "http_error"
    assert result["http_status"] == code and result["attempted"] is True
    assert result["budget_receipt"]["state"] == "held"
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["allow_redirects"] is False


def test_429_permits_next_distinct_request_with_fixed_pacing(tmp_path, monkeypatch):
    clock = [100.0]
    sleeps = []
    starts = []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    def send(*_args, **_kwargs):
        starts.append(clock[0])
        return response(status=429 if len(starts) == 1 else 200)
    monkeypatch.setattr(jt.time, "time", lambda: clock[0])
    monkeypatch.setattr(jt.time, "sleep", sleep)
    fake_session(monkeypatch, send)
    caller = transport(tmp_path)
    assert caller.call("one", payload())["status"] == "error"
    result = caller.call("two", payload())
    assert result["status"] == "ok"
    assert sleeps == [1.0] and starts == [100.0, 101.0]
    assert result["budget_receipt"]["total_accounted_nano_usd"] == jt.RESERVATION_NANO_USD + 4200


@pytest.mark.parametrize("exception,code", [(requests.Timeout(KEY), "timeout"),
                                          (requests.ConnectionError(KEY), "transport_error"),
                                          (ValueError(KEY), "transport_internal_error")])
def test_exception_strings_never_escape_and_reservation_is_held(tmp_path, monkeypatch, exception, code):
    fake_session(monkeypatch, exception)
    caller = transport(tmp_path)
    result = caller.call("one", payload())
    assert result["error_type"] == code and result["budget_receipt"]["state"] == "held"
    assert KEY not in json.dumps(result) + repr(caller) + (tmp_path / "budget.jsonl").read_text()


@pytest.mark.parametrize("escaped,status", [(False, 200), (True, 200), (False, 401)])
def test_reflected_key_body_is_discarded_before_return_or_persistence(tmp_path, monkeypatch, escaped, status):
    raw = json.dumps(dict(answer(), message=KEY))
    if escaped:
        raw = raw.replace(KEY, "".join("\\u%04x" % ord(char) for char in KEY))
    fake_session(monkeypatch, lambda *_args, **_kwargs: response(raw=raw, status=status))
    result = transport(tmp_path).call("one", payload())
    assert result["status"] == "error" and result["response_redacted"] is True
    assert result["raw_response_text"] is None and result["response"] is None
    assert result["response_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert result["budget_receipt"]["state"] == "held"
    assert KEY not in json.dumps(result) + (tmp_path / "budget.jsonl").read_text()
    assert "message" not in (tmp_path / "budget.jsonl").read_text()


@pytest.mark.parametrize("raw", ["broken JSON", '{"model":"jev-1.13.0","value":NaN}'])
def test_invalid_json_is_unavailable_not_success(tmp_path, monkeypatch, raw):
    fake_session(monkeypatch, lambda *_args, **_kwargs: response(raw=raw))
    result = transport(tmp_path).call("one", payload())
    assert result["error_type"] == "invalid_json" and result["response"] is None
    assert result["budget_receipt"]["state"] == "held"


def test_source_file_change_prevents_request(tmp_path, monkeypatch):
    source = tmp_path / "runner.py"
    source.write_text("original")
    caller = jt.JevTransport(api_key=KEY, ledger_path=tmp_path / "budget.jsonl",
                             request_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                             request_source_path=source)
    source.write_text("changed")
    _, factory = fake_session(monkeypatch)
    with pytest.raises(jt.JevTransportError, match="^source_changed$"):
        caller.call("one", payload())
    factory.assert_not_called()


@pytest.mark.parametrize("change", ["budget", "source", "rate"])
def test_reopening_with_different_pins_is_rejected(tmp_path, monkeypatch, change):
    transport(tmp_path)
    kwargs = {"api_key": KEY, "ledger_path": tmp_path / "budget.jsonl", "request_source_sha256": SOURCE_HASH}
    if change == "budget":
        kwargs["budget_usd"] = "0.50"
    elif change == "source":
        kwargs["request_source_sha256"] = "b" * 64
    else:
        monkeypatch.setitem(jt.RATE_CONFIG, "checked_date", "2099-01-01")
    with pytest.raises(jt.JevTransportError, match="^ledger_configuration_mismatch$"):
        jt.JevTransport(**kwargs)


@pytest.mark.parametrize("mutation", ["truncate", "edit", "empty"])
def test_corrupted_ledger_is_fail_closed(tmp_path, monkeypatch, mutation):
    caller = transport(tmp_path)
    path = tmp_path / "budget.jsonl"
    original = path.read_bytes()
    if mutation == "truncate":
        path.write_bytes(original[:-1])
    elif mutation == "edit":
        path.write_bytes(original.replace(b'"retries":0', b'"retries":1'))
    else:
        path.write_bytes(b"")
    _, factory = fake_session(monkeypatch)
    with pytest.raises(jt.JevTransportError, match="^ledger_integrity_error$"):
        caller.call("one", payload())
    factory.assert_not_called()


def test_concurrent_instances_cannot_double_send_same_id(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    def send(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return response()
    session, _ = fake_session(monkeypatch, send)
    first, second = transport(tmp_path), transport(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.call, "same", payload())
        assert entered.wait(5)
        b = pool.submit(second.call, "same", payload())
        release.set()
        results = [a.result(timeout=5), b.result(timeout=5)]
    assert sorted(item["status"] for item in results) == ["not_run", "ok"]
    assert session.post.call_count == 1
    assert len(events(tmp_path)) == 3


def test_concurrent_distinct_requests_cannot_exceed_budget(tmp_path, monkeypatch):
    session, _ = fake_session(monkeypatch, requests.Timeout(KEY))
    first = transport(tmp_path, budget_usd="0.002752512")
    second = transport(tmp_path, budget_usd="0.002752512")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.call, "first", payload())
        b = pool.submit(second.call, "second", payload())
        results = [a.result(timeout=5), b.result(timeout=5)]
    assert sorted(item["status"] for item in results) == ["error", "not_run"]
    assert session.post.call_count == 1
    assert events(tmp_path)[-1]["data"]["accounted_nano_usd"] == jt.RESERVATION_NANO_USD


def test_fsync_failure_prevents_network_and_preserves_written_pending(tmp_path, monkeypatch):
    caller = transport(tmp_path)
    original = jt.os.fsync
    def fail_pending(handle):
        if len(events(tmp_path)) == 2:
            raise OSError(KEY)
        return original(handle)
    monkeypatch.setattr(jt.os, "fsync", fail_pending)
    _, factory = fake_session(monkeypatch)
    with pytest.raises(jt.JevTransportError, match="^ledger_io_error$"):
        caller.call("one", payload())
    factory.assert_not_called()
    monkeypatch.setattr(jt.os, "fsync", original)
    assert caller.call("one", payload())["error_type"] == "request_already_reserved"


@pytest.mark.parametrize("mutate", [lambda p: p.update(model="jev-latest"),
                                   lambda p: p.update(api_key=KEY),
                                   lambda p: p.update(state={"text": KEY}),
                                   lambda p: p.update(state={"value": float("nan")}),
                                   lambda p: p.update(questions={})])
def test_bad_payload_cannot_create_reservation(tmp_path, monkeypatch, mutate):
    caller = transport(tmp_path)
    data = payload()
    mutate(data)
    _, factory = fake_session(monkeypatch)
    with pytest.raises(jt.JevTransportError, match="^invalid_request_payload$"):
        caller.call("one", data)
    assert len(events(tmp_path)) == 1
    factory.assert_not_called()
