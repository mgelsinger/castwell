#!/usr/bin/env python3
"""Check a running local Castwell installation without changing its library.

Only GET requests are used. The checker never runs transcription, downloads a
model, tests inference, follows redirects, or contacts a hosted classifier.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


RESPONSE_LIMIT = 32 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, address):
        return None


def local_url(value):
    """Accept literal loopback addresses and localhost, without credentials."""
    parsed = urlsplit(value)
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        parsed.port
    except ValueError:
        loopback = False
    if (not loopback or parsed.scheme not in {"http", "https"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in value or "#" in value
            or "\\" in value or any(char.isspace() for char in value)):
        raise ValueError("Use a loopback HTTP(S) address without credentials, query, or fragment.")
    return value.rstrip("/")


def get_json(address, timeout=8):
    address = local_url(address)
    request = Request(address, headers={"Accept": "application/json"}, method="GET")
    # No proxy, netrc, authentication, redirects, or caller environment secrets.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        data = response.read(RESPONSE_LIMIT + 1)
    if len(data) > RESPONSE_LIMIT:
        raise ValueError("Readiness response exceeds the allowed size.")
    return json.loads(data)


def listener_addresses(port):
    """Read listener addresses from the OS; an unknown result is not a pass."""
    if sys.platform == "win32":
        command = ["netstat", "-an", "-p", "tcp"]
    elif shutil.which("ss"):
        command = ["ss", "-H", "-ltn"]
    elif shutil.which("netstat"):
        command = ["netstat", "-an"]
    else:
        return None
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=8, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    addresses = []
    for line in output.splitlines():
        fields = line.split()
        if not fields or not any("LISTEN" in field.upper() for field in fields):
            continue
        for field in fields:
            match = re.fullmatch(r"(.+?)[.:](\d+)", field)
            if match and int(match.group(2)) == port:
                addresses.append(match.group(1).strip("[]"))
                break
    return addresses or None


def is_loopback(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == "localhost"


def inspect_library(directory):
    """Use SQLite read-only mode, including committed WAL data, without migrations."""
    database = directory / "library.sqlite3"
    if not database.is_file():
        raise ValueError("The selected library database does not exist.")
    ids, counts = set(), {"episodes": 0, "originals": 0, "cleaned": 0, "transcripts": 0, "approved_cuts": 0}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=8) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"] or list(connection.execute("PRAGMA foreign_key_check")):
            raise ValueError("SQLite integrity checks failed. Restore or repair a verified backup.")
        for row in connection.execute("SELECT id,metadata,audio,cleaned,transcript,cuts,duration FROM episodes"):
            ids.add(row["id"])
            counts["episodes"] += 1
            if not isinstance(json.loads(row["metadata"]), dict):
                raise ValueError("An episode has invalid metadata.")
            for field, count in (("audio", "originals"), ("cleaned", "cleaned")):
                if row[field]:
                    media = Path(row[field]).resolve()
                    if not media.is_relative_to(directory) or not media.is_file() or media.stat().st_size == 0:
                        raise ValueError("A stored recording is missing or empty. Restore its media from backup.")
                    counts[count] += 1
            if row["cleaned"] and not row["audio"]:
                raise ValueError("A cleaned recording has no original recording reference.")
            duration = row["duration"]
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                raise ValueError("An episode has invalid audio duration.")
            if row["transcript"]:
                transcript = json.loads(row["transcript"])
                if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
                    raise ValueError("An episode has an invalid transcript structure.")
                counts["transcripts"] += 1
            cuts = json.loads(row["cuts"])
            if not isinstance(cuts, list):
                raise ValueError("An episode has invalid cut decisions.")
            for cut in cuts:
                if not isinstance(cut, dict):
                    raise ValueError("An episode has invalid cut decisions.")
                start, end = cut.get("start"), cut.get("end")
                if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in (start, end)) or not 0 <= start < end <= duration):
                    raise ValueError("A stored cut exceeds its original audio timeline.")
                counts["approved_cuts"] += cut.get("approved") is True
        counts["revisions"] = connection.execute("SELECT count(*) FROM cut_revisions").fetchone()[0]
        counts["subscriptions"] = connection.execute("SELECT count(*) FROM feeds").fetchone()[0]
        for row in connection.execute("SELECT cuts FROM cut_revisions"):
            archived_cuts = json.loads(row[0])
            if not isinstance(archived_cuts, list) or any(not isinstance(cut, dict) for cut in archived_cuts):
                raise ValueError("A stored cut revision has invalid JSON structure.")
        for row in connection.execute("SELECT value FROM settings"):
            json.loads(row[0])
    return ids, counts


def check_release(app_url, data_dir, *, minimum_free_gb=20, require_local_ai=False):
    app_url = local_url(app_url)
    data_dir = Path(data_dir).expanduser().resolve()
    checks = []
    result = {"format": "castwell-release-readiness", "version": 2,
              "checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "local single-user operational readiness; ad-removal acceptance is separate",
              "ad_removal_acceptance": {"status": "not_measured", "inference_requests": 0},
              "production_ready": False,
              "inference_requests": 0, "library_mutations": 0, "checks": checks}

    def add(identifier, status, detail, **metrics):
        checks.append({"id": identifier, "status": status, "detail": detail, **metrics})

    def request(path):
        try:
            return get_json(app_url + path)
        except Exception:
            # Exception text can contain endpoints, premium URLs, or response text.
            add(path, "error", "The local application did not return valid readiness data.")
            return None

    parsed = urlsplit(app_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = listener_addresses(port)
    if addresses is None:
        add("listen_binding", "error", "Could not verify the HTTP listener. Check the actual listen address before release.")
    else:
        safe = all(is_loopback(address) for address in addresses)
        add("listen_binding", "ok" if safe else "error", "The HTTP listener is bound to loopback." if safe
            else "A listener on this port accepts non-loopback connections. This checker covers local operation only.")
    settings = request("/api/settings")
    health = request("/api/health")
    if isinstance(health, dict) and health.get("status") == "ok":
        add("application_health", "ok", "The application health endpoint is responding.", version=str(health.get("version", "unknown")))
    elif health is not None:
        add("application_health", "error", "The application health endpoint did not report an operational server.")
    diagnostic = request("/api/diagnostics")
    jobs = request("/api/jobs")
    refresh = request("/api/feeds/refresh/status")
    preparation = request("/api/models/status")
    for identifier, state in (("feed_refresh_idle", refresh), ("model_preparation_idle", preparation)):
        if isinstance(state, dict) and state.get("status") in {"idle", "ready", "complete", "error"}:
            add(identifier, "ok", "No background operation is running.")
        elif state is not None:
            add(identifier, "error", "A background operation is active or its idle state could not be verified. Wait before release or backup.")
    episodes = request("/api/episodes")
    podcasts = request("/api/podcasts")
    if (isinstance(podcasts, dict) and isinstance(podcasts.get("podcasts"), list)
            and all(isinstance(show, dict) and isinstance(show.get("id"), str) and show.get("ad_mode") in {"keep", "remove"}
                    for show in podcasts["podcasts"])):
        add("podcast_suite", "ok", "The podcast suite API is available.", podcasts=len(podcasts["podcasts"]))
    elif podcasts is not None:
        add("podcast_suite", "error", "The podcast suite API has an unexpected response.")
    if isinstance(diagnostic, dict) and isinstance(diagnostic.get("checks"), list):
        required = {"ffmpeg", "ffprobe", "transcription", "model", "storage"}
        reported = {check.get("id") for check in diagnostic["checks"] if isinstance(check, dict)}
        if not required <= reported:
            add("environment_complete", "error", "The server did not report every required processing prerequisite.")
        for check in diagnostic["checks"]:
            if not isinstance(check, dict):
                add("environment", "error", "An environment result has an unexpected response.")
                continue
            status = check.get("status", "error")
            if status not in {"ok", "warning", "error"}:
                status = "error"
            if check.get("id") == "model" and status != "ok":
                status = "error"
            add("environment_" + str(check.get("id", "unknown")), status, str(check.get("detail", "")))
    elif diagnostic is not None:
        add("environment", "error", "Environment diagnostics have an unexpected response.")
    if isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list):
        add("queue_idle", "ok" if not jobs["jobs"] else "error", "No episode jobs are queued." if not jobs["jobs"]
            else "Wait for queued jobs to finish before backup or release.", queued=len(jobs["jobs"]))
    elif jobs is not None:
        add("queue_idle", "error", "The processing queue has an unexpected response.")
    if isinstance(settings, dict):
        manual_verified = settings.get("review_only") is True and settings.get("ai_policy") == "verified"
        scene_mode = settings.get("ai_policy") == "scene" and isinstance(settings.get("review_only"), bool)
        threshold = settings.get("auto_approve_threshold", .90)
        scene_mode = scene_mode and (not isinstance(threshold, bool) and isinstance(threshold, (int, float))
                                    and math.isfinite(threshold) and .90 <= threshold <= 1)
        approved = manual_verified or scene_mode
        detail = ("Scene detection requires complete aligned commercial and endpoint proof; unresolved work remains review."
                  if scene_mode else "Verified detection and manual cut approval are enabled." if manual_verified
                  else "Use verified detection with manual approval, or scene detection with a threshold of at least 0.90.")
        add("cut_approval", "ok" if approved else "error", detail,
            mode="manual" if settings.get("review_only") is True else "automatic_with_structural_proof")
        ai = bool(settings.get("ai_configured")) and settings.get("detector") != "heuristic"
        if ai:
            try:
                address = local_url(settings.get("ai_base_url", ""))
            except (ValueError, TypeError):
                add("local_classifier", "error" if require_local_ai else "warning",
                    "A hosted classifier is configured. It was not contacted; this check cannot verify its readiness or cost.")
            else:
                probe_safe = (isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list) and not jobs["jobs"]
                              and isinstance(preparation, dict) and preparation.get("status") in {"idle", "ready", "complete", "error"})
                if not probe_safe:
                    # /models can wake a sleeping native server. Do not reload
                    # its weights while CUDA speech recognition owns memory.
                    add("local_classifier", "error", "Wait for episode processing and speech-model preparation before checking the classifier.")
                else:
                    try:
                        models = get_json(address + "/models")
                        names = {model["id"] for model in models["data"] if isinstance(model, dict) and isinstance(model.get("id"), str)}
                        available = settings.get("ai_model") in names
                    except Exception:
                        available = False
                    add("local_classifier", "ok" if available else "error", "The configured model is listed by the local classifier. No inference was requested." if available
                        else "The local classifier does not list the configured model. Start its pinned server profile.")
                classifier = urlsplit(address)
                classifier_port = classifier.port or (443 if classifier.scheme == "https" else 80)
                model_addresses = listener_addresses(classifier_port)
                model_local = bool(model_addresses) and all(is_loopback(value) for value in model_addresses)
                add("classifier_binding", "ok" if model_local else "error", "The local classifier listener is bound to loopback." if model_local
                    else "Could not verify a loopback-only classifier listener. Check the model server listen address.")
        else:
            add("local_classifier", "error" if require_local_ai else "warning", "Local rules are selected. Enable the reviewed local classifier for contextual detection.")
    elif settings is not None:
        add("settings", "error", "Application settings have an unexpected response.")
    try:
        ids, counts = inspect_library(data_dir)
        add("library_integrity", "ok", "SQLite, stored JSON, cut ranges, and referenced media passed read-only checks.", **counts)
        if isinstance(episodes, dict) and isinstance(episodes.get("episodes"), list):
            matches = ids == {episode["id"] for episode in episodes["episodes"]}
            add("library_identity", "ok" if matches else "error", "Application episode IDs match the selected library." if matches
                else "The running application and selected data directory contain different episodes.")
        else:
            add("library_identity", "error", "Could not compare the running application with the selected library.")
        free = shutil.disk_usage(data_dir).free
        enough = free >= minimum_free_gb * 1024**3
        add("free_storage", "ok" if enough else "error", "Storage meets the selected free-space reserve." if enough
            else "Free storage is below the selected reserve. Free space before batch processing.", free_gb=round(free / 1024**3, 2), minimum_gb=minimum_free_gb)
    except Exception:
        # Keep stored titles, paths, premium URLs and malformed JSON out of logs.
        add("library_integrity", "error", "Library integrity or media checks failed. Check the selected data directory and restore a verified backup if necessary.")
    add("accuracy_acceptance", "warning", "This report verifies operation only. Independent full-recording acceptance is required to establish ad-removal accuracy.")
    result["operational_ready"] = not any(check["status"] == "error" for check in checks)
    # Retain the existing CLI return-code contract. The scope and separate
    # fields prevent an operational pass from claiming removal acceptance.
    result["ready"] = result["operational_ready"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", "--base-url", dest="app_url", default="http://127.0.0.1:8000")
    parser.add_argument("--data-dir", type=Path, required=True, help="Existing library directory used by this server")
    parser.add_argument("--minimum-free-gb", type=float, default=20, help="Free storage reserve; default 20 GiB")
    parser.add_argument("--require-local-ai", action="store_true", help="Fail unless the configured local classifier lists the selected model")
    parser.add_argument("--output", "--json", dest="output", type=Path, help="Write the non-secret JSON report outside the library")
    args = parser.parse_args()
    if not math.isfinite(args.minimum_free_gb) or args.minimum_free_gb < 0:
        parser.error("The storage reserve must be finite and non-negative.")
    if args.output:
        output = args.output.expanduser().resolve()
        root = args.data_dir.expanduser().resolve()
        if output == root or root in output.parents:
            parser.error("Write the readiness report outside the library directory.")
    try:
        report = check_release(args.app_url, args.data_dir, minimum_free_gb=args.minimum_free_gb,
                               require_local_ai=args.require_local_ai)
    except ValueError as exc:
        parser.error(str(exc))
    body = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
    print(body, end="")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
