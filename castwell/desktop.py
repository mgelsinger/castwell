"""Windows desktop lifecycle and packaged, local CUDA runtime checks.

The launcher owns only children it starts. A library lock protects SQLite before
the web application opens it, and a per-launch response tag verifies readiness.
Speech and classifier inference require NVIDIA CUDA; neither falls back to CPU.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import webbrowser

from . import __version__


PORTABLE_MARKER = "portable.flag"
SESSION_HEADER = "X-Castwell-Desktop-Session"
DEFAULT_PORT = 8765
DEFAULT_MODEL_PORT = 8089
REVIEWED_QWEN_BYTES = 17106775008
REVIEWED_QWEN_SHA256 = "7e78da5d7e3ae28d178121f58646953305f3e5bd3cb46f4a75584e8b6c6fe169"
_DLL_HANDLES = []
_ENV_KEYS = ("CASTWELL_AI_KEY", "CASTWELL_AI_BASE_URL", "CASTWELL_AI_MODEL",
             "CASTWELL_AI_POLICY", "CASTWELL_TRANSCRIPTION_MODEL", "CASTWELL_MODEL_CACHE",
             "CASTWELL_DATA_DIR")


class DesktopError(RuntimeError):
    pass


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_directory() -> Path:
    return Path(sys.executable).resolve().parent if frozen() else Path(__file__).resolve().parents[1]


def bundle_directory() -> Path:
    return Path(getattr(sys, "_MEIPASS", executable_directory())).resolve()


def default_data_directory() -> Path:
    adjacent = executable_directory()
    if (adjacent / PORTABLE_MARKER).is_file():
        return adjacent / "Data"
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "Castwell"


def bootstrap_preferences_path() -> Path:
    adjacent = executable_directory()
    if (adjacent / PORTABLE_MARKER).is_file():
        return adjacent / "desktop-preferences.json"
    return default_data_directory() / "desktop-preferences.json"


def preferred_data_directory(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    try:
        saved = json.loads(bootstrap_preferences_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("use_default") is True:
            return default_data_directory().resolve()
        value = saved.get("data_dir") if isinstance(saved, dict) else None
        if isinstance(value, str) and value and Path(value).is_absolute():
            return Path(value).resolve()
    except (OSError, ValueError):
        pass
    return default_data_directory().resolve()


def save_data_selection(directory: Path) -> None:
    """Keep the default library relative to the current portable executable."""
    selected = Path(directory).expanduser().resolve()
    use_default = selected == default_data_directory().resolve()
    write_json(bootstrap_preferences_path(), {"schema_version": 2, "use_default": use_default,
               "data_dir": None if use_default else str(selected)})


def native_paths() -> dict[str, Path]:
    root = bundle_directory()
    paths = {"ffmpeg": root / "bin" / "ffmpeg.exe",
             "ffprobe": root / "bin" / "ffprobe.exe",
             "llama": root / "llama" / "llama-server.exe"}
    # Source development can use the previously installed, verified runtimes.
    # Frozen releases always use their own bundled binaries.
    if not frozen():
        for name in ("ffmpeg", "ffprobe"):
            found = shutil.which(name)
            if not paths[name].is_file() and found:
                paths[name] = Path(found).resolve()
        source_llama = root / ".local" / "llama.cpp" / "llama-server.exe"
        if not paths["llama"].is_file() and source_llama.is_file():
            paths["llama"] = source_llama
    return paths


def configure_native_runtime() -> dict[str, Path]:
    paths = native_paths()
    directories = [bundle_directory() / "cuda", bundle_directory() / "bin",
                   paths["llama"].parent]
    if not frozen():
        directories.insert(0, bundle_directory() / ".local" / "windows-vendor-cuda" / "cuda-runtime")
    present = list(dict.fromkeys(str(path) for path in directories if path.is_dir()))
    os.environ["PATH"] = os.pathsep.join(present + [os.environ.get("PATH", "")])
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for directory in present:
            _DLL_HANDLES.append(os.add_dll_directory(directory))
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    return paths


def _hidden_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _binary_check(path: Path) -> dict:
    result = {"path": str(path), "exists": path.is_file(), "ok": False}
    if not result["exists"]:
        result["error"] = "Bundled executable is missing."
        return result
    try:
        completed = subprocess.run([str(path), "--version" if path.name.startswith("llama") else "-version"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=20, creationflags=_hidden_flags(), check=False)
        result["ok"] = completed.returncode == 0
        result["version"] = (completed.stdout or completed.stderr).strip().splitlines()[:2]
        if not result["ok"]:
            result["error"] = "Bundled executable could not load its native dependencies."
    except (OSError, subprocess.TimeoutExpired):
        result["error"] = "Bundled executable failed to start or timed out."
    return result


def cuda_check() -> dict:
    result = {"ready": False, "device_count": 0, "float16_supported": False,
              "allocation_verified": False, "kernel_verified": False, "gpus": [], "errors": [],
              "vad": {"ready": False, "provider": None, "cpu_fallback_disabled": False}}
    try:
        candidate = shutil.which("nvidia-smi")
        if not candidate and os.name == "nt":
            candidate = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "nvidia-smi.exe")
        output = subprocess.run([candidate or "nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                timeout=15, creationflags=_hidden_flags(), check=False)
        if output.returncode:
            raise OSError("NVIDIA driver probe failed")
        for row in list(csv.reader(output.stdout.splitlines()))[:8]:
            if len(row) == 3:
                result["gpus"].append({"name": row[0].strip(), "driver_version": row[1].strip(),
                                       "memory_total_mb": int(row[2].strip())})
        if not result["gpus"]:
            raise OSError("No NVIDIA GPU found")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result["errors"].append("An NVIDIA GPU and working NVIDIA driver are required. Install or update the driver.")
    try:
        import ctranslate2
        import numpy as np
        ctranslate2.set_log_level(logging.ERROR)
        result["device_count"] = ctranslate2.get_cuda_device_count()
        if not result["device_count"]:
            raise RuntimeError("No CUDA device")
        result["float16_supported"] = "float16" in ctranslate2.get_supported_compute_types("cuda", device_index=0)
        if not result["float16_supported"]:
            raise RuntimeError("CUDA float16 is unavailable")
        sample = np.array([0.25, 0.5, 1.0, 2.0], dtype=np.float16)
        gpu = ctranslate2.StorageView.from_array(sample).to_device(ctranslate2.Device.cuda)
        result["allocation_verified"] = str(gpu.device) == "cuda"
        # Device conversion exercises a real CUDA kernel, then checks its values.
        converted = gpu.to(ctranslate2.DataType.float32)
        returned = np.asarray(converted.to_device(ctranslate2.Device.cpu))
        result["kernel_verified"] = bool(str(converted.device) == "cuda"
                                          and returned.dtype == np.float32
                                          and np.array_equal(returned, sample.astype(np.float32)))
    except Exception:
        result["errors"].append("CUDA float16 could not execute. Check the NVIDIA driver and bundled CUDA/cuDNN runtime. CPU fallback is disabled.")
    try:
        import numpy as np
        from .gpu import cuda_vad_model
        vad = cuda_vad_model()
        provider = vad.session.get_providers()[0]
        disabled = vad.session.get_session_options().get_session_config_entry("session.disable_cpu_ep_fallback") == "1"
        probabilities = np.asarray(vad(np.zeros(512 * 2, dtype=np.float32)))
        result["vad"] = {"ready": bool(provider == "CUDAExecutionProvider" and disabled
                                       and probabilities.size and np.isfinite(probabilities).all()),
                         "provider": provider, "cpu_fallback_disabled": disabled}
        if not result["vad"]["ready"]:
            raise RuntimeError("GPU speech activity check failed")
    except Exception:
        result["errors"].append("CUDA speech activity detection could not execute. The bundled GPU ONNX Runtime and CUDA/cuDNN libraries are required. CPU fallback is disabled.")
    result["ready"] = bool(result["gpus"] and result["device_count"] and result["float16_supported"]
                           and result["allocation_verified"] and result["kernel_verified"]
                           and result["vad"]["ready"] and not result["errors"])
    return result


def runtime_check() -> dict:
    paths = configure_native_runtime()
    report = {"schema_version": 1, "version": __version__, "frozen": frozen(),
              "bundle_root": str(bundle_directory()), "data_dir": str(preferred_data_directory()),
              "portable": (executable_directory() / PORTABLE_MARKER).is_file(),
              "transcription": {"device": "cuda", "compute_type": "float16", "implicit_downloads": False},
              "binaries": {name: _binary_check(path) for name, path in paths.items()},
              "imports": {}, "errors": []}
    for name in ("faster_whisper", "ctranslate2", "onnxruntime", "tkinter"):
        try:
            module = importlib.import_module(name)
            report["imports"][name] = {"ok": True, "version": str(getattr(module, "__version__", "bundled"))}
        except Exception:
            report["imports"][name] = {"ok": False}
            report["errors"].append(f"Bundled {name} could not load.")
    required = ("ggml-cuda.dll", "cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")
    search = [paths["llama"].parent, bundle_directory() / "cuda"]
    dlls = []
    for name in required:
        found = next((directory / name for directory in search if (directory / name).is_file()), search[0] / name)
        dlls.append({"name": name, "path": str(found), "exists": found.is_file()})
    report["llama_cuda_dlls"] = {"ready": all(item["exists"] for item in dlls), "files": dlls}
    report["cuda"] = cuda_check()
    for name, item in report["binaries"].items():
        if not item["ok"]:
            report["errors"].append(f"{name}: {item.get('error', 'Native runtime check failed.')}")
    if not report["llama_cuda_dlls"]["ready"]:
        report["errors"].append("The NVIDIA CUDA llama.cpp runtime is incomplete.")
    report["errors"].extend(report["cuda"]["errors"])
    report["ready"] = bool(not report["errors"] and all(item["ok"] for item in report["imports"].values()))
    return report


def write_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        # Windows readers may open JSON without FILE_SHARE_DELETE. Keep each
        # published file complete, while tolerating a brief overlapping read.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.16))
    finally:
        temporary.unlink(missing_ok=True)


class LibraryLock:
    """OS-held one-byte lock; stale metadata cannot keep a library locked."""

    def __init__(self, directory: Path, port: int, session: str):
        self.runtime = Path(directory).resolve() / "runtime"
        self.port, self.session, self.handle = port, session, None

    def __enter__(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.handle = (self.runtime / "backend.lock").open("a+b")
        try:
            if (self.runtime / "backend.lock").stat().st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise DesktopError("Another Castwell backend is using this data folder. Stop that instance first.") from None
        write_json(self.runtime / "backend-owner.json", {"pid": os.getpid(), "port": self.port,
                   "session_token": self.session, "started": datetime.now(timezone.utc).isoformat()})
        return self

    def __exit__(self, *ignored):
        if self.handle:
            try:
                owner = self.runtime / "backend-owner.json"
                if json.loads(owner.read_text(encoding="utf-8")).get("session_token") == self.session:
                    owner.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


def restore_standard_streams() -> None:
    """Windowed frozen apps can still be given explicit subprocess pipe handles."""
    for name, number, mode in (("stdin", -10, "r"), ("stdout", -11, "w"), ("stderr", -12, "w")):
        if getattr(sys, name) is not None:
            continue
        stream = None
        if os.name == "nt":
            try:
                import msvcrt
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetStdHandle.restype = ctypes.c_void_p
                kernel.GetCurrentProcess.restype = ctypes.c_void_p
                handle = kernel.GetStdHandle(number)
                duplicate = ctypes.c_void_p()
                kernel.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                                  ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
                                                  ctypes.c_int, ctypes.c_ulong]
                current = kernel.GetCurrentProcess()
                if handle and handle != ctypes.c_void_p(-1).value and kernel.DuplicateHandle(current, handle, current, ctypes.byref(duplicate), 0, False, 2):
                    descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY if mode == "r" else os.O_WRONLY)
                    stream = os.fdopen(descriptor, mode, encoding="utf-8", errors="replace", buffering=1)
            except (OSError, ValueError):
                pass
        setattr(sys, name, stream or open(os.devnull, mode, encoding="utf-8"))


def _local_address(address: str) -> bool:
    try:
        parsed = urlsplit(address)
        return bool(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                    and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def install_local_processing_guards(classifier_address: str | None = None, *, reviewed_qwen=False) -> None:
    """Desktop jobs cannot initiate weight downloads or call a remote provider."""
    from . import config, processing
    if getattr(processing.transcribe, "_desktop_guard", False):
        return
    original_transcribe, original_detect = processing.transcribe, processing.detect_ads
    original_test = config.test_classifier
    original_get, original_update = config.Settings.get, config.Settings.update

    def owned_classifier(address):
        return bool(classifier_address and _local_address(address)
                    and address.rstrip("/") == classifier_address.rstrip("/"))

    def desktop_settings(self):
        values = original_get(self)
        values.update(review_only=True, ai_policy="verified", auto_approve_threshold=0.90)
        if classifier_address:
            values.update(ai_reasoning=bool(reviewed_qwen), detector="ai")
        return values

    def desktop_update(self, values):
        # Environment profile fields are session choices, not a replacement for
        # an existing API configuration saved in this user's library.
        selected = dict(values)
        if classifier_address:
            for key in ("ai_base_url", "ai_model", "ai_policy", "ai_reasoning", "detector"):
                selected.pop(key, None)
        return original_update(self, selected)

    def local_transcribe(audio, model="small.en", *args, **kwargs):
        model_cache = kwargs.get("model_cache") or os.environ.get("CASTWELL_MODEL_CACHE", "")
        directory = config._model_directory({"transcription_model": model, "model_cache": model_cache})
        if directory is None:
            raise processing.ProcessingError("The selected speech model is not prepared locally. Use Settings to prepare it explicitly before processing. No model was downloaded.")
        return original_transcribe(audio, str(directory), *args, **kwargs)

    def local_detect(transcript, detector="auto", *, config=None, **kwargs):
        selected = config or {}
        address = selected.get("ai_base_url", os.environ.get("CASTWELL_AI_BASE_URL", ""))
        if detector != "heuristic" and address and not owned_classifier(address):
            raise processing.ProcessingError("This desktop release uses its own local CUDA models only. Select a GGUF in the launcher. The saved provider configuration is preserved but is not used by this desktop profile.")
        return original_detect(transcript, detector, config=config, **kwargs)

    def local_test(settings):
        selected = config._configuration(settings)
        if selected.get("ai_base_url") and not owned_classifier(selected["ai_base_url"]):
            raise ValueError("The desktop release only permits its own CUDA classifier. Select a GGUF in the launcher.")
        return original_test(settings)

    local_transcribe._desktop_guard = True
    processing.transcribe, processing.detect_ads, config.test_classifier = local_transcribe, local_detect, local_test
    config.Settings.get, config.Settings.update = desktop_settings, desktop_update


def _server_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "backend.log", maxBytes=4 * 1024**2, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def serve(args) -> int:
    restore_standard_streams()
    directory = preferred_data_directory(args.data_dir)
    _server_logging(directory / "runtime")
    session = args.session_token or secrets.token_hex(24)
    try:
        with LibraryLock(directory, args.port, session):
            paths = configure_native_runtime()
            gpu = cuda_check()
            if not gpu["ready"]:
                raise DesktopError(" ".join(gpu["errors"]))
            if frozen() and any(not path.is_file() for path in paths.values()):
                raise DesktopError("The bundled native runtime is incomplete. Reinstall Castwell.")
            for name in _ENV_KEYS:
                os.environ.pop(name, None)
            os.environ["CASTWELL_DATA_DIR"] = str(directory)
            os.environ["CASTWELL_MODEL_CACHE"] = str(directory / "models")
            speech = None
            if args.speech_model:
                from .config import _complete_model
                speech = Path(args.speech_model).expanduser().resolve()
                if not _complete_model(speech):
                    raise DesktopError("--speech-model must name a prepared local speech model directory.")
            from .library import Library
            from .config import Settings
            library = Library(directory)
            selected = {"review_only": True, "ai_policy": "verified", "auto_approve_threshold": 0.90}
            if "transcription_model" not in library.get_settings():
                selected["transcription_model"] = "small.en"
            Settings(library).update(selected)
            if speech:
                os.environ["CASTWELL_TRANSCRIPTION_MODEL"] = str(speech)
            classifier_address = f"http://127.0.0.1:{args.classifier_port}/v1" if args.classifier_port else None
            if classifier_address:
                os.environ["CASTWELL_AI_BASE_URL"] = classifier_address
                os.environ["CASTWELL_AI_MODEL"] = "castwell-local"
                os.environ["CASTWELL_AI_POLICY"] = "verified"
            install_local_processing_guards(classifier_address, reviewed_qwen=args.reviewed_qwen)
            from .app import create_app
            app = create_app(directory)

            @app.middleware("http")
            async def owned_session(request, call_next):
                response = await call_next(request)
                response.headers[SESSION_HEADER] = session
                return response

            import uvicorn
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_config=None,
                                                   access_log=False, timeout_graceful_shutdown=30))
            if args.parent_control:
                def control():
                    for line in sys.stdin:
                        if line.strip().upper() == "STOP":
                            server.should_exit = True
                            return
                    server.should_exit = True
                threading.Thread(target=control, name="castwell-parent-control", daemon=True).start()
            server.run()
            return 0 if server.started else 1
    except Exception as exc:
        logging.exception("Desktop backend could not start")
        # Avoid displaying arbitrary exception data (such as credentials).
        message = str(exc) if isinstance(exc, DesktopError) else "The backend could not start. See runtime/backend.log."
        if args.report:
            write_json(Path(args.report), {"status": "error", "error": message, "data_dir": str(directory)})
        return 1


def choose_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", preferred if 0 < preferred < 65536 else 0))
        return listener.getsockname()[1]


def available_port(preferred: int) -> int:
    try:
        return choose_port(preferred)
    except OSError:
        return choose_port(0)


def _request_json(address: str, session: str | None = None, *, payload=None) -> dict:
    request = Request(address, data=json.dumps(payload).encode("utf-8") if payload is not None else None,
                      headers={"Content-Type": "application/json"} if payload is not None else {})
    with urlopen(request, timeout=3) as response:
        if session is not None and response.headers.get(SESSION_HEADER) != session:
            raise DesktopError("The listener does not belong to this launcher.")
        data = response.read(2 * 1024**2 + 1)
        if len(data) > 2 * 1024**2:
            raise DesktopError("Unexpectedly large readiness response.")
        return json.loads(data)


def valid_gguf(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    try:
        with path.open("rb") as source:
            if source.read(4) == b"GGUF":
                return path
    except OSError:
        pass
    raise DesktopError("Choose an existing GGUF model file. The launcher never downloads classifier weights.")


@lru_cache(maxsize=8)
def _reviewed_qwen_hash(path: str, size: int, modified: int) -> bool:
    if size != REVIEWED_QWEN_BYTES:
        return False
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024**2), b""):
                digest.update(chunk)
        current = Path(path).stat()
        return bool(current.st_size == size and current.st_mtime_ns == modified
                    and digest.hexdigest() == REVIEWED_QWEN_SHA256)
    except OSError:
        return False


def is_reviewed_qwen(path: Path) -> bool:
    """Use the evaluated profile only for the exact pinned GGUF, never its name."""
    path = Path(path).resolve()
    try:
        metadata = path.stat()
        return _reviewed_qwen_hash(str(path), metadata.st_size, metadata.st_mtime_ns)
    except OSError:
        return False


class DesktopController:
    """Testable lifecycle independent of Tk's event loop."""

    def __init__(self, directory: Path, port=DEFAULT_PORT, *, speech_model=None, gguf=None,
                 report=None, no_browser=False, persist_data_selection=False):
        self.directory = Path(directory).expanduser().resolve()
        self.runtime = self.directory / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.port, self.speech_model, self.gguf = port, speech_model, gguf
        self.report, self.no_browser = Path(report) if report else None, no_browser
        self.persist_data_selection = persist_data_selection
        self.session = secrets.token_hex(24)
        self.app_process = self.model_process = None
        self.runtime_process = None
        self.model_port = None
        self.model_log_offset = 0
        self.reviewed_qwen = False
        self._streams = []
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self.status, self.error = "stopped", None
        self._publish()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def _publish(self):
        state = {"schema_version": 1, "version": __version__, "status": self.status, "error": self.error,
                 "data_dir": str(self.directory), "port": self.port, "base_url": self.base_url,
                 "session_token": self.session, "app_pid": self.app_process.pid if self.app_process else None,
                 "model_pid": self.model_process.pid if self.model_process else None,
                 "runtime_pid": self.runtime_process.pid if self.runtime_process else None,
                 "classifier_profile": self.classifier_profile,
                 "transcription": {"device": "cuda", "compute_type": "float16", "implicit_downloads": False}}
        paths = [self.runtime / "launcher-state.json"]
        if self.report and self.report != paths[0]:
            paths.append(self.report)
        for path in paths:
            try:
                write_json(path, state)
            except OSError:
                # Readiness reports are observational. A locked report file
                # must never prevent Start, Stop or parent control from working.
                logging.warning("Could not publish launcher state; the lifecycle remains active.")
        return state

    @property
    def classifier_profile(self):
        return "reviewed-qwen3.8-cuda" if self.reviewed_qwen else "generic-local-cuda" if self.gguf else "saved-local-only"

    def snapshot(self):
        with self._lock:
            if self.status == "ready" and self.app_process and self.app_process.poll() is not None:
                self.status, self.error = "error", "The backend exited. See runtime/backend.log."
                self._publish()
            return self._publish()

    def _environment(self):
        environment = dict(os.environ)
        for name in _ENV_KEYS:
            environment.pop(name, None)
        environment["ORT_DISABLE_TELEMETRY"] = "1"
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        return environment

    def _launch(self, command, log_name, *, parent_control=False):
        stream = (self.runtime / log_name).open("ab", buffering=0)
        self._streams.append(stream)
        return subprocess.Popen(command, stdin=subprocess.PIPE if parent_control else subprocess.DEVNULL,
                                stdout=stream, stderr=stream, env=self._environment(),
                                creationflags=_hidden_flags())

    def _app_command(self):
        command = [sys.executable] if frozen() else [sys.executable, "-m", "castwell.desktop"]
        command += ["--serve", "--parent-control", "--data-dir", str(self.directory), "--port", str(self.port),
                    "--session-token", self.session, "--report", str(self.runtime / "backend-startup.json")]
        if self.speech_model:
            command += ["--speech-model", str(self.speech_model)]
        if self.model_port:
            command += ["--classifier-port", str(self.model_port)]
        if self.reviewed_qwen:
            command += ["--reviewed-qwen"]
        return command

    def _probe_runtime(self):
        # Native GPU initialization inside a frozen Tk worker can stall. Probe
        # in a short owned process, then let its CUDA contexts leave with it.
        # The actual backend independently validates CUDA before accepting work.
        configure_native_runtime()
        target = self.runtime / "runtime-check.json"
        target.unlink(missing_ok=True)
        command = [sys.executable] if frozen() else [sys.executable, "-m", "castwell.desktop"]
        command += ["--check-runtime", "--report", str(target), "--data-dir", str(self.directory)]
        process = self.runtime_process = self._launch(command, "runtime-probe.log")
        self._publish()
        try:
            try:
                code = process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                raise DesktopError("The CUDA runtime check timed out. See runtime/runtime-probe.log. No model was loaded and CPU fallback is disabled.") from None
            try:
                if target.stat().st_size > 1024**2:
                    raise ValueError("Unexpected report size")
                report = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise DesktopError("The runtime check did not produce a fresh valid report. See runtime/runtime-probe.log.") from None
            if (code != 0 or not isinstance(report, dict) or report.get("ready") is not True
                    or report.get("frozen") is not frozen()):
                errors = report.get("errors", []) if isinstance(report, dict) else []
                detail = " ".join(str(error) for error in errors[:8]) if isinstance(errors, list) else ""
                raise DesktopError(detail or "The bundled CUDA runtime check failed. See runtime/runtime-probe.log. CPU fallback is disabled.")
            return report
        finally:
            self._terminate_owned(process, timeout=3)
            self.runtime_process = None

    def _wait_app(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.app_process.poll() is not None:
                try:
                    detail = json.loads((self.runtime / "backend-startup.json").read_text(encoding="utf-8"))
                    raise DesktopError(detail.get("error") or "The backend exited during startup. See runtime/backend.log.")
                except (OSError, ValueError):
                    raise DesktopError("The backend exited during startup. See runtime/backend.log.") from None
            try:
                health = _request_json(self.base_url + "/api/health", self.session)
                if health.get("status") == "ok" and self.app_process.poll() is None:
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise DesktopError("The backend did not become ready. See runtime/backend.log.")

    def _wait_model(self, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.model_process.poll() is not None:
                raise DesktopError("The CUDA classifier could not load. Check GPU memory and runtime/classifier.log. CPU fallback is disabled.")
            ready = False
            try:
                models = _request_json(f"http://127.0.0.1:{self.model_port}/v1/models")
                ready = any(item.get("id") == "castwell-local" for item in models.get("data", []))
            except Exception:
                pass
            if ready:
                self._verify_model_gpu_offload()
                return
            time.sleep(0.5)
        raise DesktopError("The classifier did not become ready. See runtime/classifier.log.")

    def _verify_model_gpu_offload(self):
        try:
            path = self.runtime / "classifier.log"
            with path.open("rb") as source:
                source.seek(max(self.model_log_offset, path.stat().st_size - 2 * 1024**2))
                log = source.read().decode("utf-8", errors="replace")
            matches = re.findall(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", log, re.IGNORECASE)
            if not matches or int(matches[-1][0]) != int(matches[-1][1]) or int(matches[-1][0]) < 1:
                raise DesktopError("The selected classifier did not offload all layers to NVIDIA CUDA. Choose a smaller GGUF or free GPU memory. CPU fallback is disabled.")
        except OSError:
            raise DesktopError("Could not verify classifier CUDA offload. See runtime/classifier.log.") from None

    def start(self):
        with self._lock:
            if self.status in {"starting", "ready", "stopping"}:
                return
            self.status, self.error = "starting", None
            self.session = secrets.token_hex(24)
            self._publish()
        try:
            # Check before constructing either model or library. No downloads.
            check = self._probe_runtime()
            if not check["ready"]:
                raise DesktopError(" ".join(check["errors"]) or "The bundled CUDA runtime is unavailable.")
            # The launcher itself does not hold this lock while its child runs.
            # Probe it before loading a selected GGUF to avoid waste on a busy DB.
            with LibraryLock(self.directory, self.port, self.session):
                pass
            self.port = available_port(self.port)
            if self.gguf:
                selected = valid_gguf(self.gguf)
                self.reviewed_qwen = is_reviewed_qwen(selected)
                self.model_port = available_port(DEFAULT_MODEL_PORT)
                if self.model_port == self.port:
                    self.model_port = available_port(0)
                model = native_paths()["llama"]
                command = [str(model), "--model", str(selected), "--alias", "castwell-local", "--offline",
                           "--host", "127.0.0.1", "--port", str(self.model_port), "--ctx-size", "8192",
                           "--n-gpu-layers", "all", "--fit", "off", "--parallel", "1", "--verbosity", "4"]
                if self.reviewed_qwen:
                    # Match the tested request profile, keeping server defaults
                    # bounded. Individual review requests enable thinking with
                    # their own 1024 token budget through the verified policy.
                    command += ["--batch-size", "512", "--ubatch-size", "128", "--flash-attn", "on",
                                "--jinja", "--reasoning", "off", "--reasoning-format", "deepseek",
                                "--reasoning-budget", "1024", "--n-predict", "4096",
                                "--temp", "1.0", "--top-p", "0.95", "--top-k", "20", "--min-p", "0",
                                "--presence-penalty", "1.5", "--repeat-penalty", "1.0", "--seed", "42"]
                log_path = self.runtime / "classifier.log"
                self.model_log_offset = log_path.stat().st_size if log_path.exists() else 0
                self.model_process = self._launch(command, "classifier.log")
                self._publish()
                self._wait_model()
            (self.runtime / "backend-startup.json").unlink(missing_ok=True)
            self.app_process = self._launch(self._app_command(), "backend-console.log", parent_control=True)
            self._publish()
            self._wait_app()
            self.status = "ready"
            write_json(self.runtime / "launcher.json", {"schema_version": 1, "data_dir": str(self.directory),
                       "port": self.port, "gguf": str(self.gguf) if self.gguf else None,
                       "classifier_profile": self.classifier_profile})
            if self.persist_data_selection:
                save_data_selection(self.directory)
            self._publish()
            if not self.no_browser:
                webbrowser.open(self.base_url)
        except Exception as exc:
            self._stop_owned_startup_children()
            self.status = "error"
            self.error = str(exc) if isinstance(exc, DesktopError) else "Startup failed. See runtime logs."
            self._publish()

    def activity(self):
        if not self.app_process or self.app_process.poll() is not None:
            return {"jobs": [], "refresh_running": False, "model_running": False}
        health = _request_json(self.base_url + "/api/health", self.session)
        jobs = _request_json(self.base_url + "/api/jobs", self.session).get("jobs", [])
        preparation = _request_json(self.base_url + "/api/models/status", self.session)
        # Jobs.snapshot lists owned pending work. Its stages include downloading,
        # transcribing, detecting and rendering, not a generic running status.
        active = jobs
        return {"jobs": active, "refresh_running": bool(health.get("refresh_running")),
                "model_running": preparation.get("status") == "running"}

    @staticmethod
    def _terminate_owned(process, timeout=10):
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _close_streams(self):
        for stream in self._streams:
            stream.close()
        self._streams.clear()

    def _stop_owned_startup_children(self):
        # Startup has not admitted work yet. These are only our Popen children.
        self._terminate_owned(self.runtime_process, timeout=3)
        if self.app_process and self.app_process.poll() is None:
            try:
                self.app_process.stdin.write(b"STOP\n")
                self.app_process.stdin.flush()
                self.app_process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self._terminate_owned(self.app_process)
        self._terminate_owned(self.model_process)
        self._close_streams()

    def stop(self, *, cancel_jobs=False) -> bool:
        # Serialize stop attempts without holding the state lock during network
        # checks or child waits. Tk can keep rendering progress and log controls.
        with self._stop_lock:
            if self.status == "starting":
                raise DesktopError("Wait for startup to finish before stopping.")
            previous = self.status
            try:
                activity = self.activity()
                if activity["refresh_running"] or activity["model_running"]:
                    raise DesktopError("A feed refresh or speech model preparation is running. Wait for it to finish before stopping.")
                if activity["jobs"]:
                    if not cancel_jobs:
                        raise DesktopError("Podcast jobs are active. Finish them or explicitly cancel them before stopping.")
                    identifiers = list(dict.fromkeys(item["episode_id"] for item in activity["jobs"]))
                    for index in range(0, len(identifiers), 500):
                        _request_json(self.base_url + "/api/episodes/batch/cancel", self.session,
                                      payload={"ids": identifiers[index:index + 500]})
                    deadline = time.monotonic() + 20
                    while self.activity()["jobs"]:
                        if time.monotonic() >= deadline:
                            raise DesktopError("Cancellation is still finishing. The app remains running; try Stop again shortly.")
                        time.sleep(0.25)
                self.status, self.error = "stopping", None
                self._publish()
                if self.app_process and self.app_process.poll() is None:
                    self.app_process.stdin.write(b"STOP\n")
                    self.app_process.stdin.flush()
                    try:
                        self.app_process.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        raise DesktopError("Graceful shutdown is still waiting. The process was not killed; check runtime/backend.log.") from None
                self._terminate_owned(self.model_process)
                self._close_streams()
                self.status = "stopped"
                self._publish()
                return True
            except Exception as exc:
                self.status = previous
                self.error = str(exc) if isinstance(exc, DesktopError) else "Could not confirm that work is idle. The app remains running."
                self._publish()
                raise DesktopError(self.error) from None


def _read_launcher_preferences(directory: Path) -> dict:
    try:
        saved = json.loads((directory / "runtime" / "launcher.json").read_text(encoding="utf-8"))
        return {"gguf": saved.get("gguf")} if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def _run_gui_tick(window, update, next_tick, on_error):
    try:
        update()
    except Exception:
        logging.warning("Could not refresh launcher UI; the next refresh remains scheduled.")
        on_error()
    finally:
        try:
            if window.winfo_exists():
                window.after(350, next_tick)
        except Exception:
            # A destroyed Tk window has no next tick to schedule.
            pass


def gui(args) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    restore_standard_streams()
    window = tk.Tk()
    window.title("Castwell")
    window.geometry("690x330")
    window.minsize(610, 300)
    directory = preferred_data_directory(args.data_dir)
    saved = _read_launcher_preferences(directory)
    data = tk.StringVar(value=str(directory))
    gguf = tk.StringVar(value=args.gguf or saved.get("gguf") or "")
    status = tk.StringVar(value="Stopped. NVIDIA CUDA inference is required.")
    controller = DesktopController(directory, args.port, speech_model=args.speech_model,
                                   gguf=gguf.get() or None, report=args.report, no_browser=args.no_browser,
                                   persist_data_selection=True)
    icon = bundle_directory() / "castwell.ico"
    if icon.is_file():
        try:
            window.iconbitmap(str(icon))
        except tk.TclError:
            pass
    frame = ttk.Frame(window, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Castwell", font=("Segoe UI", 21, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Your local podcast library and reviewed ad removal").pack(anchor="w", pady=(0, 15))
    settings = ttk.Frame(frame)
    settings.pack(fill="x")
    ttk.Label(settings, text="Data folder").grid(row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Entry(settings, textvariable=data).grid(row=0, column=1, sticky="ew", pady=4)
    def browse_data():
        if controller.status in {"ready", "starting", "stopping"}:
            messagebox.showinfo("Data folder", "Stop Castwell before choosing another data folder.")
            return
        chosen = filedialog.askdirectory(initialdir=data.get(), title="Choose Castwell data folder")
        if chosen:
            data.set(chosen)
    ttk.Button(settings, text="Browse", command=browse_data).grid(row=0, column=2, padx=(8, 0))
    ttk.Label(settings, text="Optional GGUF").grid(row=1, column=0, sticky="w")
    ttk.Entry(settings, textvariable=gguf).grid(row=1, column=1, sticky="ew", pady=4)
    def browse_model():
        chosen = filedialog.askopenfilename(title="Choose local classifier GGUF", filetypes=[("GGUF models", "*.gguf")])
        if chosen:
            gguf.set(chosen)
    ttk.Button(settings, text="Browse", command=browse_model).grid(row=1, column=2, padx=(8, 0))
    settings.columnconfigure(1, weight=1)
    ttk.Label(frame, text="Weights are optional and never downloaded by the launcher. Prepare speech in Settings.", wraplength=640).pack(anchor="w", pady=(10, 8))
    ttk.Label(frame, textvariable=status, wraplength=640).pack(anchor="w", pady=(0, 10))
    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    pending = {"close": False}

    def start():
        nonlocal controller
        if controller.status in {"ready", "starting", "stopping"}:
            return
        controller = DesktopController(Path(data.get()), args.port, speech_model=args.speech_model,
                                       gguf=gguf.get() or None, report=args.report, no_browser=args.no_browser,
                                       persist_data_selection=True)
        threading.Thread(target=controller.start, name="castwell-launch", daemon=True).start()

    def stop(close=False, from_control=False):
        if controller.status == "starting":
            if not from_control:
                messagebox.showinfo("Startup", "Wait for startup to finish before stopping.")
            return
        try:
            activity = controller.activity()
            cancel = False
            if activity["refresh_running"] or activity["model_running"]:
                raise DesktopError("Wait for feed refresh or speech model preparation to finish before closing.")
            if activity["jobs"]:
                if from_control:
                    raise DesktopError("Jobs are active. STOP did not close the app.")
                cancel = messagebox.askyesno("Active jobs", "Podcast jobs are active. Cancel these jobs and stop Castwell?")
                if not cancel:
                    return
            pending["close"] = close
            def finish():
                try:
                    controller.stop(cancel_jobs=cancel)
                except DesktopError:
                    pending["close"] = False
            threading.Thread(target=finish, name="castwell-stop", daemon=True).start()
        except DesktopError as exc:
            controller.error = str(exc)
            controller._publish()
            if not from_control:
                messagebox.showinfo("Castwell is still running", str(exc))

    ttk.Button(actions, text="Start", command=start).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="Open library", command=lambda: webbrowser.open(controller.base_url) if controller.status == "ready" else None).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="Stop", command=stop).pack(side="left", padx=(0, 8))
    def open_logs():
        if os.name == "nt":
            os.startfile(str(controller.runtime))
    ttk.Button(actions, text="View logs", command=open_logs).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", lambda: stop(close=True))
    control_stop = threading.Event()
    if args.parent_control:
        def parent_control():
            for line in sys.stdin:
                if line.strip().upper() == "STOP":
                    control_stop.set()
                    return
            control_stop.set()
        threading.Thread(target=parent_control, name="castwell-gui-control", daemon=True).start()

    def update_tick():
        state = controller.snapshot()
        descriptions = {"starting": "Starting local CUDA services...", "ready": f"Ready: {controller.base_url} | {controller.classifier_profile}",
                        "stopping": "Stopping owned services...", "stopped": "Stopped. Data is saved.", "error": "Startup or runtime error."}
        status.set(state["error"] or descriptions[state["status"]])
        if control_stop.is_set() and controller.status != "starting":
            control_stop.clear()
            stop(close=True, from_control=True)
        if pending["close"] and controller.status in {"stopped", "error"}:
            window.destroy()
            return
    def tick():
        _run_gui_tick(window, update_tick, tick, lambda: status.set("Could not refresh launcher state. Retrying..."))
    tick()
    if args.auto_start:
        window.after(150, start)
    window.mainloop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Castwell desktop launcher (NVIDIA CUDA required)")
    parser.add_argument("--serve", action="store_true", help="Run the owned loopback backend without a GUI")
    parser.add_argument("--check-runtime", action="store_true", help="Verify binaries, imports and CUDA without model weights")
    parser.add_argument("--report", help="Write runtime diagnostics or launcher state as JSON")
    parser.add_argument("--data-dir", help="Override the data folder")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--speech-model", help="Existing prepared local speech model directory")
    parser.add_argument("--gguf", help="Existing local classifier GGUF file")
    parser.add_argument("--parent-control", action="store_true", help="Read STOP from stdin; EOF requests shutdown")
    parser.add_argument("--session-token", help=argparse.SUPPRESS)
    parser.add_argument("--classifier-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--reviewed-qwen", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open a browser")
    parser.add_argument("--auto-start", action="store_true", help="Start services when the launcher window opens")
    args = parser.parse_args(argv)
    if not 0 < args.port < 65536 or args.classifier_port is not None and not 0 < args.classifier_port < 65536:
        parser.error("Ports must be between 1 and 65535")
    if args.session_token and (len(args.session_token) > 128 or not args.session_token.isalnum()):
        parser.error("Invalid session token")
    restore_standard_streams()
    if args.check_runtime:
        report = runtime_check()
        report["data_dir"] = str(preferred_data_directory(args.data_dir))
        if args.report:
            write_json(Path(args.report), report)
        print(json.dumps(report, indent=2))
        return 0 if report["ready"] else 1
    if args.serve:
        return serve(args)
    return gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
