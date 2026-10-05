"""Validated, non-secret preferences and explicit local readiness checks.

Reading settings or diagnostics never contacts a model provider. API keys stay
in the process environment and are never copied into the library or responses.
"""

from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path
import re
import shutil
from typing import Callable, Optional
from urllib.parse import urlsplit

from .processing import ProcessingError


_DEFAULTS = {
    "transcription_model": "base",
    "language": None,
    "detector": "auto",
    "auto_approve_threshold": 0.90,
    "review_only": False,
    "ai_base_url": "",
    "ai_model": "",
}
_ENVIRONMENT = {
    "transcription_model": "CASTWELL_TRANSCRIPTION_MODEL",
    "ai_base_url": "CASTWELL_AI_BASE_URL",
    "ai_model": "CASTWELL_AI_MODEL",
}
_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


def _text(value, label: str, *, allow_empty=False, limit=1024) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} must be valid text of at most {limit} characters")
    return value


def _validated(values: dict) -> dict:
    if not isinstance(values, dict) or not values.keys() <= _DEFAULTS.keys():
        # Never repeat unsupported keys: a caller may have accidentally used a
        # credential itself as a key.
        raise ValueError("Unsupported settings; credentials must be configured in the environment")
    result = dict(values)
    if "transcription_model" in result:
        result["transcription_model"] = _text(result["transcription_model"], "Transcription model")
    if "language" in result:
        language = result["language"]
        if isinstance(language, str):
            language = language.strip().lower()
        if language in (None, "", "auto"):
            result["language"] = None
        elif isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language):
            result["language"] = language
        else:
            raise ValueError("Language must be a two or three letter language code, or auto")
    if "detector" in result and result["detector"] not in ("auto", "ai", "heuristic"):
        raise ValueError("Detector must be auto, ai, or heuristic")
    if "auto_approve_threshold" in result:
        threshold = result["auto_approve_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0.5 <= threshold <= 1:
            raise ValueError("Automatic approval threshold must be a finite number between 0.5 and 1")
        result["auto_approve_threshold"] = float(threshold)
    if "review_only" in result and not isinstance(result["review_only"], bool):
        raise ValueError("Review only must be a boolean")
    if "ai_model" in result:
        result["ai_model"] = _text(result["ai_model"], "Classifier model", allow_empty=True, limit=256)
    if "ai_base_url" in result:
        address = _text(result["ai_base_url"], "Classifier URL", allow_empty=True, limit=2048)
        if address:
            try:
                parsed = urlsplit(address)
                # Reading .port also validates malformed/non-numeric ports.
                parsed.port
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment or "?" in address or "#" in address
                        or any(char.isspace() for char in address) or "\\" in address):
                    raise ValueError
            except ValueError:
                raise ValueError("Classifier URL must be HTTP(S), without credentials, query parameters, or a fragment") from None
        result["ai_base_url"] = address.rstrip("/")
    return result


class Settings:
    """Library-backed preferences; nonempty environment values take precedence."""

    def __init__(self, library):
        self.library = library

    def get(self) -> dict:
        saved = self.library.get_settings()
        values = {**_DEFAULTS, **{key: value for key, value in saved.items() if key in _DEFAULTS}}
        overrides = []
        for field, variable in _ENVIRONMENT.items():
            value = os.getenv(variable, "").strip()
            if value:
                values[field] = value
                overrides.append(field)
        values = _validated(values)
        cache = os.getenv("CASTWELL_MODEL_CACHE", "").strip()
        if cache:
            overrides.append("model_cache")
        values.update(
            model_cache=str(Path(cache).expanduser().resolve() if cache else self.library.root / "models"),
            key_configured=bool(os.getenv("CASTWELL_AI_KEY", "").strip()),
            ai_configured=bool(values["ai_base_url"] and values["ai_model"]),
            env_overrides=overrides,
        )
        return values

    def update(self, values: dict) -> dict:
        values = _validated(values)
        for field, variable in _ENVIRONMENT.items():
            override = os.getenv(variable, "").strip()
            if override and field in values and values[field] != _validated({field: override})[field]:
                raise ValueError(f"{field} is controlled by {variable}; change that environment variable first")
        self.library.update_settings(values)
        return self.get()

    def diagnostics(self) -> dict:
        return diagnostics(self)

    def test_classifier(self) -> dict:
        return test_classifier(self)


def _configuration(settings) -> dict:
    return settings.get() if isinstance(settings, Settings) else dict(settings)


def _complete_model(directory: Path) -> bool:
    try:
        return all((directory / name).is_file() and (directory / name).stat().st_size > 0 for name in _MODEL_FILES)
    except OSError:
        return False


def _model_directory(config: dict) -> Optional[Path]:
    """Find this model's usable local files without resolving anything online."""
    name = config["transcription_model"]
    local = Path(name).expanduser()
    if local.is_dir():
        return local.resolve() if _complete_model(local) else None
    try:
        from faster_whisper.utils import _MODELS
    except (ImportError, OSError):
        _MODELS = {}
    repository = _MODELS.get(name, name if "/" in name and not local.is_absolute() else None)
    if not repository:
        return None
    # Hugging Face resolves an unpinned model through refs/main. An incomplete
    # download or a snapshot for another model must not look ready.
    root = Path(config["model_cache"]) / ("models--" + repository.replace("/", "--"))
    try:
        revision = (root / "refs" / "main").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", revision):
            return None
        directory = root / "snapshots" / revision
        return directory if _complete_model(directory) else None
    except OSError:
        return None


