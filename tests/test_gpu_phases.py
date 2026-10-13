from unittest.mock import MagicMock

import pytest

from castwell import gpu_phases, processing


def session(monkeypatch, states):
    import requests
    client = MagicMock()
    client.__enter__.return_value = client
    replies = []
    for state in states:
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.status_code = 200
        reply.json.return_value = state
        replies.append(reply)
    client.get.side_effect = replies
    monkeypatch.setattr(requests, "Session", lambda: client)
    monkeypatch.setattr(gpu_phases.time, "sleep", lambda _seconds: None)
    return client


def test_waits_until_classifier_memory_is_released(monkeypatch):
    client = session(monkeypatch, [{"is_sleeping": False}, {"is_sleeping": True}])
    gpu_phases.wait_classifier_sleep("http://127.0.0.1:8081/v1")
    assert client.get.call_count == 2
    assert client.trust_env is False
    assert all(call.kwargs["allow_redirects"] is False for call in client.get.call_args_list)
    assert all(call.args[0].endswith("/props") for call in client.get.call_args_list)


@pytest.mark.parametrize("address", ["https://provider.invalid/v1", "http://localhost:8081/v1",
                                    "http://key@127.0.0.1:8081/v1", "http://127.0.0.1:8081/v1?key=x",
                                    "http://127.0.0.1:8081/other"])
def test_rejects_unowned_addresses_before_contact(monkeypatch, address):
    client = session(monkeypatch, [])
    with pytest.raises(ValueError):
        gpu_phases.wait_classifier_sleep(address)
    client.get.assert_not_called()


def test_missing_native_sleep_support_is_an_error(monkeypatch):
    session(monkeypatch, [{}])
    with pytest.raises(processing.ProcessingError, match="release GPU memory"):
        gpu_phases.wait_classifier_sleep("http://127.0.0.1:8081/v1")


def test_cancelled_speech_phase_never_contacts_classifier(monkeypatch):
    client = session(monkeypatch, [])
    with pytest.raises(processing.ProcessingCancelled):
        gpu_phases.wait_classifier_sleep("http://127.0.0.1:8081/v1", should_cancel=lambda: True)
    client.get.assert_not_called()


def test_busy_classifier_cannot_be_ignored(monkeypatch):
    session(monkeypatch, [{"is_sleeping": False}])
    values = iter([0, 16])
    monkeypatch.setattr(gpu_phases.time, "monotonic", lambda: next(values))
    with pytest.raises(processing.ProcessingError, match="still using GPU memory"):
        gpu_phases.wait_classifier_sleep("http://127.0.0.1:8081/v1")


def test_speech_cache_is_released_before_next_model(monkeypatch):
    model = MagicMock()
    monkeypatch.setattr(processing, "_cached_model", model)
    gpu_phases.release_speech_model()
    model.cache_clear.assert_called_once()
