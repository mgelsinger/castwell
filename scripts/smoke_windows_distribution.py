#!/usr/bin/env python3
"""Verify built Windows distributions without external Python or FFmpeg.

The Python interpreter runs this verification harness only. Both tested apps use
their frozen runtime, a restricted PATH, and an isolated profile and library.
No weights are downloaded and no classifier service is contacted. CUDA ASR
requires an existing model directory and an explicitly supplied speech file.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid
import wave
from xml.etree import ElementTree as ET
import zipfile


APP_ID = "{B38AE33D-2865-4B30-97CF-9887669E948D}"
WORKSPACE_PREFIX = "Castwell Windows distribution "
CREATE_NO_WINDOW = 0x08000000
TRANSCRIPT = {"language": "en", "duration": 12.0, "segments": [
    {"id": 0, "start": 0.0, "end": 3.0, "text": "A synthetic introduction."},
    {"id": 1, "start": 3.0, "end": 7.0, "text": "A manually selected demonstration interval."},
    {"id": 2, "start": 7.0, "end": 12.0, "text": "The synthetic recording continues."},
]}


def note(message):
    print(message, flush=True)


def owned(path, workspace):
    path, workspace = Path(path).resolve(), Path(workspace).resolve()
    if path != workspace and workspace not in path.parents:
        raise AssertionError(f"Path escaped the owned workspace: {path}")
    return path


def cleanup_owned_workspace(workspace):
    """Remove the exact temporary root, tolerating already-removed entries."""
    workspace = Path(workspace).resolve()
    assert owned(workspace, workspace) == workspace
    assert workspace.parent == Path(tempfile.gettempdir()).resolve()
    assert workspace.name.startswith(WORKSPACE_PREFIX)

    def onerror(_operation, _path, failure):
        error = failure[1]
        if not isinstance(error, FileNotFoundError):
            raise error.with_traceback(failure[2])

    # Inno may remove its remaining directory after scandir cached an entry.
    # Ignore only that disappearance; permission and other failures remain fatal.
    shutil.rmtree(workspace, onerror=onerror)


def restricted_environment(profile):
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CASTWELL_") or name in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(name)
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment.update(
        PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
        APPDATA=str(profile / "Roaming"), LOCALAPPDATA=str(profile / "Local"),
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        HF_HOME=str(profile / "Model cache"),
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
    )
    for directory in (profile / "Roaming", profile / "Local"):
        directory.mkdir(parents=True, exist_ok=True)
    assert shutil.which("python", path=environment["PATH"]) is None
    assert shutil.which("ffmpeg", path=environment["PATH"]) is None
    assert shutil.which("ffprobe", path=environment["PATH"]) is None
    return environment


def request(base, route, body=None, method=None, *, raw=None, content_type=None):
    headers = {"Origin": base}
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    query = Request(base + route, data=raw, headers=headers, method=method)
    try:
        with build_opener(ProxyHandler({})).open(query, timeout=30) as response:
            data = response.read()
            return response.status, dict(response.headers), data
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1500]
        raise AssertionError(f"{method or 'GET'} {route}: HTTP {error.code}: {detail}") from error


def api(base, route, body=None, method=None):
    status, _, data = request(base, route, body, method)
    assert 200 <= status < 300, (route, status)
    return json.loads(data)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop_owned_process(process, timeout=15):
    if process.poll() is not None:
        return
    try:
        process.stdin.write(b"STOP\n")
        process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=timeout)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        # The PID came from this exact Popen and is still running. Restrict the
        # forced fallback to that process tree, never an executable-name match.
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        subprocess.run([str(windows / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        process.wait(timeout=5)


def generate_audio(path):
    rate = 16000
    samples = b"".join(struct.pack("<h", int(4500 * math.sin(2 * math.pi * 330 * sample / rate)))
                       for sample in range(rate))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        for _ in range(12):
            output.writeframesraw(samples)
        output.writeframes(b"")


class Feed(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/feed.xml":
            rss = ET.Element("rss", version="2.0")
            channel = ET.SubElement(rss, "channel")
            ET.SubElement(channel, "title").text = "Distribution demonstration"
            for number in range(2):
                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "title").text = f"A short demonstration {number + 1}"
                ET.SubElement(item, "guid").text = f"distribution-demo-{number}"
                ET.SubElement(item, "enclosure", url=self.server.base + "/audio.wav", type="audio/wav")
            data, mime = ET.tostring(rss, encoding="utf-8"), "application/rss+xml"
        elif self.path == "/audio.wav":
            data, mime = self.server.audio, "audio/wav"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@contextmanager
def feed_server(audio):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Feed)
    server.base = f"http://127.0.0.1:{server.server_port}"
    server.audio = audio.read_bytes()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def check_runtime(executable, environment, output, bundle, expect_no_gpu=False):
    output.unlink(missing_ok=True)
    completed = subprocess.run([str(executable), "--check-runtime", "--report", str(output)],
                               cwd=executable.parent, env=environment, capture_output=True,
                               timeout=90, creationflags=CREATE_NO_WINDOW)
    assert output.is_file(), "Windowed launcher did not write its requested runtime report"
    report = json.loads(output.read_text(encoding="utf-8-sig"))
    assert report["frozen"] is True, "Distribution must use its frozen Python runtime"
    cuda = report["cuda"]
    if expect_no_gpu:
        assert completed.returncode != 0 and not report["ready"] and not cuda["ready"], report
        assert any("CUDA" in error or "NVIDIA" in error for error in report["errors"]), report["errors"]
    else:
        assert completed.returncode == 0 and report["ready"] is True, report
        assert cuda["ready"] and cuda["device_count"] > 0, cuda
        assert cuda["float16_supported"] and cuda["allocation_verified"] and cuda["kernel_verified"], cuda
        assert cuda["vad"]["ready"] and cuda["vad"]["provider"] == "CUDAExecutionProvider" and cuda["vad"]["cpu_fallback_disabled"], cuda["vad"]
    assert report["transcription"] == {"device": "cuda", "compute_type": "float16", "implicit_downloads": False}, report["transcription"]
    llama_cuda = report["llama_cuda_dlls"]
    assert llama_cuda["ready"] and llama_cuda["files"], llama_cuda
    for record in llama_cuda["files"]:
        assert record["exists"] and owned(record["path"], bundle).is_file(), record
    owned(report["bundle_root"], bundle)
    for name in ("ffmpeg", "ffprobe", "llama"):
        record = report["binaries"][name]
        assert record["exists"] and record["ok"], (name, record)
        assert owned(record["path"], bundle).is_file(), record
    for name in ("faster_whisper", "ctranslate2", "onnxruntime"):
        assert report["imports"][name]["ok"], (name, report["imports"][name])
    return report


def smoke_no_gpu(executable, bundle, workspace, label, artifacts, *, force_visibility_mask=False):
    note(f"Windows distribution: {label} clearly rejects unsupported GPU inference")
    area = owned(workspace / label, workspace)
    area.mkdir()
    environment = restricted_environment(area / "Profile")
    if force_visibility_mask:
        environment["CUDA_VISIBLE_DEVICES"] = "-1"
    runtime = check_runtime(executable, environment, artifacts / f"{label}-runtime.json", bundle, expect_no_gpu=True)
    library = area / "Library with spaces"
    library.mkdir()
    sentinel = library / "keep-on-uninstall.txt"
    sentinel.write_text("Owned test library must survive app removal.\n", encoding="utf-8")
    port = free_port()
    report_path = artifacts / f"{label}-rejection.json"
    report_path.unlink(missing_ok=True)
    completed = subprocess.run([str(executable), "--serve", "--parent-control", "--data-dir", str(library),
                                "--port", str(port), "--report", str(report_path)], env=environment,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                               creationflags=CREATE_NO_WINDOW)
    assert completed.returncode != 0 and report_path.is_file(), "Unsupported GPU must stop serving with a clear report"
    rejection = json.loads(report_path.read_text(encoding="utf-8-sig"))
    assert rejection["status"] == "error" and any(word in rejection["error"] for word in ("CUDA", "NVIDIA")), rejection
    try:
        request(f"http://127.0.0.1:{port}", "/api/health")
    except (URLError, TimeoutError, ConnectionError):
        pass
    else:
        raise AssertionError("Unsupported GPU must not start a fallback server")
    return {"version": runtime["version"], "expected_gpu_rejection": True, "visibility_mask_simulation": force_visibility_mask,
            "model_inference_run": False, "cuda": runtime["cuda"],
            "library": str(library), "sentinel": str(sentinel)}


@contextmanager
def running_app(executable, library, environment, log_path, speech_model=None):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    command = [str(executable), "--serve", "--parent-control", "--data-dir", str(library), "--port", str(port)]
    if speech_model:
        command += ["--speech-model", str(speech_model)]
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=executable.parent, env=environment,
                                   stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"Frozen app exited during startup ({process.returncode}); see {log_path}")
                try:
                    if api(base, "/api/health").get("status") == "ok":
                        break
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(.15)
            else:
                raise AssertionError(f"Frozen app did not become ready; see {log_path}")
            yield base
        finally:
            stop_owned_process(process)
            backend_log = library / "runtime/backend.log"
            if backend_log.is_file():
                shutil.copyfile(backend_log, log_path.with_name(log_path.stem + "-backend.log"))
            assert process.returncode == 0, f"Owned frozen process failed to shut down cleanly: {process.returncode}"


def wait_episode(base, identifier, status, *, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = api(base, f"/api/episodes/{identifier}")
        if record["status"] == "error":
            raise AssertionError(record.get("error") or "Episode job failed")
        if record["status"] == status:
            return record
        time.sleep(.15)
    raise AssertionError(f"Episode {identifier} did not reach {status}")


def upload(base, source):
    boundary = "castwell-smoke-" + uuid.uuid4().hex
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{source.name}"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, _, result = request(base, "/api/audio", method="POST", raw=data,
                                content_type=f"multipart/form-data; boundary={boundary}")
    assert status == 201
    return json.loads(result)


def duration(binary, audio, environment):
    completed = subprocess.run([str(binary), "-v", "error", "-show_entries", "format=duration",
                                "-of", "json", str(audio)], env=environment, capture_output=True,
                               check=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    return float(json.loads(completed.stdout)["format"]["duration"])


def visible_launcher_window(identifier):
    """Read only windows belonging to the freshly launched test process."""
    import ctypes
    from ctypes import wintypes
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    @callback_type
    def visit(handle, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value == identifier and user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            if "Castwell" in title.value:
                windows.append(title.value)
        return True
    user32.EnumWindows(visit, 0)
    return bool(windows)


def smoke_launcher(executable, library, environment, report_path, log_path, speech_model, *, ready_hook=None):
    note("Windows distribution: real desktop launcher and owned server shutdown")
    report_path.unlink(missing_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    command = [str(executable), "--no-browser", "--auto-start", "--parent-control",
               "--port", str(port), "--report", str(report_path)]
    if speech_model:
        command += ["--speech-model", str(speech_model)]
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=executable.parent, env=environment,
                                   stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 60
            state = {}
            while time.monotonic() < deadline:
                assert process.poll() is None, f"Desktop launcher exited early; see {log_path}"
                if report_path.is_file():
                    state = json.loads(report_path.read_text(encoding="utf-8-sig"))
                    assert not state.get("error"), state.get("error")
                    if state.get("app_pid"):
                        try:
                            if api(base, "/api/health").get("status") == "ok":
                                break
                        except (URLError, TimeoutError, ConnectionError):
                            pass
                time.sleep(.15)
            else:
                raise AssertionError("Desktop launcher did not start its owned local service")
            assert state["port"] == port and Path(state["data_dir"]).resolve() == library.resolve(), state
            assert state["app_pid"] != process.pid and not state.get("model_pid"), state
            assert visible_launcher_window(process.pid), "Expected a visible native Castwell launcher window"
            if ready_hook:
                ready_hook(base)
        finally:
            stop_owned_process(process, timeout=20)
            assert process.returncode == 0, f"Native launcher did not exit cleanly: {process.returncode}"
        try:
            request(base, "/api/health")
        except (URLError, TimeoutError, ConnectionError):
            pass
        else:
            raise AssertionError("Desktop launcher left its owned local server running")
    return {"visible_native_window": True, "owned_server_started": True, "owned_server_stopped": True,
            "browser_opened": False, "automatic_weight_downloads": False}


def smoke_portable_relocation(executable, workspace, artifacts, speech_model, audio):
    """Move the whole portable folder and verify real library/media continuity."""
    note("Windows distribution: portable default Data survives folder relocation")
    package = owned(executable.parent, workspace)
    assert (package / "portable.flag").is_file()
    library = owned(package / "Data", workspace)
    library.mkdir(parents=True, exist_ok=True)
    sentinel = library / "preserve-relocated-library.txt"
    sentinel.write_text("The adjacent portable library moves with the application.\n", encoding="utf-8")
    environment = restricted_environment(workspace / "Relocation profile")
    expected = {}

    def seed(base):
        original = upload(base, audio)
        identifier = original["id"]
        api(base, f"/api/episodes/{identifier}/transcript", TRANSCRIPT, "POST")
        api(base, f"/api/episodes/{identifier}/cuts", {"cuts": [
            {"start": 3, "end": 7, "approved": True, "confidence": 1,
             "source": "manual", "reason": "Known synthetic relocation interval"}]}, "POST")
        assert api(base, "/api/render", {"ids": [identifier]}, "POST")["queued"] == 1
        wait_episode(base, identifier, "ready")
        api(base, f"/api/episodes/{identifier}/state", {"favorite": True, "position": 4.5}, "PATCH")
        expected["episode_id"] = identifier
        expected["transcript"] = api(base, f"/api/episodes/{identifier}/transcript?format=json")
        for version in ("original", "cleaned"):
            expected[version + "_sha256"] = hashlib.sha256(request(base, f"/media/{identifier}/{version}")[2]).hexdigest()

    smoke_launcher(executable, library, environment, artifacts / "relocation-before-state.json",
                   artifacts / "relocation-before.log", speech_model, ready_hook=seed)
    preferences = json.loads((package / "desktop-preferences.json").read_text(encoding="utf-8"))
    assert preferences.get("schema_version") == 2 and preferences.get("use_default") is True and preferences.get("data_dir") is None, preferences
    assert (library / "library.sqlite3").is_file()
    destination = owned(workspace / "Relocated portable package with spaces" / "Castwell", workspace)
    assert package != destination and not destination.exists()
    destination.parent.mkdir()
    # Both resolved paths are inside the exact temporary workspace. Move only
    # this stopped portable package, never a live library or unrelated checkout.
    shutil.move(str(package), str(destination))
    assert not package.exists()
    relocated_library = destination / "Data"
    assert (relocated_library / sentinel.name).read_text(encoding="utf-8") == "The adjacent portable library moves with the application.\n"

    def verify(base):
        identifier = expected["episode_id"]
        record = api(base, f"/api/episodes/{identifier}")
        assert record["has_audio"] and record["has_cleaned"] and record["has_transcript"], record
        assert record["favorite"] and abs(record["position"] - 4.5) < .01
        assert len(record["cuts"]) == 1 and record["cuts"][0]["approved"]
        transcript = api(base, f"/api/episodes/{identifier}/transcript?format=json")
        assert transcript == expected["transcript"], "Relocation changed the imported transcript"
        for version in ("original", "cleaned"):
            digest = hashlib.sha256(request(base, f"/media/{identifier}/{version}")[2]).hexdigest()
            assert digest == expected[version + "_sha256"], f"Relocation changed {version} audio"

    smoke_launcher(destination / executable.name, relocated_library, environment,
                   artifacts / "relocation-after-state.json", artifacts / "relocation-after.log", speech_model, ready_hook=verify)
    assert not package.exists(), "The relocated launcher recreated the old application or Data path"
    return {"default_data_resolved_after_move": True, "original_and_cleaned_audio_unchanged": True,
            "transcript_and_cut_state_preserved": True, "listening_state_preserved": True,
            "visible_native_window": True, "asr_or_ad_classifier_inference_run": False,
            "original_data_dir": str(library), "relocated_data_dir": str(relocated_library),
            "episode_id": expected["episode_id"]}


def smoke_app(executable, bundle, workspace, label, audio, artifacts, speech_model=None, speech_audio=None):
    note(f"Windows distribution: {label} frozen runtime and isolated audio workflow")
    area = owned(workspace / label, workspace)
    area.mkdir()
    environment = restricted_environment(area / "Profile")
    runtime = check_runtime(executable, environment, artifacts / f"{label}-runtime.json", bundle)
    library = area / "Library with spaces"
    library.mkdir()
    sentinel = library / "keep-on-uninstall.txt"
    sentinel.write_text("Owned test library must survive app removal.\n", encoding="utf-8")
    with running_app(executable, library, environment, artifacts / f"{label}-server.log", speech_model) as base:
        _, _, page = request(base, "/")
        assert b'podcast-rail' in page and b'library-rails' in page and b'Castwell' in page
        _, _, application = request(base, "/static/app.js")
        _, _, stylesheet = request(base, "/static/style.css")
        assert b'renderDiscovery' in application and b'Continue listening' in application
        assert b'podcast-rail' in stylesheet
        settings = api(base, "/api/settings")
        assert not settings.get("ai_configured"), "Fresh test profile must not inherit a classifier"
        api(base, "/api/settings", {"detector": "heuristic", "review_only": True}, "PATCH")
        diagnostics = api(base, "/api/diagnostics")
        checks = {item["id"]: item for item in diagnostics["checks"]}
        for identifier in ("ffmpeg", "ffprobe", "transcription", "storage"):
            assert checks[identifier]["status"] == "ok", checks[identifier]
        if not speech_model:
            assert checks["model"]["status"] == "warning", "No speech weights should be bundled or downloaded"
        else:
            assert checks["model"]["status"] == "ok", checks["model"]
        original = upload(base, audio)
        assert abs(original["duration"] - 12) < .05
        api(base, f'/api/episodes/{original["id"]}/transcript', TRANSCRIPT, "POST")
        api(base, f'/api/episodes/{original["id"]}/cuts', {"cuts": [
            {"start": 3, "end": 7, "approved": True, "confidence": 1,
             "source": "manual", "reason": "Known synthetic interval"}]}, "POST")
        rendered = api(base, "/api/render", {"ids": [original["id"]]}, "POST")
        assert rendered["queued"] == 1, rendered
        record = wait_episode(base, original["id"], "ready")
        assert record["has_cleaned"] and abs(record["removed_seconds"] - 4) < .05
        exported = area / "Rendered known cut.mp3"
        _, _, data = request(base, f'/media/{original["id"]}/cleaned')
        exported.write_bytes(data)
        measured = duration(runtime["binaries"]["ffprobe"]["path"], exported, environment)
        assert abs(measured - 8) < .15, measured
        subprocess.run([runtime["binaries"]["ffmpeg"]["path"], "-v", "error", "-i", str(exported),
                        "-f", "null", "-"], env=environment, capture_output=True, check=True,
                       timeout=30, creationflags=CREATE_NO_WINDOW)
        with feed_server(audio) as feed:
            imported = api(base, "/api/feeds", {"url": feed + "/feed.xml"}, "POST")
            assert imported["added"] == 2, imported
            show = next(item for item in api(base, "/api/podcasts")["podcasts"]
                        if item["title"] == "Distribution demonstration")
            api(base, f'/api/podcasts/{show["id"]}', {"ad_mode": "keep"}, "PATCH")
            entries = [item for item in api(base, "/api/episodes")["episodes"] if item["podcast_id"] == show["id"]]
            ids = [item["id"] for item in entries]
            assert len(ids) == 2
            downloaded = api(base, "/api/download", {"ids": ids}, "POST")
            assert downloaded["queued"] == 2, downloaded
            for identifier in ids:
                wait_episode(base, identifier, "downloaded")
                api(base, f"/api/episodes/{identifier}/transcript", TRANSCRIPT, "POST")
            prepared = api(base, "/api/process", {"ids": ids}, "POST")
            assert prepared["queued"] == 2 and all(item["ad_mode"] == "keep" for item in prepared["results"]), prepared
            for identifier in ids:
                kept = wait_episode(base, identifier, "ready")
                assert kept["has_transcript"] and kept["cuts"] == [] and not kept["has_cleaned"]
            changed = api(base, "/api/episodes/batch/state", {"ids": ids, "favorite": True, "played": True}, "PATCH")
            assert changed["updated"] == 2
        _, _, archive_data = request(base, "/api/export/archive", {"ids": [original["id"], *ids]}, "POST")
        with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read("report.json"))
            assert len(manifest["included"]) == 3 and manifest["omitted"] == [], manifest
            assert sorted(item["version"] for item in manifest["included"]) == ["cleaned", "original", "original"]
        speech = None
        if speech_audio:
            assert speech_model, "A supplied speech recording requires cached model weights"
            recorded = upload(base, speech_audio)
            queued = api(base, f'/api/episodes/{recorded["id"]}/process', {"remove_ads": False}, "POST")
            assert queued["queued"]
            transcribed = wait_episode(base, recorded["id"], "ready", timeout=180)
            assert transcribed["has_transcript"] and not transcribed["cuts"]
            transcript = api(base, f'/api/episodes/{recorded["id"]}/transcript?format=json')
            assert any(item.get("text", "").strip() for item in transcript["segments"]), "Cached-model ASR must produce speech text"
            speech = {"segments": len(transcript["segments"]), "duration": transcribed["duration"], "cached_model": True}
    assert sentinel.read_text(encoding="utf-8") == "Owned test library must survive app removal.\n"
    gui_library = executable.parent / "Data" if (executable.parent / "portable.flag").is_file() else Path(environment["LOCALAPPDATA"]) / "Castwell"
    owned(gui_library, workspace).mkdir(parents=True, exist_ok=True)
    gui_sentinel = gui_library / "preserve-default-library.txt"
    gui_sentinel.write_text("Keep the default GUI library when uninstalling.\n", encoding="utf-8")
    launcher = smoke_launcher(executable, gui_library, environment,
                              artifacts / f"{label}-launcher-state.json", artifacts / f"{label}-launcher.log", speech_model)
    preference_path = executable.parent / "desktop-preferences.json" if (executable.parent / "portable.flag").is_file() else gui_library / "desktop-preferences.json"
    preferences = json.loads(preference_path.read_text(encoding="utf-8"))
    assert preferences.get("schema_version") == 2 and preferences.get("use_default") is True and preferences.get("data_dir") is None, preferences
    return {"version": runtime["version"], "runtime_ready": True, "frozen": True, "cuda": runtime["cuda"], "transcription": runtime["transcription"],
            "launcher": launcher, "external_python_on_path": False,
            "external_ffmpeg_on_path": False, "rendered_seconds": measured,
            "feed_episodes": len(ids), "archive_files": 3, "speech": speech, "asr_inference_run": speech is not None,
            "semantic_default_library_preference": True,
            "library": str(library), "sentinel": str(sentinel), "gui_sentinel": str(gui_sentinel),
            "gui_default_library": str(gui_library)}


def extract_portable(archive, destination, workspace):
    destination = owned(destination, workspace)
    destination.mkdir()
    with zipfile.ZipFile(archive) as source:
        assert len(source.infolist()) < 50000
        assert sum(item.file_size for item in source.infolist()) < 10 * 1024**3
        for item in source.infolist():
            assert not item.filename.startswith(("/", "\\"))
            target = owned(destination / item.filename, destination)
            assert not stat.S_ISLNK(item.external_attr >> 16), item.filename
            parts = {part.lower() for part in Path(item.filename.replace("\\", "/")).parts}
            assert not parts.intersection({".git", ".local", "secrets"}), item.filename
            assert target.suffix.lower() not in {".gguf", ".safetensors"}, "Weights must remain separate"
            assert target.name.lower() not in {".env", "index.json", "library.sqlite3", "library.sqlite3-wal", "library.sqlite3-shm", "desktop-preferences.json", "settings.json", "model.bin", "transcript.json", "secrets.json"}, item.filename
            source.extract(item, destination)
    executables = list(destination.glob("*/Castwell.exe"))
    assert len(executables) == 1, "Portable ZIP must contain one Castwell/Castwell.exe"
    executable = executables[0]
    assert (executable.parent / "portable.flag").is_file(), "Portable data marker is missing"
    assert not any((executable.parent / name).exists() for name in ("data", "tests", "scripts", ".local", ".git"))
    return executable


def uninstall_entries(app_id):
    import winreg
    key_name = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{app_id}_is1"
    found = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | view) as key:
                values = {}
                count = winreg.QueryInfoKey(key)[1]
                for index in range(count):
                    name, value, _ = winreg.EnumValue(key, index)
                    values[name] = value
                if values not in found:
                    found.append(values)
        except FileNotFoundError:
            pass
    return found


def shortcut_metadata(path):
    import base64
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = """[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CASTWELL_SMOKE_SHORTCUT)
[pscustomobject]@{target=$link.TargetPath;arguments=$link.Arguments;working_directory=$link.WorkingDirectory} | ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment["CASTWELL_SMOKE_SHORTCUT"] = str(path)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               env=environment, capture_output=True, check=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
    return json.loads(completed.stdout.decode("utf-8-sig"))