def diagnostics(settings) -> dict:
    """Report local prerequisites. No requests, downloads, or file writes."""
    checks = []

    def check(identifier, label, status, detail):
        checks.append({"id": identifier, "label": label, "status": status, "detail": detail})

    for binary in ("ffmpeg", "ffprobe"):
        available = bool(shutil.which(binary))
        check(binary, binary.upper(), "ok" if available else "error",
              "Installed and available." if available else "Install FFmpeg and make its binaries available on PATH.")
    try:
        installed = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        installed = False
    check("transcription", "Local transcription engine", "ok" if installed else "error",
          "faster-whisper is installed." if installed else "Install the transcription extra: pip install 'castwell[transcription]'.")
    try:
        config = _configuration(settings)
    except (TypeError, ValueError):
        check("settings", "Processing settings", "error", "A saved setting or environment override is invalid. Check model names and the classifier URL format.")
        return {"checks": checks, "ready": False}

    model_available = bool(_model_directory(config))
    check("model", "Transcription model", "ok" if model_available else "warning",
          "The selected model's required files are available locally." if model_available
          else "The selected model is not fully available locally. Prepare the model before processing; the first preparation may download weights.")
    if isinstance(settings, Settings):
        writable = os.access(settings.library.root, os.W_OK)
        check("storage", "Library storage", "ok" if writable else "error",
              "The library directory is writable." if writable else "The library directory is not writable. Check its permissions.")
    if config["detector"] == "heuristic":
        check("classifier", "Advertisement detector", "warning", "Local rules are selected. Subtle host-read ads and ads without explicit commercial cues need manual review.")
    elif config["ai_base_url"] and config["ai_model"]:
        check("classifier", "Advertisement detector", "ok", "An AI endpoint and model are configured. Use Test classifier to verify the provider; diagnostics do not contact it.")
        address = urlsplit(config["ai_base_url"])
        if address.scheme == "http" and address.hostname not in {"127.0.0.1", "localhost", "::1"}:
            check("classifier_transport", "Classifier connection", "warning", "This classifier uses unencrypted HTTP. Use HTTPS for a remote provider.")
    elif config["ai_base_url"] or config["ai_model"] or config["key_configured"] or config["detector"] == "ai":
        check("classifier", "Advertisement detector", "error", "AI detection requires both an endpoint URL and a model name. API keys are optional for local providers.")
    else:
        check("classifier", "Advertisement detector", "warning", "Local rules are available. Configure a contextual AI classifier to help identify ads without bumpers.")
    return {"checks": checks, "ready": model_available and all(item["status"] != "error" for item in checks)}


def test_classifier(settings) -> dict:
    """Explicit opt-in provider check using only a fixed, synthetic transcript."""
    from .processing import detect_ads

    config = _configuration(settings)
    if not config["ai_base_url"] or not config["ai_model"]:
        return {"ok": False, "message": "Configure both the classifier URL and model first.", "model": config["ai_model"]}
    transcript = {"language": "en", "duration": 15.0, "segments": [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "Today we explain why leaves change color in autumn."},
        {"id": 1, "start": 5.0, "end": 10.0, "text": "This episode is sponsored by Example Coffee. Use code EXAMPLE for twenty percent off your first order."},
        {"id": 2, "start": 10.0, "end": 15.0, "text": "Now back to the show. Chlorophyll makes most leaves look green."},
    ]}
    try:
        cuts = detect_ads(transcript, detector="ai", config=config)
    except Exception:
        # Transport exceptions can include full URLs and authorization headers.
        return {"ok": False, "message": "Classifier test failed. Check the endpoint, model, and environment credential; the provider must return valid classification JSON.", "model": config["ai_model"]}
    return {"ok": True, "message": "The classifier responded with valid results for a synthetic sample. This checks connectivity and response format, not podcast accuracy.",
            "model": config["ai_model"], "detected_ads": len(cuts)}


def warm_transcription_model(settings, progress: Optional[Callable] = None) -> dict:
    """Explicitly download/load model weights before the first episode job."""
    config = _configuration(settings)
    if progress:
        progress("Preparing the local transcription model; the first run may download model weights")
    try:
        from faster_whisper import WhisperModel
    except (ImportError, OSError) as exc:
        raise ProcessingError("Model preparation requires faster-whisper. Install castwell's transcription dependencies.") from exc
    try:
        cache = Path(config["model_cache"])
        cache.mkdir(parents=True, exist_ok=True)
        model = config["transcription_model"]
        local = Path(model).expanduser()
        if local.is_dir():
            model = str(local.resolve())
        engine = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=min(4, os.cpu_count() or 1), download_root=str(cache))
        del engine
    except Exception as exc:
        raise ProcessingError("Model preparation failed. Check the model name or local directory, model-host network access, disk space, and available memory.") from exc
    if progress:
        progress("The transcription model is ready")
    return {"ok": True, "model": config["transcription_model"], "message": "The transcription model loaded successfully and is ready for local processing."}
