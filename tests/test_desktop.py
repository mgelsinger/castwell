"""Desktop lifecycle proves isolation and CUDA requirements without model calls."""
from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest

from castwell import desktop


def test_portable_marker_and_installed_data_are_separate(tmp_path, monkeypatch):
    executable = tmp_path / "Program Files" / "Castwell" / "Castwell.exe"
    executable.parent.mkdir(parents=True)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Profile"))
    assert desktop.default_data_directory() == tmp_path / "Profile" / "Castwell"
    (executable.parent / "portable.flag").write_text("portable", encoding="utf-8")
    assert desktop.default_data_directory() == executable.parent / "Data"


@pytest.mark.parametrize("portable", [False, True])
def test_selected_data_folder_bootstrap_respects_explicit_override(tmp_path, monkeypatch, portable):
    executable = tmp_path / "Install folder" / "Castwell.exe"
    executable.parent.mkdir()
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Profile"))
    if portable:
        (executable.parent / "portable.flag").write_text("portable")
    expected = executable.parent / "desktop-preferences.json" if portable else tmp_path / "Profile/Castwell/desktop-preferences.json"
    assert desktop.bootstrap_preferences_path() == expected
    selected = tmp_path / "Podcast library with spaces"
    desktop.write_json(expected, {"schema_version": 1, "data_dir": str(selected)})
    assert desktop.preferred_data_directory() == selected
    assert desktop.preferred_data_directory(tmp_path / "Explicit library") == tmp_path / "Explicit library"
    expected.write_text('{"data_dir":42}', encoding="utf-8")
    assert desktop.preferred_data_directory() == desktop.default_data_directory()


def test_portable_default_library_follows_moved_executable(tmp_path, monkeypatch):
    original = tmp_path / "Original portable folder"
    original.mkdir()
    (original / "portable.flag").write_text("portable")
    (original / "Data").mkdir()
    (original / "Data/library-sentinel").write_text("existing library")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(original / "Castwell.exe"))
    desktop.save_data_selection(desktop.default_data_directory())
    preferences = json.loads((original / "desktop-preferences.json").read_text())
    assert preferences == {"schema_version": 2, "use_default": True, "data_dir": None}

    moved = tmp_path / "Relocated portable folder"
    original.rename(moved)
    monkeypatch.setattr(desktop.sys, "executable", str(moved / "Castwell.exe"))
    assert desktop.preferred_data_directory() == moved / "Data"
    assert (desktop.preferred_data_directory() / "library-sentinel").read_text() == "existing library"
    assert not original.exists()
    assert desktop.preferred_data_directory(tmp_path / "Explicit library") == tmp_path / "Explicit library"


@pytest.mark.parametrize("portable", [False, True])
def test_successful_gui_start_switches_custom_library_back_to_default(tmp_path, monkeypatch, portable):
    executable = tmp_path / "Application folder" / "Castwell.exe"
    executable.parent.mkdir()
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Profile"))
    if portable:
        (executable.parent / "portable.flag").write_text("portable")
    custom = tmp_path / "Custom absolute library" / "Data"
    desktop.save_data_selection(custom)
    assert desktop.preferred_data_directory() == custom
    preferences = json.loads(desktop.bootstrap_preferences_path().read_text())
    assert preferences == {"schema_version": 2, "use_default": False, "data_dir": str(custom)}

    selected = desktop.default_data_directory().resolve()
    # A safely recognizable legacy absolute default is upgraded on successful Start.
    desktop.write_json(desktop.bootstrap_preferences_path(), {"schema_version": 1, "data_dir": str(selected)})
    assert desktop.preferred_data_directory() == selected
    controller = desktop.DesktopController(selected, no_browser=True, persist_data_selection=True)
    monkeypatch.setattr(controller, "_probe_runtime", lambda: {"ready": True, "errors": []})
    monkeypatch.setattr(controller, "_launch", lambda *args, **kwargs: Child(909))
    monkeypatch.setattr(controller, "_wait_app", lambda: None)
    controller.start()
    assert controller.status == "ready"
    assert desktop.preferred_data_directory() == selected
    assert json.loads(desktop.bootstrap_preferences_path().read_text()) == {
        "schema_version": 2, "use_default": True, "data_dir": None}
    assert desktop.preferred_data_directory(custom) == custom


