"""One-attempt official Jev transport with an append-only spending ledger.

The ledger is a conservative local spending bound, not a provider invoice.
Unknown attempts retain their entire reservation and are never sent again.
No environment credentials, proxy configuration, redirects, or retries are used.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time

import requests

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
PROVIDER = "official-typesafe-jev"
MAX_BUDGET_USD = Decimal("5")
MAX_INPUT_TOKENS = 65536
INPUT_NANO_USD_PER_TOKEN = 42
RESERVATION_NANO_USD = MAX_INPUT_TOKENS * INPUT_NANO_USD_PER_TOKEN
MIN_START_INTERVAL_SECONDS = 1.0
RATE_CONFIG = {
    "checked_date": "2026-10-09",
    "input_usd_per_million_tokens": "0.042",
    "output_usd_per_million_tokens": "0",
    "reserved_input_tokens": MAX_INPUT_TOKENS,
    "input_nano_usd_per_token": INPUT_NANO_USD_PER_TOKEN,
    "reservation_nano_usd": RESERVATION_NANO_USD,
}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_ZERO_HASH = "0" * 64


class JevTransportError(RuntimeError):
    """A fixed-code configuration or integrity failure; never contains secrets."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _invalid_constant(_value):
    raise ValueError


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    try:
        return _sha(Path(path).read_bytes())
    except OSError:
        raise JevTransportError("source_unavailable") from None


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise JevTransportError("invalid_source_sha256")
    return value


def _budget(value):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int, float)):
        raise JevTransportError("invalid_budget")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > MAX_BUDGET_USD:
            raise JevTransportError("invalid_budget")
    except (InvalidOperation, ValueError):
        raise JevTransportError("invalid_budget") from None
    return amount


def _money(nano_usd):
    return str(Decimal(nano_usd).scaleb(-3))


@contextmanager
def _ledger_lock(path):
    # Thread locks supplement OS locks, including separate instances in one process.
    lock_path = path.with_name(path.name + ".lock")
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(str(lock_path), threading.RLock())
    with thread_lock:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            raise JevTransportError("ledger_io_error") from None


