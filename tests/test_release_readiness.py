"""Readiness checks remain local, read-only, and fail on concrete blockers."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from castwell.library import Library


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_release.py"
spec = importlib.util.spec_from_file_location("release_readiness", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    library = Library(tmp_path / "data")
    media = library.root / "original.wav"
    media.write_bytes(b"readiness fixture media")
    entry = {"id": "release-fixture", "title": "Private title", "podcast": "Local fixture",
             "feed_url": "https://private.example/feed?token=never-output-this", "media_url": "https://private.example/audio"}
    library.import_entries([entry])
    library.update(entry["id"], audio=str(media), duration=12, cuts=[{"start": 3, "end": 7, "approved": False}],
                   transcript={"duration": 12, "segments": [{"start": 0, "end": 12, "text": "Private fixture speech"}]})
    responses = {
        "/api/settings": {"review_only": True, "ai_policy": "verified", "ai_configured": True, "detector": "auto",
                          "ai_base_url": "http://127.0.0.1:8081/v1", "ai_model": "castwell-local"},
        "/api/health": {"status": "ok", "version": "0.4.0"},
        "/api/diagnostics": {"checks": [{"id": "ffmpeg", "status": "ok", "detail": "Available"},
                                        {"id": "model", "status": "ok", "detail": "Cached model"},
                                        {"id": "ffprobe", "status": "ok", "detail": "Available"},
                                        {"id": "transcription", "status": "ok", "detail": "Installed"},
                                        {"id": "storage", "status": "ok", "detail": "Writable"}]},
        "/api/jobs": {"jobs": []},
        "/api/feeds/refresh/status": {"status": "idle"},
        "/api/models/status": {"status": "idle"},
        "/api/episodes": {"episodes": [{"id": entry["id"]}]},
        "/api/podcasts": {"podcasts": [{"id": "podcast-fixture", "ad_mode": "remove"}]},
    }
    calls = []

    def request(address, timeout=8):
        calls.append(address)
        if address == "http://127.0.0.1:8081/v1/models":
            return {"data": [{"id": "castwell-local"}]}
        return responses[address.removeprefix("http://127.0.0.1:8000")]

    monkeypatch.setattr(release, "get_json", request)
    monkeypatch.setattr(release, "listener_addresses", lambda port: ["127.0.0.1"])
    monkeypatch.setattr(release.shutil, "disk_usage", lambda directory: SimpleNamespace(free=30 * 1024**3))
    return SimpleNamespace(library=library, responses=responses, calls=calls, media=media)


def run(installation):
    return release.check_release("http://127.0.0.1:8000", installation.library.root, require_local_ai=True)


def status(report, identifier):
    return next(item["status"] for item in report["checks"] if item["id"] == identifier)


def test_ready_uses_only_local_readiness_and_preserves_library(installation):
    before = installation.library.get("release-fixture")
    report = run(installation)
    assert report["ready"] is True
    assert report["operational_ready"] is True and report["production_ready"] is False
    assert report["ad_removal_acceptance"]["status"] == "not_measured"
    assert report["inference_requests"] == report["library_mutations"] == 0
    assert installation.library.get("release-fixture") == before
    assert len(installation.calls) == 9
    assert all("chat/completions" not in address for address in installation.calls)
    serialized = json.dumps(report)
    for private in ("Private title", "Private fixture speech", "never-output-this", "private.example"):
        assert private not in serialized
    assert status(report, "accuracy_acceptance") == "warning"


@pytest.mark.parametrize("address", ["https://api.example/v1", "http://192.168.1.3:8000",
                                    "http://user:secret@127.0.0.1:8000", "http://127.0.0.1/?key=secret",
                                    "http://127.0.0.1/#secret", "http://127.0.0.1:bad", "http://127.0.0.1\\@example.com"])
def test_remote_or_credentialled_application_addresses_are_rejected(address):
    with pytest.raises(ValueError, match="loopback"):
        release.local_url(address)


def test_hosted_classifier_is_never_contacted(installation):
    installation.responses["/api/settings"]["ai_base_url"] = "https://paid.example/v1"
    report = run(installation)
    assert report["ready"] is False
    assert status(report, "local_classifier") == "error"
    assert all("paid.example" not in address for address in installation.calls)


@pytest.mark.parametrize("field,value", [("review_only", False), ("ai_policy", "legacy")])
def test_cut_safety_settings_block_release(installation, field, value):
    installation.responses["/api/settings"][field] = value
    report = run(installation)
    assert report["ready"] is False and status(report, "cut_approval") == "error"


def test_scene_automatic_profile_is_operational_without_claiming_accuracy_acceptance(installation):
    installation.responses["/api/settings"].update(ai_policy="scene", review_only=False, auto_approve_threshold=.90)
    report = run(installation)
    assert report["operational_ready"] and status(report, "cut_approval") == "ok"
    assert not report["production_ready"] and report["ad_removal_acceptance"]["status"] == "not_measured"
    assert report["inference_requests"] == 0
    installation.responses["/api/settings"]["auto_approve_threshold"] = .5
    assert status(run(installation), "cut_approval") == "error"


@pytest.mark.parametrize("review_only", [True, False, None, 1])
def test_fast_profile_requires_manual_review_and_never_claims_accuracy_acceptance(installation, review_only):
    installation.responses["/api/settings"].update(ai_policy="fast", review_only=review_only)
    report = run(installation)
    assert report["operational_ready"] is (review_only is True)
    assert status(report, "cut_approval") == ("ok" if review_only is True else "error")
    assert report["production_ready"] is False
    assert report["ad_removal_acceptance"]["status"] == "not_measured"
    assert report["inference_requests"] == report["library_mutations"] == 0
    assert status(report, "accuracy_acceptance") == "warning"


@pytest.mark.parametrize("addresses", [["0.0.0.0"], ["127.0.0.1", "::"], None])
def test_nonlocal_or_unverified_listeners_block_release(installation, monkeypatch, addresses):
    monkeypatch.setattr(release, "listener_addresses", lambda port: addresses)
    report = run(installation)
    assert report["ready"] is False and status(report, "listen_binding") == "error"


def test_uncached_speech_model_blocks_release(installation):
    installation.responses["/api/diagnostics"]["checks"][1]["status"] = "warning"
    report = run(installation)
    assert report["ready"] is False and status(report, "environment_model") == "error"


def test_incomplete_diagnostics_do_not_pass_release(installation):
    installation.responses["/api/diagnostics"] = {"checks": []}
    report = run(installation)
    assert report["ready"] is False and status(report, "environment_complete") == "error"


def test_missing_original_blocks_release_without_leaking_stored_path(installation):
    installation.media.unlink()
    report = run(installation)
    assert report["ready"] is False and status(report, "library_integrity") == "error"
    assert str(installation.library.root) not in json.dumps(report)


def test_media_outside_managed_library_blocks_portable_exports(installation, tmp_path):
    external = tmp_path / "external.wav"
    external.write_bytes(b"external recording")
    installation.library.update("release-fixture", audio=str(external))
    report = run(installation)
    assert report["ready"] is False and status(report, "library_integrity") == "error"


def test_invalid_revision_blocks_release(installation):
    installation.library.save_revision("release-fixture", [], "Before editing")
    with sqlite3.connect(installation.library.db) as connection:
        connection.execute("UPDATE cut_revisions SET cuts=?", ('{"invalid":"secret-value"}',))
    report = run(installation)
    assert report["ready"] is False and status(report, "library_integrity") == "error"
    assert "secret-value" not in json.dumps(report)


def test_invalid_cut_json_blocks_release_without_echoing_input(installation):
    with sqlite3.connect(installation.library.db) as connection:
        connection.execute("UPDATE episodes SET cuts=?", ('not valid secret=value',))
    report = run(installation)
    assert report["ready"] is False and status(report, "library_integrity") == "error"
    assert "secret=value" not in json.dumps(report)


def test_wrong_library_and_active_queue_block_release(installation):
    installation.responses["/api/episodes"] = {"episodes": [{"id": "other-library"}]}
    installation.responses["/api/jobs"] = {"jobs": [{"id": "active"}]}
    report = run(installation)
    assert report["ready"] is False
    assert status(report, "library_identity") == status(report, "queue_idle") == "error"
    assert "http://127.0.0.1:8081/v1/models" not in installation.calls


@pytest.mark.parametrize("path,identifier", [("/api/feeds/refresh/status", "feed_refresh_idle"),
                                          ("/api/models/status", "model_preparation_idle")])
def test_background_operations_block_release_when_episode_queue_is_idle(installation, path, identifier):
    installation.responses[path] = {"status": "running"}
    report = run(installation)
    assert report["ready"] is False
    assert status(report, "queue_idle") == "ok"
    assert status(report, identifier) == "error"
    if path == "/api/models/status":
        assert "http://127.0.0.1:8081/v1/models" not in installation.calls


def test_low_storage_blocks_batch_release(installation, monkeypatch):
    monkeypatch.setattr(release.shutil, "disk_usage", lambda directory: SimpleNamespace(free=1024**3))
    report = run(installation)
    assert report["ready"] is False and status(report, "free_storage") == "error"


def test_read_only_database_inspection_does_not_create_missing_library(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        release.inspect_library(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_redirect_handler_refuses_every_redirect():
    assert release.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://paid.example") is None


def test_large_library_response_remains_bounded_and_uses_get_without_credentials(monkeypatch):
    # A 7,000-episode catalogue can exceed the earlier 8 MiB bound. The limit is
    # still explicit so a malformed local response cannot consume unlimited RAM.
    body = json.dumps({"episodes": [], "padding": "x" * (9 * 1024 * 1024)}).encode()
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(body)

    monkeypatch.setattr(release, "build_opener", lambda *handlers: Opener())
    result = release.get_json("http://127.0.0.1:8000/api/episodes")
    assert result["episodes"] == [] and len(result["padding"]) == 9 * 1024 * 1024
    assert requests[0].method == "GET" and not requests[0].has_header("Authorization")
    monkeypatch.setattr(release, "RESPONSE_LIMIT", 64)
    with pytest.raises(ValueError, match="allowed size"):
        release.get_json("http://127.0.0.1:8000/api/episodes")


def test_listener_parser_detects_wildcard_and_ipv6(monkeypatch):
    monkeypatch.setattr(release.sys, "platform", "win32")
    monkeypatch.setattr(release.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=(
        "TCP 127.0.0.1:8000 0.0.0.0:0 LISTENING\n"
        "TCP [::]:8000 [::]:0 LISTENING\n"
        "TCP 127.0.0.1:9000 0.0.0.0:0 LISTENING\n")))
    assert release.listener_addresses(8000) == ["127.0.0.1", "::"]