def test_library_lock_prevents_second_backend_and_releases_after_error(tmp_path):
    with pytest.raises(RuntimeError, match="fixture failed"):
        with desktop.LibraryLock(tmp_path, 8765, "owner"):
            owner = json.loads((tmp_path / "runtime/backend-owner.json").read_text())
            assert owner["port"] == 8765
            with pytest.raises(desktop.DesktopError, match="Another Castwell backend"):
                with desktop.LibraryLock(tmp_path, 8766, "other"):
                    pytest.fail("second lock admitted")
            raise RuntimeError("fixture failed")
    with desktop.LibraryLock(tmp_path, 8766, "newowner"):
        assert (tmp_path / "runtime/backend-owner.json").is_file()
    assert not (tmp_path / "runtime/backend-owner.json").exists()


def test_port_conflict_selects_fresh_loopback_listener():
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        occupied = foreign.getsockname()[1]
        selected = desktop.available_port(occupied)
        assert selected != occupied
        with socket.socket() as ours:
            ours.bind(("127.0.0.1", selected))
        assert foreign.getsockname()[1] == occupied


def test_frozen_binary_paths_never_use_external_path(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "C:/foreign/" + name + ".exe")
    paths = desktop.native_paths()
    assert paths == {"ffmpeg": tmp_path / "bin/ffmpeg.exe", "ffprobe": tmp_path / "bin/ffprobe.exe",
                     "llama": tmp_path / "llama/llama-server.exe"}


def test_guard_requires_existing_model_and_rejects_paid_remote_provider(tmp_path, monkeypatch):
    from castwell import config, processing
    calls = []
    monkeypatch.setattr(processing, "transcribe", lambda *args, **kwargs: calls.append(args) or "local speech")
    monkeypatch.setattr(processing, "detect_ads", lambda *args, **kwargs: calls.append(args) or [])
    monkeypatch.setattr(config, "test_classifier", lambda settings: {"ok": True})
    monkeypatch.setattr(config.Settings, "get", config.Settings.get)
    monkeypatch.setattr(config.Settings, "update", config.Settings.update)
    desktop.install_local_processing_guards()
    with pytest.raises(processing.ProcessingError, match="No model was downloaded"):
        processing.transcribe(tmp_path / "audio.wav", "missing-model", model_cache=str(tmp_path))
    assert calls == []
    prepared = tmp_path / "Prepared speech model"
    prepared.mkdir()
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (prepared / filename).write_bytes(b"fixture")
    assert processing.transcribe(tmp_path / "audio.wav", str(prepared)) == "local speech"
    assert calls[0][1] == str(prepared.resolve())
    with pytest.raises(processing.ProcessingError, match="local CUDA models only"):
        processing.detect_ads({}, "ai", config={"ai_base_url": "https://paid.example/v1"})
    with pytest.raises(ValueError, match="only permits its own CUDA"):
        config.test_classifier({"ai_base_url": "https://paid.example/v1"})


@pytest.mark.parametrize("reviewed_qwen", [False, True])
def test_owned_session_profile_preserves_saved_provider_configuration(tmp_path, monkeypatch, reviewed_qwen):
    from castwell import config, processing
    from castwell.library import Library
    library = Library(tmp_path)
    library.update_settings({"ai_base_url": "https://saved.example/v1", "ai_model": "saved-model",
                             "ai_reasoning": True, "detector": "auto", "review_only": False})
    for name in desktop._ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CASTWELL_AI_BASE_URL", "http://127.0.0.1:8089/v1")
    monkeypatch.setenv("CASTWELL_AI_MODEL", "castwell-local")
    monkeypatch.setattr(processing, "transcribe", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing, "detect_ads", lambda *args, **kwargs: [])
    monkeypatch.setattr(config, "test_classifier", lambda settings: {"ok": True})
    monkeypatch.setattr(config.Settings, "get", config.Settings.get)
    monkeypatch.setattr(config.Settings, "update", config.Settings.update)
    desktop.install_local_processing_guards("http://127.0.0.1:8089/v1", reviewed_qwen=reviewed_qwen)
    settings = config.Settings(library)
    effective = settings.get()
    assert effective["ai_base_url"] == "http://127.0.0.1:8089/v1"
    assert effective["ai_reasoning"] is reviewed_qwen and effective["review_only"] is True
    settings.update({"ai_base_url": effective["ai_base_url"], "ai_model": effective["ai_model"],
                     "ai_reasoning": False, "detector": "ai", "language": "en"})
    saved = library.get_settings()
    assert saved["ai_base_url"] == "https://saved.example/v1"
    assert saved["ai_model"] == "saved-model" and saved["ai_reasoning"] is True
    assert saved["detector"] == "auto" and saved["language"] == "en"