def smoke_installer(installer, workspace, artifacts, audio, app_id, speech_model, speech_audio, expect_no_gpu=False, expected_version=None, *, check_masked_gpu_rejection=False, verify_install_hook=None):
    assert not uninstall_entries(app_id), "Refusing to replace an existing Castwell installation or uninstall entry"
    install = owned(workspace / "Installed app with spaces", workspace)
    group = "Castwell"
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / group
    assert not start_menu.exists(), "Refusing to replace an existing Castwell Start menu group"
    profile = workspace / "Installer profile"
    environment = restricted_environment(profile)
    external_library = profile / "Local/Castwell"
    external_library.mkdir(parents=True)
    sentinel = external_library / "preserve-library.txt"
    sentinel.write_text("Uninstall must preserve user library files.\n", encoding="utf-8")
    note("Windows distribution: silent per-user install, shortcut and uninstall registry")
    uninstaller = install / "unins000.exe"
    result = None
    try:
        completed = subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                    "/CURRENTUSER", f"/DIR={install}",
                                    f"/LOG={artifacts / 'installer.log'}"], env=environment,
                                   capture_output=True, timeout=180, creationflags=CREATE_NO_WINDOW)
        assert completed.returncode == 0, f"Installer failed: {completed.returncode}"
        executable = install / "Castwell.exe"
        assert executable.is_file() and uninstaller.is_file()
        assert not (install / "portable.flag").exists(), "Installed app must use the user profile by default"
        entries = uninstall_entries(app_id)
        assert len(entries) == 1, entries
        entry = entries[0]
        assert entry["DisplayName"] == "Castwell" and entry["DisplayVersion"] == expected_version, entry
        assert Path(entry["InstallLocation"]).resolve() == install.resolve(), entry
        assert str(uninstaller).lower() in entry["UninstallString"].lower(), entry
        shortcut = start_menu / "Castwell.lnk"
        assert shortcut.is_file(), f"Missing current-user start-menu shortcut: {shortcut}"
        link = shortcut_metadata(shortcut)
        assert Path(link["target"]).resolve() == executable.resolve(), link
        assert not link["arguments"], link
        assert Path(link["working_directory"]).resolve() == install.resolve(), link
        identity = verify_install_hook(install) if verify_install_hook else None
        result = (smoke_no_gpu(executable, install, workspace, "installed", artifacts) if expect_no_gpu
                  else smoke_app(executable, install, workspace, "installed", audio, artifacts, speech_model, speech_audio))
        result.update(shortcut_valid=True, uninstall_registry_valid=True, current_user=True)
        if identity is not None:
            result["installed_package_identity"] = identity
        if check_masked_gpu_rejection:
            result["masked_gpu_rejection"] = smoke_no_gpu(
                executable, install, workspace, "installed-gpu-masked", artifacts, force_visibility_mask=True)
    finally:
        if uninstaller.is_file():
            entries = uninstall_entries(app_id)
            assert all(Path(item["InstallLocation"]).resolve() == install.resolve() for item in entries), "Refusing to uninstall an unrelated installation"
            completed = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                        f"/LOG={artifacts / 'uninstaller.log'}"], env=environment,
                                       capture_output=True, timeout=90, creationflags=CREATE_NO_WINDOW)
            assert completed.returncode == 0, f"Owned uninstaller failed: {completed.returncode}"
            assert not uninstall_entries(app_id), "Uninstall registration remains after removal"
            assert not (install / "Castwell.exe").exists(), "Installed executable remains after uninstall"
            assert not start_menu.exists(), "Owned shortcut group remains after uninstall"
            assert sentinel.read_text(encoding="utf-8") == "Uninstall must preserve user library files.\n"
            if result:
                assert Path(result["sentinel"]).is_file(), "Uninstall removed the external --data-dir library"
                if result.get("gui_sentinel"):
                    assert Path(result["gui_sentinel"]).read_text(encoding="utf-8") == "Keep the default GUI library when uninstalling.\n", "Uninstall removed the default GUI library"
                if result.get("masked_gpu_rejection"):
                    assert Path(result["masked_gpu_rejection"]["sentinel"]).is_file(), "Uninstall removed the GPU rejection test's external library"
                result["uninstall_preserved_libraries"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", type=Path, required=True, help="Built portable ZIP")
    parser.add_argument("--installer", type=Path, help="Built Inno Setup installer")
    parser.add_argument("--artifacts", type=Path, default=Path(".local/windows-distribution-check"))
    parser.add_argument("--app-id", default=APP_ID)
    parser.add_argument("--speech-model", type=Path, help="Existing faster-whisper model directory for CUDA ASR proof, no download")
    parser.add_argument("--speech-audio", type=Path, help="Short speech WAV outside public artifacts")
    parser.add_argument("--expect-no-gpu", action="store_true", help="Verify clear CUDA rejection and installation only, without inference")
    parser.add_argument("--keep-workspace", action="store_true", help="Retain the isolated workspace for diagnosis")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This smoke check requires Windows")
    if bool(args.speech_model) != bool(args.speech_audio):
        parser.error("Supply both --speech-model and --speech-audio for optional cached-model ASR")
    if not args.expect_no_gpu and not args.speech_model:
        parser.error("GPU validation requires --speech-model and --speech-audio")
    if args.expect_no_gpu and args.speech_model:
        parser.error("Do not supply speech fixtures with --expect-no-gpu")
    for source in (args.portable, args.installer, args.speech_audio):
        if source and not source.resolve().is_file():
            parser.error(f"Input file does not exist: {source}")
    if args.speech_model:
        args.speech_model = args.speech_model.resolve()
        if not all((args.speech_model / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            parser.error("Speech model must be an existing complete faster-whisper directory")
    args.artifacts = args.artifacts.resolve()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX)).resolve()
    assert workspace.parent == Path(tempfile.gettempdir()).resolve()
    report = {"passed": False, "model_downloads": 0, "classifier_api_calls": 0,
              "speech_requested": bool(args.speech_model), "expect_no_gpu": args.expect_no_gpu, "workspace": str(workspace)}
    started = time.monotonic()
    try:
        audio = workspace / "Known synthetic source.wav"
        generate_audio(audio)
        executable = extract_portable(args.portable.resolve(), workspace / "Portable package with spaces", workspace)
        report["private_library_and_speech_classifier_weights_absent"] = True
        digest = hashlib.sha256()
        with args.portable.open("rb") as archive:
            for chunk in iter(lambda: archive.read(1024 * 1024), b""):
                digest.update(chunk)
        report["portable_sha256"] = digest.hexdigest()
        report["portable"] = (smoke_no_gpu(executable, executable.parent, workspace, "portable", args.artifacts) if args.expect_no_gpu
                              else smoke_app(executable, executable.parent, workspace, "portable", audio,
                                             args.artifacts, args.speech_model, args.speech_audio))
        if not args.expect_no_gpu:
            report["portable_relocation"] = smoke_portable_relocation(
                executable, workspace, args.artifacts, args.speech_model, audio)
        if args.installer:
            report["installed"] = smoke_installer(args.installer.resolve(), workspace, args.artifacts, audio,
                                                   args.app_id, args.speech_model, args.speech_audio, args.expect_no_gpu,
                                                   report["portable"]["version"])
        report["passed"] = True
        note("PASS: expected CUDA rejection and requested installation checks" if args.expect_no_gpu else
             "PASS: frozen CUDA runtime, real ASR and GUI, local feed/batches, known-cut audio and requested installation checks")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        remaining = uninstall_entries(args.app_id) if args.installer else []
        owned_installation_remains = any(workspace in Path(item.get("InstallLocation", "")).resolve().parents for item in remaining)
        if owned_installation_remains:
            report["workspace_retained_due_to_installation"] = True
        (args.artifacts / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not args.keep_workspace and not owned_installation_remains:
            # This exact temporary root was created above and is the only recursive target.
            cleanup_owned_workspace(workspace)
        else:
            note(f"Owned workspace retained: {workspace}")


if __name__ == "__main__":
    main()
