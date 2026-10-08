#!/usr/bin/env python3
"""Exercise the complete gallery with local fixtures and no model downloads.

Run with the project's development dependencies installed:
    python scripts/smoke_browser.py --artifacts /tmp/castwell-browser-check

Uses a system Chromium when available, otherwise Playwright's installed browser.
The app, feed server, data directory, and audio fixtures are isolated per run.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = {"language": "en", "duration": 12.0, "segments": [
    {"id": 0, "start": 0.0, "end": 3.0, "text": "Today we explore the science of the night sky."},
    {"id": 1, "start": 3.0, "end": 5.0, "text": "This episode is sponsored by Example Coffee."},
    {"id": 2, "start": 5.0, "end": 7.0, "text": "Use our code STARS for a free trial."},
    {"id": 3, "start": 7.0, "end": 12.0, "text": "Now back to the show. Early astronomers carefully observed the planets."},
]}
TITLE = "A night under the stars"


def note(message):
    print(message, flush=True)


def wait_js(page, expression, argument=None, *, label="browser state", timeout=20):
    """Poll a function from Python; wait_for_function uses eval blocked by CSP."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate(expression, argument):
            return
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {label}")


def episode(page, episode_id):
    response = page.request.get(f"/api/episodes/{episode_id}")
    assert response.ok, response.text()
    return response.json()


def wait_episode(page, episode_id, status, *, cleaned=None):
    wait_js(page, """async ({id, status, cleaned}) => {
        const record = await (await fetch(`/api/episodes/${id}`)).json();
        if (record.status === 'error') throw new Error(record.error || 'Episode failed');
        return record.status === status && (cleaned === null || record.has_cleaned === cleaned);
    }""", {"id": episode_id, "status": status, "cleaned": cleaned}, label=f"episode {status}")