class Child:
    def __init__(self, pid):
        self.pid, self.stdin, self.terminated, self.waits = pid, io.BytesIO(), False, []
        self.code = None

    def poll(self):
        return self.code

    def wait(self, timeout):
        self.waits.append(timeout)
        self.code = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        pytest.fail("ordinary lifecycle must not force kill")


def test_activity_tracks_transcribing_and_does_not_stop_active_work(tmp_path, monkeypatch):
    controller = desktop.DesktopController(tmp_path, no_browser=True)
    child = controller.app_process = Child(456)
    controller.status = "ready"
    answers = {"/api/health": {"refresh_running": False},
               "/api/jobs": {"jobs": [{"episode_id": "a", "status": "transcribing"}]},
               "/api/models/status": {"status": "idle"}}
    monkeypatch.setattr(desktop, "_request_json", lambda address, session, **kwargs: answers[address.removeprefix(controller.base_url)])
    assert len(controller.activity()["jobs"]) == 1
    with pytest.raises(desktop.DesktopError, match="jobs are active"):
        controller.stop()
    assert child.stdin.getvalue() == b""
    assert not child.terminated
    assert controller.status == "ready"


def test_explicit_cancel_precedes_graceful_stop_and_only_owned_classifier(tmp_path, monkeypatch):
    controller = desktop.DesktopController(tmp_path, no_browser=True)
    app, model = Child(501), Child(502)
    controller.app_process = app
    controller.model_process, controller.status = model, "ready"
    requests = []
    activity = iter([{"jobs": [{"episode_id": "a"}], "refresh_running": False, "model_running": False},
                     {"jobs": [], "refresh_running": False, "model_running": False}])
    monkeypatch.setattr(controller, "activity", lambda: next(activity))
    monkeypatch.setattr(desktop, "_request_json", lambda address, session, **kwargs: requests.append((address, kwargs)) or {})
    assert controller.stop(cancel_jobs=True)
    assert requests == [(controller.base_url + "/api/episodes/batch/cancel", {"payload": {"ids": ["a"]}})]
    assert app.stdin.getvalue() == b"STOP\n"
    assert app.waits == [35]
    assert not app.terminated
    assert model.terminated and model.waits == [10]
    assert controller.status == "stopped"


def test_gui_snapshot_remains_responsive_during_owned_child_shutdown(tmp_path, monkeypatch):
    controller = desktop.DesktopController(tmp_path, no_browser=True)
    entered, release, snapshot_finished = threading.Event(), threading.Event(), threading.Event()
    class SlowChild(Child):
        def wait(self, timeout):
            entered.set()
            assert release.wait(4), "test shutdown was not released"
            return super().wait(timeout)
    controller.app_process, controller.status = SlowChild(601), "ready"
    monkeypatch.setattr(controller, "activity", lambda: {"jobs": [], "refresh_running": False, "model_running": False})
    errors = []
    def stop():
        try:
            controller.stop()
        except Exception as exc:
            errors.append(exc)
    stopping = threading.Thread(target=stop)
    stopping.start()
    reading = None
    try:
        assert entered.wait(2)
        def snapshot():
            assert controller.snapshot()["status"] == "stopping"
            snapshot_finished.set()
        reading = threading.Thread(target=snapshot)
        reading.start()
        assert snapshot_finished.wait(1), "GUI snapshot blocked on child shutdown"
    finally:
        release.set()
        stopping.join(3)
        if reading:
            reading.join(3)
    assert not errors and controller.status == "stopped"