class JevTransport:
    """Send fixed-model requests only after a durable maximum-cost reservation.

    ``request_source_sha256`` identifies the calling plan or runner. Supplying
    its path additionally verifies its bytes on every call. Reopening a ledger
    requires the same budget, source identity, transport, and rate policy.
    Returned bodies are never written to this ledger. Typed answer validation
    remains the caller's responsibility; model and billing usage are checked here.
    """

    def __init__(self, *, api_key, ledger_path, budget_usd=Decimal("5"),
                 request_source_sha256, request_source_path=None):
        if (not isinstance(api_key, str) or not api_key.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in api_key)):
            raise JevTransportError("invalid_api_key")
        self._key = api_key.strip()
        self._budget = _budget(budget_usd)
        self._path = Path(ledger_path).resolve()
        self._source_path = Path(request_source_path).resolve() if request_source_path else None
        self._source_hash = _digest(request_source_sha256)
        self._transport_path = Path(__file__).resolve()
        self._requests_path = Path(requests.__file__).resolve()
        self._config = {
            "schema_version": 1, "provider": PROVIDER, "endpoint": ENDPOINT,
            "model": MODEL, "budget_usd": str(self._budget),
            "rate": dict(RATE_CONFIG), "request_source_sha256": self._source_hash,
            "transport_sha256": _file_sha(self._transport_path),
            "requests_version": requests.__version__,
            "requests_source_sha256": _file_sha(self._requests_path),
            "timeout_seconds": [10, 180], "redirects": False,
            "trust_env": False, "retries": 0,
            "minimum_start_interval_seconds": MIN_START_INTERVAL_SECONDS,
            "absolute_ceiling_usd": str(MAX_BUDGET_USD),
        }
        self._assert_sources()
        with _ledger_lock(self._path):
            if not self._path.exists():
                self._append([], "configuration", self._config)
            self._read()

    def __repr__(self):
        return "JevTransport(provider='official-typesafe-jev', credentials=<redacted>)"

    def _assert_sources(self):
        if (_file_sha(self._transport_path) != self._config["transport_sha256"]
                or _file_sha(self._requests_path) != self._config["requests_source_sha256"]
                or requests.__version__ != self._config["requests_version"]
                or (self._source_path is not None
                    and _file_sha(self._source_path) != self._source_hash)):
            raise JevTransportError("source_changed")

    def _append(self, records, event, data):
        record = {"sequence": len(records), "previous_sha256": records[-1]["sha256"] if records else _ZERO_HASH,
                  "event": event, "data": data,
                  "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
        record["sha256"] = _sha(_canonical(record))
        try:
            with self._path.open("ab") as handle:
                handle.write(_canonical(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            raise JevTransportError("ledger_io_error") from None
        return record

    def _read(self):
        try:
            raw = self._path.read_bytes()
            if not raw or not raw.endswith(b"\n"):
                raise ValueError
            records = [json.loads(line, parse_constant=_invalid_constant) for line in raw.splitlines()]
            previous = _ZERO_HASH
            for index, record in enumerate(records):
                hashed = dict(record)
                actual_hash = hashed.pop("sha256")
                if (type(record["sequence"]) is not int or record["sequence"] != index
                        or record["previous_sha256"] != previous
                        or _sha(_canonical(hashed)) != actual_hash):
                    raise ValueError
                previous = actual_hash
            if records[0]["event"] != "configuration" or _canonical(records[0]["data"]) != _canonical(self._config):
                raise JevTransportError("ledger_configuration_mismatch")
            attempts, auth_stopped = {}, False
            for record in records[1:]:
                data, event = record["data"], record["event"]
                identifier = data["request_id"]
                if event == "reserved":
                    dispatch = data["dispatch_at_unix_seconds"]
                    if (identifier in attempts or type(data["reserved_nano_usd"]) is not int
                            or data["reserved_nano_usd"] != RESERVATION_NANO_USD
                            or isinstance(dispatch, bool) or not isinstance(dispatch, (float, int))
                            or not math.isfinite(dispatch) or dispatch < 0):
                        raise ValueError
                    _digest(data["payload_sha256"])
                    attempts[identifier] = dict(data, state="pending", accounted_nano_usd=RESERVATION_NANO_USD)
                elif event == "completed":
                    attempt = attempts[identifier]
                    if attempt["state"] != "pending":
                        raise ValueError
                    cost = data["accounted_nano_usd"]
                    if type(cost) is not int or not 0 <= cost <= RESERVATION_NANO_USD:
                        raise ValueError
                    usage = data["usage"]
                    if data["state"] == "settled":
                        if self._usage(usage) is None or cost != usage["input_tokens"] * INPUT_NANO_USD_PER_TOKEN:
                            raise ValueError
                    elif data["state"] != "held" or cost != RESERVATION_NANO_USD or usage is not None:
                        raise ValueError
                    attempt.update(data)
                    auth_stopped |= data["http_status"] in (401, 402, 403)
                else:
                    raise ValueError
            total = sum(attempt["accounted_nano_usd"] for attempt in attempts.values())
            if Decimal(total).scaleb(-9) > self._budget:
                raise ValueError
            return records, attempts, total, auth_stopped, _sha(raw)
        except JevTransportError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise JevTransportError("ledger_integrity_error") from None

    @staticmethod
    def _usage(value):
        if not isinstance(value, dict):
            return None
        if any(type(value.get(name)) is not int or not 0 <= value[name] <= MAX_INPUT_TOKENS
               for name in ("input_tokens", "output_tokens")):
            return None
        return {name: value[name] for name in ("input_tokens", "output_tokens")}

    def _receipt(self, records, attempts, total, ledger_sha256, identifier):
        attempt = attempts.get(identifier)
        reserved = attempt["reserved_nano_usd"] if attempt else 0
        accounted = attempt["accounted_nano_usd"] if attempt else 0
        settled = accounted if attempt and attempt["state"] == "settled" else None
        return {
            "ledger_sha256": ledger_sha256, "last_event_sha256": records[-1]["sha256"],
            "request_id": identifier, "state": attempt["state"] if attempt else "not_reserved",
            "reserved_nano_usd": reserved, "reserved_micro_usd": _money(reserved),
            "settled_nano_usd": settled, "settled_micro_usd": _money(settled) if settled is not None else None,
            "accounted_nano_usd": accounted, "accounted_micro_usd": _money(accounted),
            "total_accounted_nano_usd": total, "total_accounted_micro_usd": _money(total),
            "budget_usd": str(self._budget),
        }

    def call(self, request_id, payload):
        """Attempt a request once, or return an explicit durable no-call result."""
        # Serialize dispatch across processes. Budget reads may still occur while
        # a request is active, but a second sender cannot overtake its pacing.
        with _ledger_lock(self._path.with_name(self._path.name + ".dispatch")):
            return self._call(request_id, payload)

    def _call(self, request_id, payload):
        if (not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", request_id) is None
                or self._key in request_id):
            raise JevTransportError("invalid_request_id")
        try:
            encoded = _canonical(payload)
            copied = json.loads(encoded)
            if (not isinstance(copied, dict) or set(copied) != {"model", "state", "questions"}
                    or copied["model"] != MODEL or not isinstance(copied["state"], dict)
                    or not isinstance(copied["questions"], dict) or not copied["questions"]
                    or self._key in encoded.decode("utf-8")):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise JevTransportError("invalid_request_payload") from None
        payload_hash = _sha(encoded)
        self._assert_sources()
        result = {"provider": PROVIDER, "endpoint": ENDPOINT, "requested_model": MODEL,
                  "response_model": None, "status": "not_run", "attempted": False,
                  "http_status": None, "elapsed_seconds": None, "raw_response_text": None,
                  "response": None, "response_redacted": False, "error_type": None,
                  "usage": None, "payload_sha256": payload_hash, "response_sha256": None}
        with _ledger_lock(self._path):
            records, attempts, total, stopped, digest = self._read()
            prior = attempts.get(request_id)
            if prior and prior["payload_sha256"] != payload_hash:
                raise JevTransportError("request_id_payload_mismatch")
            if prior:
                result["error_type"] = "request_already_reserved"
            elif stopped:
                result["error_type"] = "authentication_stopped"
            elif Decimal(total + RESERVATION_NANO_USD).scaleb(-9) > self._budget:
                result["error_type"] = "budget_exhausted"
            else:
                if attempts:
                    # Pacing from completion is stricter than pacing from start
                    # and remains safe if session construction delayed dispatch.
                    if any(item["state"] == "pending" for item in attempts.values()):
                        remaining = MIN_START_INTERVAL_SECONDS
                    else:
                        previous_end = max(item["completed_at_unix_seconds"] for item in attempts.values())
                        remaining = MIN_START_INTERVAL_SECONDS - (time.time() - previous_end)
                    if remaining > 0:
                        time.sleep(remaining)
                self._append(records, "reserved", {"request_id": request_id,
                             "payload_sha256": payload_hash, "reserved_nano_usd": RESERVATION_NANO_USD,
                             "dispatch_at_unix_seconds": time.time()})
            if result["error_type"] is not None:
                result["budget_receipt"] = self._receipt(records, attempts, total, digest, request_id)
                return result

        # The reservation is durable before opening the connection. A crash here
        # leaves a pending reservation that no later invocation may retransmit.
        started = time.monotonic()
        result.update(status="error", attempted=True)
        response = None
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.auth = None
                session.proxies.clear()
                session.cookies.clear()
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
                response = session.post(ENDPOINT, json=copied,
                                        headers={"Authorization": "Bearer " + self._key},
                                        timeout=(10, 180), allow_redirects=False, verify=True, proxies={})
                status = response.status_code
                if type(status) is not int or not 100 <= status <= 599:
                    result["error_type"] = "invalid_http_status"
                else:
                    result["http_status"] = status
                    raw = response.text
                    if not isinstance(raw, str):
                        raise ValueError
                    result["response_sha256"] = _sha(raw.encode("utf-8"))
                    result["response_redacted"] = self._key in raw
                    result["raw_response_text"] = None if result["response_redacted"] else raw
                    try:
                        parsed = json.loads(result["raw_response_text"], parse_constant=_invalid_constant)
                    except (ValueError, TypeError):
                        parsed = None
                    if parsed is not None and self._key in _canonical(parsed).decode("utf-8"):
                        result.update(response_redacted=True, raw_response_text=None)
                        parsed = None
                    result["response"] = parsed
                    if isinstance(parsed, dict) and isinstance(parsed.get("model"), str):
                        result["response_model"] = parsed["model"]
                    if status != 200:
                        result["error_type"] = "account_blocked" if status in (401, 402, 403) else "http_error"
                    elif result["response_redacted"]:
                        result["error_type"] = "response_contained_credentials"
                    elif not isinstance(parsed, dict):
                        result["error_type"] = "invalid_json"
                    elif parsed.get("model") != MODEL:
                        result["error_type"] = "response_model_mismatch"
                    elif (usage := self._usage(parsed.get("usage"))) is None:
                        result["error_type"] = "invalid_usage"
                    else:
                        result.update(status="ok", usage=usage)
        except requests.Timeout:
            result["error_type"] = "timeout"
        except requests.RequestException:
            result["error_type"] = "transport_error"
        except Exception:
            # Neither provider bodies nor exception strings belong in errors.
            result["error_type"] = "transport_internal_error"
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        result["elapsed_seconds"] = max(0.0, time.monotonic() - started)
        cost = (result["usage"]["input_tokens"] * INPUT_NANO_USD_PER_TOKEN
                if result["usage"] is not None else RESERVATION_NANO_USD)
        state = "settled" if result["usage"] is not None else "held"
        with _ledger_lock(self._path):
            records, attempts, _, _, _ = self._read()
            if attempts[request_id]["state"] != "pending":
                raise JevTransportError("reservation_state_changed")
            self._append(records, "completed", {"request_id": request_id, "state": state,
                         "accounted_nano_usd": cost, "usage": result["usage"],
                         "http_status": result["http_status"], "error_type": result["error_type"],
                         "completed_at_unix_seconds": time.time(),
                         "response_sha256": result["response_sha256"]})
            records, attempts, total, _, digest = self._read()
            result["budget_receipt"] = self._receipt(records, attempts, total, digest, request_id)
        return result

    one_attempt = call