class FixtureFeed(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == "/feed.xml":
            root = ET.Element("rss", version="2.0")
            channel = ET.SubElement(root, "channel")
            ET.SubElement(channel, "title").text = "Field notes & discoveries"
            ET.SubElement(channel, "description").text = "Local browser smoke fixtures"
            for number in range(self.server.episode_count):
                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "title").text = f"A field recording {number + 1}"
                ET.SubElement(item, "guid").text = f"local-browser-{number}"
                ET.SubElement(item, "enclosure", url=self.server.base + "/audio.wav", type="audio/wav")
            body, mime = ET.tostring(root, encoding="utf-8"), "application/rss+xml"
        elif urlsplit(self.path).path == "/audio.wav":
            body, mime = self.server.audio, "audio/wav"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def fixture_server(audio):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureFeed)
    server.base = f"http://127.0.0.1:{server.server_port}"
    server.audio = audio.read_bytes()
    server.episode_count = 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def app_server(workspace, artifacts):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CASTWELL_AI_") or name in {"CASTWELL_TRANSCRIPTION_MODEL", "CASTWELL_MODEL_CACHE", "CASTWELL_DATA_DIR"}:
            environment.pop(name)
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CASTWELL_MODEL_CACHE=str(workspace / "models"),
                       NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    log_path = workspace / "app.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "castwell", "--host", "127.0.0.1", "--port", str(port),
                                    "--data-dir", str(workspace / "library")], cwd=ROOT, env=environment,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("The gallery failed to start:\n" + log_path.read_text(errors="replace")[-5000:])
                try:
                    with build_opener(ProxyHandler({})).open(base + "/api/settings", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("The gallery did not start within 20 seconds")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if artifacts:
                shutil.copyfile(log_path, artifacts / "app.log")


def save_download(page, selector, destination):
    with page.expect_download() as pending:
        page.locator(selector).click()
    download = pending.value
    assert download.failure() is None, "Browser download failed"
    download.save_as(destination)
    assert destination.is_file() and destination.stat().st_size > 0
    return destination


def run_browser(base, feed, audio, transcript_file, workspace, chromium, artifacts):
    from playwright.sync_api import sync_playwright, expect

    errors = []
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
        if chromium:
            options["executable_path"] = chromium
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 1100}, accept_downloads=True)

        def local_only(route):
            address = urlsplit(route.request.url)
            if address.scheme in {"data", "blob"} or address.hostname in {"127.0.0.1", "localhost", "::1"}:
                route.continue_()
            else:
                errors.append("Unexpected external browser request: " + address.hostname)
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("dialog", lambda dialog: dialog.accept())

        def screenshot(name):
            if artifacts:
                page.screenshot(path=str(artifacts / f"{name}.png"), full_page=True)

        try:
            note("Browser: upload audio, import a timestamped transcript, and configure review")
            page.goto(base, wait_until="networkidle")
            expect(page.locator("#empty-state")).to_be_visible()
            expect(page.locator("#playback-speed")).to_have_value("1")
            page.locator("#add-more").click()
            page.locator("#upload-button").click()
            page.locator("#audio-upload").set_input_files(str(audio))
            with page.expect_response(lambda response: response.url.endswith("/api/audio") and response.request.method == "POST") as uploaded:
                page.locator("#upload-submit").click()
            assert uploaded.value.status == 201, uploaded.value.text()
            episode_id = uploaded.value.json()["id"]
            expect(page.locator("#episode-dialog")).to_be_visible()
            expect(page.locator("#detail-title")).to_have_text(TITLE)
            page.locator("#transcript-file").set_input_files(str(transcript_file))
            expect(page.locator("#transcript-lines .transcript-line")).to_have_count(4)
            page.locator("#close-detail").click()
            page.locator("#sidebar-settings").click()
            expect(page.locator("#save-settings")).to_be_enabled()
            expect(page.locator("#setting-review-only")).to_be_checked()
            expect(page.locator("#setting-ai-reasoning")).not_to_be_checked()
            page.locator(".settings-advanced summary").click()
            page.locator("#setting-ai-reasoning").check()
            page.locator("#setting-detector").select_option("heuristic")
            page.locator("#setting-review-only").check()
            with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH") as saved:
                page.locator("#save-settings").click()
            assert saved.value.json()["review_only"] is True
            expect(page.locator("#settings-error")).to_be_hidden()
            assert page.evaluate("async () => (await (await fetch('/api/settings')).json()).ai_reasoning") is True
            page.locator("#setting-ai-reasoning").uncheck()
            with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                page.locator("#save-settings").click()
            page.locator('[data-close="settings-dialog"]').click()

            note("Browser: queue the episode, review suggestions, and render real cleaned audio")
            page.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
            with page.expect_response(lambda response: response.url.endswith(f"/{episode_id}/process")) as queued:
                page.locator("#detail-process").click()
            assert queued.value.status == 202 and queued.value.json()["queued"]
            wait_episode(page, episode_id, "review", cleaned=False)
            page.locator("#tab-cuts").click()
            expect(page.locator("#cut-list .cut-row")).to_have_count(1)
            approval = page.locator("#cut-list input[type=checkbox]")
            expect(approval).not_to_be_checked()
            expect(page.get_by_label("Cut 1 start in seconds", exact=True)).to_have_value("3")
            expect(page.get_by_label("Cut 1 end in seconds", exact=True)).to_have_value("7")
            approval.check()
            page.locator("#render-cuts").click()
            wait_episode(page, episode_id, "ready", cleaned=True)
            expect(page.locator("#play-cleaned")).to_be_enabled()
            assert episode(page, episode_id)["removed_seconds"] == 4
            screenshot("desktop-review")

            note("Browser: verify waveform, both audio versions, playback speed, and downloads")
            wait_js(page, "() => document.querySelector('#audio-player').readyState >= 2", label="original media decoding")
            assert abs(page.locator("#audio-player").evaluate("audio => audio.duration") - 12) < 0.1
            assert page.locator("#audio-player").evaluate("audio => audio.playbackRate") == 1
            expect(page.locator("#waveform-notice")).to_contain_text("click the waveform")
            assert page.request.get(f"/api/episodes/{episode_id}/waveform").json()["peaks"]
            waveform = page.locator("#audio-waveform")
            bounds = waveform.bounding_box()
            assert bounds and bounds["width"] > 100
            waveform.click(position={"x": bounds["width"] / 6, "y": bounds["height"] / 2})
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return !audio.paused && audio.currentTime > 2.1 && !audio.error; }", label="original playback")
            page.locator("#audio-player").evaluate("audio => audio.pause()")
            page.locator("#playback-speed").select_option("1.5")
            assert page.locator("#audio-player").evaluate("audio => audio.playbackRate") == 1.5
            page.locator("#play-cleaned").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/cleaned'); }", label="cleaned media decoding")
            assert 7.9 <= page.locator("#audio-player").evaluate("audio => audio.duration") <= 8.2
            start = page.locator("#audio-player").evaluate("audio => audio.currentTime")
            page.locator("#audio-player").evaluate("audio => audio.play()")
            wait_js(page, "start => { const audio = document.querySelector('#audio-player'); return audio.currentTime > start + .2 && !audio.error; }", start, label="cleaned playback")
            page.locator("#audio-player").evaluate("audio => audio.pause()")
            page.locator("#tab-transcript").click()
            page.locator("#transcript-version").select_option("cleaned")
            expect(page.locator("#transcript-lines .transcript-line")).to_have_count(2)
            srt = save_download(page, "#download-srt", workspace / "cleaned.srt").read_text()
            assert "00:00:03,000 --> 00:00:08,000" in srt and "sponsor" not in srt.lower()
            page.locator("#transcript-version").select_option("original")
            transcript = json.loads(save_download(page, "#download-json", workspace / "transcript.json").read_text())
            assert len(transcript["segments"]) == 4
            decisions = json.loads(save_download(page, "#download-decisions", workspace / "decisions.json").read_text())
            assert decisions["cuts"][0]["start"] == 3 and decisions["timeline"] == "original"

            note("Browser: edit cuts, restore history, and preserve favorites and listening progress")
            page.locator("#tab-cuts").click()
            page.get_by_label("Cut 1 start in seconds", exact=True).fill("3.25")
            page.locator("#save-cuts").click()
            wait_js(page, "async id => (await (await fetch(`/api/episodes/${id}`)).json()).cuts[0]?.start === 3.25", episode_id, label="saved adjusted cut")
            page.locator("#cut-history-button").click()
            expect(page.locator("#cut-history .revision-row").first).to_be_visible()
            page.locator("#cut-history .revision-row").first.get_by_role("button", name="Restore", exact=True).click()
            expect(page.get_by_label("Cut 1 start in seconds", exact=True)).to_have_value("3")
            assert not episode(page, episode_id)["has_cleaned"]
            page.locator("#render-cuts").click()
            wait_episode(page, episode_id, "ready", cleaned=True)
            page.locator("#detail-favorite").click()
            expect(page.locator("#detail-favorite")).to_have_attribute("aria-pressed", "true")
            page.locator("#play-original").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && audio.currentSrc.endsWith('/original'); }", label="original media switch")
            waveform.press("Home")
            bounds = waveform.bounding_box()
            waveform.click(position={"x": bounds["width"] * 2 / 3, "y": bounds["height"] / 2})
            wait_js(page, "() => document.querySelector('#audio-player').currentTime >= 8", label="resume position after the removed advertisement")
            page.locator("#audio-player").evaluate("audio => audio.pause()")
            page.locator("#close-detail").click()
            expect(page.locator("#mini-player")).to_be_visible()
            expect(page.locator("#mini-toggle")).to_have_attribute("aria-label", "Play")
            saved_position = episode(page, episode_id)["position"]
            assert 7.8 <= saved_position < 10, saved_position
            page.locator("#mini-toggle").click()
            wait_js(page, "() => !document.querySelector('#audio-player').paused", label="mini-player playback")
            page.locator("#mini-toggle").click()
            page.reload(wait_until="networkidle")
            page.locator('[data-filter="favorites"]').click()
            expect(page.locator("#episodes .episode-card")).to_have_count(1)
            page.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
            wait_js(page, "() => document.querySelector('#audio-player').readyState >= 2", label="resumed audio metadata")
            # Prepared episodes reopen on the cleaned timeline. Switch back to
            # compare the stored resume position, which is always original time.
            page.locator("#play-original").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && audio.currentSrc.endsWith('/original'); }", label="resumed original timeline")
            resumed = page.locator("#audio-player").evaluate("audio => audio.currentTime")
            persisted = episode(page, episode_id)["position"]
            assert abs(resumed - persisted) < 0.3, (resumed, persisted)
            expect(page.locator("#playback-speed")).to_have_value("1.5")
            page.locator("#detail-archive").click()
            expect(page.locator("#detail-archive")).to_have_text("Unarchive")
            page.locator("#close-detail").click()
            expect(page.locator("#episodes .episode-card")).to_have_count(0)
            page.locator('[data-filter="archived"]').click()
            expect(page.locator("#episodes .episode-card")).to_have_count(1)
            page.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
            page.locator("#detail-archive").click()
            expect(page.locator("#detail-archive")).to_have_text("Archive")
            page.locator("#close-detail").click()
            page.locator('[data-filter="all"]').click()

            note("Browser: import and refresh a local subscription, then unsubscribe")
            page.locator("#import-button").click()
            page.locator("#feed-url").fill(feed.base + "/feed.xml")
            page.locator("#import-submit").click()
            expect(page.locator("#import-dialog")).not_to_be_visible()
            expect(page.locator("#episodes .episode-card")).to_have_count(2)
            page.locator("#sidebar-subscriptions").click()
            expect(page.locator("#subscription-list .subscription-row")).to_have_count(1)
            feed.episode_count = 2
            page.locator("#subscription-list").get_by_role("button", name="Refresh", exact=True).click()
            expect(page.locator("#subscription-list small")).to_contain_text("2 episodes")
            page.locator("#subscription-list").get_by_role("button", name="Unsubscribe", exact=True).click()
            expect(page.locator("#subscription-list")).to_contain_text("No subscriptions yet")
            page.locator('[data-close="subscriptions-dialog"]').click()
            expect(page.locator("#episodes .episode-card")).to_have_count(3)
            screenshot("desktop-library")

            note("Browser: verify the 390-pixel mobile library and review layout")
            page.set_viewport_size({"width": 390, "height": 844})
            wait_js(page, "() => document.documentElement.scrollWidth <= innerWidth + 1", label="mobile library without horizontal overflow")
            screenshot("mobile-library")
            page.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
            page.locator("#tab-cuts").click()
            wait_js(page, "() => { const dialog = document.querySelector('#episode-dialog'); return document.documentElement.scrollWidth <= innerWidth + 1 && dialog.scrollWidth <= dialog.clientWidth + 1; }", label="mobile review without horizontal overflow")
            screenshot("mobile-review")

            note("Browser: retain the original playhead when editing a cleaned recording")
            playback_audio = workspace / "Playback position regression.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                            "-i", "sine=frequency=330:sample_rate=16000:duration=24", "-c:a", "pcm_s16le", str(playback_audio)],
                           check=True, capture_output=True)
            uploaded = page.request.post("/api/audio", multipart={"file": {
                "name": playback_audio.name, "mimeType": "audio/wav", "buffer": playback_audio.read_bytes(),
            }})
            assert uploaded.status == 201, uploaded.text()
            playback_id = uploaded.json()["id"]
            imported = page.request.post(f"/api/episodes/{playback_id}/transcript", data={
                "duration": 24, "language": "en", "segments": [
                    {"id": 0, "start": 0, "end": 6, "text": "The beginning of the conversation."},
                    {"id": 1, "start": 6, "end": 14, "text": "A manually reviewed commercial passage."},
                    {"id": 2, "start": 14, "end": 24, "text": "The conversation continues after the commercial."},
                ],
            })
            assert imported.ok, imported.text()
            cuts = page.request.post(f"/api/episodes/{playback_id}/cuts", data={"cuts": [
                {"start": 6, "end": 14, "approved": True, "source": "manual", "reason": "Playback regression fixture"},
            ]})
            assert cuts.ok, cuts.text()
            rendered = page.request.post(f"/api/episodes/{playback_id}/render", data={})
            assert rendered.status == 202, rendered.text()
            wait_episode(page, playback_id, "ready", cleaned=True)
            quality_url = f"**/api/episodes/{playback_id}"

            def intercept_quality(route):
                response = route.fetch()
                detail = response.json()
                detail["transcript"]["quality"] = {
                    "version": 1, "requires_review": True,
                    "warning": "Original audio has a gap with no transcript text. It may be silence, music, or missed speech.",
                    "gaps": [{"start": 16, "end": 20, "duration": 4}],
                }
                detail["transcript"]["segments"][0]["asr_recovered"] = True
                route.fulfill(response=response, content_type="application/json", body=json.dumps(detail))

            page.route(quality_url, intercept_quality)
            page.goto(base, wait_until="networkidle")
            page.get_by_role("button", name="Open Playback position regression", exact=True).click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/cleaned'); }", label="playback regression cleaned audio")
            expect(page.locator("#transcript-quality-notice")).to_be_visible()
            page.locator("#transcript-version").select_option("original")
            expect(page.locator("#transcript-lines").get_by_text("Recovered speech - review audio", exact=True)).to_be_visible()
            page.locator("#listen-transcript-gap").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/original') && audio.currentTime >= 15 && audio.currentTime < 17; }", label="transcript gap preview on original timeline")
            page.locator("#audio-player").evaluate("audio => audio.pause()")
            page.locator("#play-cleaned").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/cleaned'); }", label="return to cleaned regression audio")
            page.unroute(quality_url, intercept_quality)
            page.locator("#audio-player").evaluate("audio => { audio.pause(); audio.currentTime = 9; }")
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return !audio.seeking && Math.abs(audio.currentTime - 9) < .05; }", label="cleaned nine-second playhead")
            expect(page.locator("#waveform-position")).to_have_text("0:17")
            page.locator("#tab-cuts").click()
            page.get_by_label("Cut 1 start in seconds", exact=True).fill("6.25")
            page.locator("#save-cuts").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/original') && Math.abs(audio.currentTime - 17) < .05; }", label="original seventeen-second playhead after editing")
            assert page.locator("#audio-player").evaluate("audio => audio.paused") is True
            assert page.locator("#audio-player").evaluate("audio => audio.playbackRate") == 1.5
            regeneration_requests = []

            def intercept_regeneration(route):
                regeneration_requests.append(route.request.post_data_json)
                route.fulfill(status=202, content_type="application/json", body='{"queued": true}')

            regeneration_url = f"**/api/episodes/{playback_id}/process"
            page.route(regeneration_url, intercept_regeneration)
            page.locator("#detail-retranscribe").click()
            wait_js(page, "() => !document.querySelector('#detail-retranscribe').disabled", label="regeneration action complete")
            assert regeneration_requests == [{"retranscribe": True}], regeneration_requests
            page.unroute(regeneration_url, intercept_regeneration)
            page.locator("#close-detail").click()
            wait_js(page, "async id => Math.abs((await (await fetch(`/api/episodes/${id}`)).json()).position - 17) < .05", playback_id, label="saved original position after editing")

            note("Browser: preserve detector evidence and distinguish manually adjusted boundaries")
            # Explicit fixtures exercise metadata persistence without calling a model.
            verification = [{"segment_id": 1, "parent_segment_id": 1,
                             "intent": {"label": "commercial", "confidence": .96, "reason": "Fixture intent evidence"},
                             "boundary": {"label": "commercial", "confidence": .95, "reason": "Fixture boundary evidence"}}]
            noisy = {"start": 4.440000000000003, "end": 19.619999999999997, "approved": False,
                     "source": "ai", "confidence": .95, "reason": "Floating point display fixture",
                     "requires_review": False, "label": "commercial", "policy_version": "smoke-fixture-v1",
                     "verification": verification}
            precise = dict(noisy, start=20.1234567890123, end=21.2345678901234, reason="Precise imported boundary")
            seeded = page.request.post(f"/api/episodes/{playback_id}/cuts", data={"cuts": [noisy, precise]})
            assert seeded.ok, seeded.text()
            page.goto(base, wait_until="networkidle")
            page.get_by_role("button", name="Open Playback position regression", exact=True).click()
            page.locator("#tab-cuts").click()
            expect(page.get_by_label("Cut 1 start in seconds", exact=True)).to_have_value("4.44")
            expect(page.get_by_label("Cut 1 end in seconds", exact=True)).to_have_value("19.62")
            expect(page.get_by_label("Cut 2 start in seconds", exact=True)).to_have_value("20.1234567890123")
            expect(page.get_by_label("Cut 2 end in seconds", exact=True)).to_have_value("21.2345678901234")
            page.locator("#select-all-cuts").click()
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            assert episode(page, playback_id)["cuts"] == [dict(noisy, approved=True), dict(precise, approved=True)]
            page.get_by_label("Cut 1 start in seconds", exact=True).fill("4.5")
            expect(page.locator("#cut-list .cut-row").nth(0).locator("input[type=checkbox]")).not_to_be_checked()
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            precision_cuts = episode(page, playback_id)["cuts"]
            assert precision_cuts[0]["start"] == 4.5 and precision_cuts[0]["end"] == noisy["end"]
            assert precision_cuts[0]["approved"] is False and precision_cuts[0]["requires_review"] is True
            assert precision_cuts[0]["detected_bounds"] == {"start": noisy["start"], "end": noisy["end"]}
            assert precision_cuts[0]["manual_adjustment"]["verification_scope"] == "original_detected_bounds"
            assert precision_cuts[0]["verification"] == verification
            assert precision_cuts[1] == dict(precise, approved=True)
            page.locator("#cut-selection-controls").scroll_into_view_if_needed()
            if artifacts:
                page.screenshot(path=str(artifacts / "mobile-boundary-precision.png"), full_page=False)

            detected = {
                "start": 6, "end": 14, "approved": False, "source": "ai", "confidence": .95,
                "reason": "Verified fixture", "requires_review": False, "label": "commercial",
                "policy_version": "smoke-fixture-v1", "sources": ["ai", "intent-verifier", "boundary-verifier"],
                "verification": verification,
            }
            recovered = {
                "start": 16, "end": 20, "approved": False, "source": "ai", "confidence": .94,
                "reason": "Provisional recovered fixture", "requires_review": True, "label": "uncertain",
                "policy_version": "smoke-fixture-v1", "verification": verification,
                "asr_recovered": True, "recovery_provenance": {"segment_ids": [2], "method": "no-vad-gap-recovery"},
            }
            seeded = page.request.post(f"/api/episodes/{playback_id}/cuts", data={"cuts": [detected, recovered]})
            assert seeded.ok, seeded.text()
            page.goto(base, wait_until="networkidle")
            page.get_by_role("button", name="Open Playback position regression", exact=True).click()
            page.locator("#tab-cuts").click()
            rows = page.locator("#cut-list .cut-row")
            expect(rows).to_have_count(2)
            expect(rows.nth(0).locator(".cut-review-status")).to_contain_text("Both model checks passed")
            expect(rows.nth(1).locator(".cut-review-status")).to_contain_text("Review required by detector")
            rows.nth(0).locator("input[type=checkbox]").check()
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST") as metadata_saved:
                page.locator("#save-cuts").click()
            assert metadata_saved.value.ok, metadata_saved.value.text()
            unchanged = episode(page, playback_id)["cuts"]
            assert unchanged == [dict(detected, approved=True), recovered], unchanged
            expect(page.locator("#save-cuts")).to_be_enabled()
            page.get_by_label("Cut 1 start in seconds", exact=True).fill("6.25")
            expect(rows.nth(0).locator("input[type=checkbox]")).not_to_be_checked()
            expect(rows.nth(0).locator(".cut-review-status")).to_contain_text("Manually adjusted")
            expect(rows.nth(0).locator(".cut-review-status")).to_contain_text("original 6.00 - 14.00 seconds")
            expect(rows.nth(0).locator(".cut-confidence")).to_have_text("Original Score 0.95")
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            adjusted = episode(page, playback_id)["cuts"][0]
            assert adjusted["requires_review"] is True and adjusted["approved"] is False
            assert adjusted["detected_bounds"] == {"start": 6, "end": 14}
            assert adjusted["manual_adjustment"] == {
                "kind": "boundary-edit", "verification_scope": "original_detected_bounds",
                "original_requires_review": False, "original_approved": True,
            }
            assert adjusted["verification"] == verification and adjusted["policy_version"] == detected["policy_version"]
            # Reload, edit again, and approve manually. The first detected bounds
            # and every unrelated cut's recovery provenance must remain intact.
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="Open Playback position regression", exact=True).click()
            page.locator("#tab-cuts").click()
            page.get_by_label("Cut 1 end in seconds", exact=True).fill("13.75")
            rows.nth(0).locator("input[type=checkbox]").check()
            expect(rows.nth(0).locator(".cut-review-status")).to_contain_text("selected by you")
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            final_cuts = episode(page, playback_id)["cuts"]
            assert final_cuts[0] == dict(adjusted, end=13.75, approved=True), final_cuts[0]
            assert final_cuts[1] == recovered, final_cuts[1]

            note("Browser: bulk selection preserves review metadata and exports only the final selection")
            expect(page.locator("#cut-selection-count")).to_have_text("1 of 2 selected")
            # Prepare a cleaned timeline using the current single approved cut.
            # Bulk edits must keep its original-time playhead when saving
            # invalidates that export, just as individual checkbox edits do.
            page.locator("#render-cuts").click()
            wait_episode(page, playback_id, "review", cleaned=True)
            page.locator("#play-cleaned").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/cleaned'); }", label="cleaned audio before bulk selection")
            page.locator("#audio-player").evaluate("audio => { audio.pause(); audio.currentTime = 10; }")
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return !audio.seeking && Math.abs(audio.currentTime - 10) < .05; }", label="cleaned playhead before bulk selection")
            page.locator("#select-all-cuts").click()
            expect(page.locator("#cut-selection-count")).to_have_text("2 of 2 selected")
            expect(page.locator("#select-all-cuts")).to_be_disabled()
            expect(rows.nth(0).locator("input[type=checkbox]")).to_be_checked()
            expect(rows.nth(1).locator("input[type=checkbox]")).to_be_checked()
            expect(rows.nth(0).locator(".cut-review-status")).to_contain_text("Manually adjusted")
            expect(rows.nth(1).locator(".cut-review-status")).to_contain_text("Review required by detector")
            assert episode(page, playback_id)["cuts"] == final_cuts, "Bulk selection must remain unsaved until requested"
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            assert episode(page, playback_id)["cuts"] == [dict(cut, approved=True) for cut in final_cuts]
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/original') && Math.abs(audio.currentTime - 17.5) < .05; }", label="original playhead after saving bulk selection")
            assert page.locator("#audio-player").evaluate("audio => audio.paused") is True
            assert page.locator("#audio-player").evaluate("audio => audio.playbackRate") == 1.5

            page.locator("#clear-cut-selection").click()
            expect(page.locator("#cut-selection-count")).to_have_text("0 of 2 selected")
            expect(page.locator("#clear-cut-selection")).to_be_disabled()
            expect(rows.nth(0).locator("input[type=checkbox]")).not_to_be_checked()
            expect(rows.nth(1).locator("input[type=checkbox]")).not_to_be_checked()
            with page.expect_response(lambda response: response.url.endswith(f"/{playback_id}/cuts") and response.request.method == "POST"):
                page.locator("#save-cuts").click()
            assert episode(page, playback_id)["cuts"] == [dict(cut, approved=False) for cut in final_cuts]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="Open Playback position regression", exact=True).click()
            page.locator("#tab-cuts").click()
            expect(page.locator("#cut-selection-count")).to_have_text("0 of 2 selected")
            expect(rows.nth(0).locator("input[type=checkbox]")).not_to_be_checked()
            expect(rows.nth(1).locator("input[type=checkbox]")).not_to_be_checked()

            # A per-cut choice after selecting all controls the actual export.
            page.locator("#select-all-cuts").click()
            rows.nth(0).locator("input[type=checkbox]").uncheck()
            expect(page.locator("#cut-selection-count")).to_have_text("1 of 2 selected")
            expect(page.locator("#cut-summary")).to_contain_text("1 cut selected · 0:04 to remove")
            page.locator("#render-cuts").click()
            wait_episode(page, playback_id, "review", cleaned=True)
            exported = episode(page, playback_id)
            assert exported["cuts"] == [dict(final_cuts[0], approved=False), dict(final_cuts[1], approved=True)]
            assert abs(exported["removed_seconds"] - 4) < .01, exported["removed_seconds"]
            assert abs(exported["cleaned_duration"] - 20) < .15, exported["cleaned_duration"]
            page.locator("#play-cleaned").click()
            wait_js(page, "() => { const audio = document.querySelector('#audio-player'); return audio.readyState >= 2 && new URL(audio.currentSrc).pathname.endsWith('/cleaned') && Math.abs(audio.duration - 20) < .15; }", label="bulk selection export decoding")
            assert page.locator("#episode-dialog").evaluate("node => node.scrollWidth <= node.clientWidth"), "Bulk controls overflow the mobile dialog"
            page.locator("#cut-selection-controls").scroll_into_view_if_needed()
            if artifacts:
                page.screenshot(path=str(artifacts / "mobile-bulk-review.png"), full_page=False)
            assert not errors, "Browser errors:\n" + "\n".join(errors)
            note("PASS: gallery upload, review, real audio exports/playback, revision recovery, downloads, subscriptions, and desktop/mobile layouts")
        except Exception:
            screenshot("failure")
            if errors:
                note("Browser errors:\n" + "\n".join(errors))
            raise
        finally:
            context.close()
            browser.close()


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chromium", default=shutil.which("chromium"), help="Chromium executable; defaults to system chromium or Playwright's installed browser")
    parser.add_argument("--artifacts", type=Path, help="Save desktop/mobile screenshots and app logs in this directory")
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("FFmpeg and ffprobe must be installed")
    artifacts = args.artifacts.expanduser().resolve() if args.artifacts else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="castwell-browser-") as directory:
        workspace = Path(directory)
        audio = workspace / f"{TITLE}.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=16000:duration=12", "-c:a", "pcm_s16le", str(audio)],
                       check=True, capture_output=True)
        transcript_file = workspace / "import-transcript.json"
        transcript_file.write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
        with fixture_server(audio) as feed, app_server(workspace, artifacts) as base:
            run_browser(base, feed, audio, transcript_file, workspace, args.chromium, artifacts)
    note(f"Browser smoke completed in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