@pytest.mark.parametrize("field", ["refresh_running", "model_running"])
def test_stop_refuses_feed_or_model_mutation(tmp_path, monkeypatch, field):
    controller = desktop.DesktopController(tmp_path)
    child = controller.app_process = Child(777)
    controller.status = "ready"
    activity = {"jobs": [], "refresh_running": False, "model_running": False}
    activity[field] = True
    monkeypatch.setattr(controller, "activity", lambda: activity)
    with pytest.raises(desktop.DesktopError, match="preparation is running"):
        controller.stop(cancel_jobs=True)
    assert not child.terminated and child.stdin.getvalue() == b""


def test_start_gpu_failure_never_constructs_child_or_changes_library(tmp_path, monkeypatch):
    controller = desktop.DesktopController(tmp_path, no_browser=True)
    monkeypatch.setattr(controller, "_probe_runtime", lambda: {"ready": False, "errors": ["NVIDIA GPU required"]})
    monkeypatch.setattr(controller, "_launch", lambda *a, **kw: pytest.fail("GPU failure launched a child"))
    controller.start()
    assert controller.status == "error" and "NVIDIA GPU" in controller.error
    assert not (tmp_path / "library.sqlite3").exists()
    assert controller.app_process is None and controller.model_process is None


@pytest.mark.parametrize("reviewed_qwen", [False, True])
def test_child_environment_drops_keys_and_uses_cuda_all_layers(tmp_path, monkeypatch, reviewed_qwen):
    model_file = tmp_path / "local.gguf"
    model_file.write_bytes(b"GGUFfixture")
    controller = desktop.DesktopController(tmp_path, gguf=model_file, no_browser=True)
    monkeypatch.setenv("CASTWELL_AI_KEY", "never-copy-me")
    monkeypatch.setenv("CASTWELL_AI_BASE_URL", "https://paid.example/v1")
    assert "CASTWELL_AI_KEY" not in controller._environment()
    assert "CASTWELL_AI_BASE_URL" not in controller._environment()
    monkeypatch.setattr(controller, "_probe_runtime", lambda: {"ready": True, "errors": []})
    monkeypatch.setattr(desktop, "is_reviewed_qwen", lambda path: reviewed_qwen)
    commands = []
    def launch(command, log, **kwargs):
        commands.append((command, kwargs))
        return Child(123 + len(commands))
    monkeypatch.setattr(controller, "_launch", launch)
    monkeypatch.setattr(controller, "_wait_app", lambda: None)
    monkeypatch.setattr(controller, "_wait_model", lambda: None)
    controller.start()
    assert controller.status == "ready"
    command = commands[0][0]
    assert command[command.index("--n-gpu-layers") + 1] == "all"
    assert command[command.index("--fit") + 1] == "off"
    assert command[command.index("--verbosity") + 1] == "4"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert "--offline" in command
    assert commands[1][1]["parent_control"] is True
    saved = (tmp_path / "runtime/launcher.json").read_text()
    assert "never-copy-me" not in saved and "paid.example" not in saved
    assert json.loads(saved)["classifier_profile"] == ("reviewed-qwen3.8-cuda" if reviewed_qwen else "generic-local-cuda")
    assert ("--reviewed-qwen" in commands[1][0]) is reviewed_qwen
    if reviewed_qwen:
        assert command[command.index("--reasoning") + 1] == "off"
        assert command[command.index("--reasoning-budget") + 1] == "1024"
        assert command[command.index("--n-predict") + 1] == "4096"


