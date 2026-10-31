"""Yield an owned llama.cpp model's GPU memory before CUDA speech inference."""

from __future__ import annotations

import gc
import math
import time
from urllib.parse import urlsplit

from .processing import ProcessingError, _check_cancel


def wait_classifier_sleep(base_url, *, should_cancel=None, progress=None, timeout=15):
    """Poll only the non-waking props endpoint of a literal loopback server.

    The launcher must enable llama.cpp's idle sleep. An unsupported server is
    an explicit failure, never permission to overcommit GPU memory or use CPU.
    """
    import requests

    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None or parsed.password is not None or parsed.query
            or parsed.fragment or parsed.path.rstrip("/") != "/v1"
            or "\\" in base_url or any(char.isspace() for char in base_url)):
        raise ValueError("GPU memory sharing requires the owned loopback classifier")
    parsed.port
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("Invalid GPU phase timeout")
    address = f"{parsed.scheme}://{parsed.netloc}/props"
    deadline = time.monotonic() + timeout
    if progress:
        progress("Preparing GPU memory for speech recognition")
    with requests.Session() as session:
        session.trust_env = False
        while True:
            _check_cancel(should_cancel)
            try:
                with session.get(address, timeout=min(2, timeout), allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ValueError("Unsupported sleep endpoint")
                    properties = response.json()
                if not isinstance(properties, dict) or type(properties.get("is_sleeping")) is not bool:
                    raise ValueError("Missing sleep status")
            except (requests.RequestException, ValueError) as exc:
                raise ProcessingError("The local classifier could not release GPU memory. Restart it with idle sleep enabled before transcribing.") from exc
            if properties["is_sleeping"]:
                _check_cancel(should_cancel)
                return
            if time.monotonic() >= deadline:
                raise ProcessingError("The local classifier is still using GPU memory. Wait for its work to finish before transcribing.")
            time.sleep(.1)


def release_speech_model():
    """Release cached CUDA speech weights before the classifier wakes again."""
    from . import processing

    with processing._MODEL_LOCK:
        processing._cached_model.cache_clear()
    gc.collect()