def test_reviewed_profile_requires_exact_bytes_and_cached_hash(tmp_path, monkeypatch):
    model = tmp_path / "Qwen3.8-27B-Q4_K_M.gguf"
    content = b"GGUFexact pinned fixture"
    monkeypatch.setattr(desktop, "REVIEWED_QWEN_BYTES", len(content))
    monkeypatch.setattr(desktop, "REVIEWED_QWEN_SHA256", hashlib.sha256(content).hexdigest())
    desktop._reviewed_qwen_hash.cache_clear()
    try:
        model.write_bytes(content)
        assert desktop.is_reviewed_qwen(model)
        assert desktop.is_reviewed_qwen(model)
        assert desktop._reviewed_qwen_hash.cache_info().hits == 1
        # A suggestive filename with different contents never receives the
        # evaluated reasoning profile, even when its byte count is identical.
        model.write_bytes(content[:-1] + b"X")
        import os
        metadata = model.stat()
        os.utime(model, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1000000))
        assert not desktop.is_reviewed_qwen(model)
        model.write_bytes(b"GGUFshort")
        assert not desktop.is_reviewed_qwen(model)
    finally:
        desktop._reviewed_qwen_hash.cache_clear()


@pytest.mark.parametrize("runtime_ready", [False, True])
def test_bootstrap_selection_saved_only_after_successful_gui_start(tmp_path, monkeypatch, runtime_ready):
    target = tmp_path / "Bootstrap/desktop-preferences.json"
    previous = tmp_path / "Previous library"
    desktop.write_json(target, {"schema_version": 1, "data_dir": str(previous)})
    monkeypatch.setattr(desktop, "bootstrap_preferences_path", lambda: target)
    controller = desktop.DesktopController(tmp_path / "Chosen library", no_browser=True, persist_data_selection=True)
    monkeypatch.setattr(controller, "_probe_runtime", lambda: {"ready": runtime_ready, "errors": [] if runtime_ready else ["NVIDIA GPU required"]})
    monkeypatch.setattr(controller, "_launch", lambda *args, **kwargs: Child(909))
    monkeypatch.setattr(controller, "_wait_app", lambda: None)
    controller.start()
    assert json.loads(target.read_text())["data_dir"] == str(controller.directory if runtime_ready else previous)
    assert controller.status == ("ready" if runtime_ready else "error")
    # An explicitly headless controller never rewrites GUI bootstrap preferences.
    headless = desktop.DesktopController(tmp_path / "Headless library", no_browser=True)
    monkeypatch.setattr(headless, "_probe_runtime", lambda: {"ready": runtime_ready, "errors": [] if runtime_ready else ["NVIDIA GPU required"]})
    monkeypatch.setattr(headless, "_launch", lambda *args, **kwargs: Child(910))
    monkeypatch.setattr(headless, "_wait_app", lambda: None)
    before = target.read_bytes()
    headless.start()
    assert target.read_bytes() == before


def test_classifier_requires_full_gpu_offload_proof(tmp_path):
    controller = desktop.DesktopController(tmp_path)
    log = tmp_path / "runtime/classifier.log"
    log.write_text("load_tensors: offloaded 15/41 layers to GPU\n", encoding="utf-8")
    with pytest.raises(desktop.DesktopError, match="did not offload all layers"):
        controller._verify_model_gpu_offload()
    log.write_text("load_tensors: offloaded 41/41 layers to GPU\n", encoding="utf-8")
    controller._verify_model_gpu_offload()


def test_readiness_rejects_foreign_listener_tag(monkeypatch):
    class Response:
        headers = {desktop.SESSION_HEADER: "foreign-session"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return b'{"status":"ok"}'
    monkeypatch.setattr(desktop, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(desktop.DesktopError, match="does not belong"):
        desktop._request_json("http://127.0.0.1:8765/api/health", "our-session")


def test_runtime_report_and_exit_code_are_available_without_console(tmp_path, monkeypatch, capsys):
    report = {"ready": False, "errors": ["NVIDIA GPU required"]}
    monkeypatch.setattr(desktop, "runtime_check", lambda: report)
    target = tmp_path / "path with spaces" / "report.json"
    assert desktop.main(["--check-runtime", "--report", str(target)]) == 1
    assert json.loads(target.read_text()) == report
    assert "NVIDIA GPU required" in capsys.readouterr().out


def test_atomic_report_retries_windows_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "launcher-state.json"
    target.write_text('{"status":"old"}', encoding="utf-8")
    actual_replace = desktop.os.replace
    attempts, sleeps = [], []
    def overlapping_reader(source, destination):
        attempts.append(destination)
        if len(attempts) < 3:
            raise PermissionError(13, "Windows sharing violation")
        actual_replace(source, destination)
    monkeypatch.setattr(desktop.os, "replace", overlapping_reader)
    monkeypatch.setattr(desktop.time, "sleep", sleeps.append)
    desktop.write_json(target, {"status": "ready"})
    assert len(attempts) == 3 and sleeps == [0.02, 0.04]
    assert json.loads(target.read_text())["status"] == "ready"
    assert list(tmp_path.glob("*.tmp")) == []


def test_report_failure_is_bounded_and_never_prevents_owned_shutdown(tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    target.write_text('{"status":"previous"}', encoding="utf-8")
    attempts = []
    def locked(source, destination):
        attempts.append(destination)
        raise PermissionError(13, "Report is locked")
    monkeypatch.setattr(desktop.os, "replace", locked)
    monkeypatch.setattr(desktop.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        desktop.write_json(target, {"status": "new"})
    assert len(attempts) == 6
    assert json.loads(target.read_text())["status"] == "previous"
    assert list(tmp_path.glob("*.tmp")) == []
    controller = desktop.DesktopController(tmp_path / "Library", report=target)
    controller.app_process, controller.status = Child(701), "ready"
    monkeypatch.setattr(controller, "activity", lambda: {"jobs": [], "refresh_running": False, "model_running": False})
    assert controller.stop()
    assert controller.app_process.stdin.getvalue() == b"STOP\n"
    assert controller.status == "stopped"


def test_gui_tick_reschedules_after_report_error_and_stops_for_closed_window():
    class Window:
        exists = True
        def __init__(self):
            self.scheduled = []
        def winfo_exists(self):
            return self.exists
        def after(self, delay, callback):
            self.scheduled.append((delay, callback))
    window, errors = Window(), []
    def update():
        raise PermissionError("reader holds report")
    def next_tick():
        pass
    desktop._run_gui_tick(window, update, next_tick, lambda: errors.append("refresh failed"))
    assert window.scheduled == [(350, next_tick)] and errors == ["refresh failed"]
    window.exists = False
    desktop._run_gui_tick(window, lambda: None, next_tick, lambda: None)
    assert len(window.scheduled) == 1


@pytest.mark.parametrize("mode", ["ready", "negative", "nonzero", "stale", "wrong_frozen", "timeout"])
def test_owned_runtime_probe_rejects_stale_failure_and_reaps_timeout(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "configure_native_runtime", lambda: {})
    controller = desktop.DesktopController(tmp_path)
    target = tmp_path / "runtime/runtime-check.json"
    desktop.write_json(target, {"ready": True, "frozen": True})
    class Probe(Child):
        def wait(self, timeout):
            if mode == "timeout" and not self.terminated:
                import subprocess
                raise subprocess.TimeoutExpired("owned runtime probe", timeout)
            self.code = 1 if mode in {"negative", "nonzero"} else 0
            self.waits.append(timeout)
            return self.code
    child, commands = Probe(802), []
    def launch(command, logfile, **kwargs):
        commands.append(command)
        assert not target.exists(), "probe reused the previous ready report"
        if mode != "stale":
            desktop.write_json(target, {"ready": mode != "negative", "frozen": mode != "wrong_frozen",
                                        "errors": ["NVIDIA GPU required"] if mode == "negative" else []})
        return child
    monkeypatch.setattr(controller, "_launch", launch)
    if mode == "ready":
        assert controller._probe_runtime()["ready"] is True
    else:
        with pytest.raises(desktop.DesktopError):
            controller._probe_runtime()
    assert commands[0][1] == "--check-runtime" and "--report" in commands[0]
    assert controller.runtime_process is None and child.poll() is not None
    assert child.terminated is (mode == "timeout")
