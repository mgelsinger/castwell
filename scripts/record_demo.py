#!/usr/bin/env python3
"""Capture the real gallery with synthetic fixtures, without downloading models.

Requires the project development dependencies, FFmpeg, and a Playwright browser.
Run from the checkout: python scripts/record_demo.py --chromium /path/to/chromium
All library data, generated audio, and browser recordings are temporary.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smoke_browser import app_server, wait_episode, wait_js
from castwell.library import Library


TRANSCRIPT = {"language": "en", "duration": 24.0, "segments": [
    {"id": 0, "start": 0.0, "end": 6.0, "text": "Today we explore the science of the night sky, from our own backyard."},
    {"id": 1, "start": 6.0, "end": 10.0, "text": "This episode is sponsored by Example Coffee."},
    {"id": 2, "start": 10.0, "end": 14.0, "text": "Use our code STARS for a free trial."},
    {"id": 3, "start": 14.0, "end": 24.0, "text": "Now back to the show. Early astronomers carefully observed the planets and mapped their journeys."},
]}
TITLE = "A night under the stars"


def synthetic_audio(path):
    """Make a deliberately non-speech, varying waveform for the local demo."""
    sample_rate = 16000
    samples = bytearray()
    for index in range(sample_rate * 24):
        moment = index / sample_rate
        amplitude = 0.07 + 0.15 * abs(math.sin(moment * 2.7)) + 0.08 * abs(math.sin(moment * 6.3))
        frequency = 330 if moment < 6 or moment >= 14 else 550
        sample = amplitude * (math.sin(2 * math.pi * frequency * moment) + 0.2 * math.sin(2 * math.pi * 110 * moment))
        samples.extend(struct.pack("<h", round(sample * 32767)))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples)


def seed(workspace):
    library = Library(workspace / "library")
    library.update_settings({"detector": "heuristic", "review_only": True})
    titles = [TITLE, "The art of paying attention", "Small ideas, big discoveries", "Walking the quiet city"]
    podcasts = ["Night Sky / Demo", "Slow Notes / Demo", "Curious Minds / Demo", "Field Recordings / Demo"]
    for number, (title, podcast) in enumerate(zip(titles, podcasts)):
        identifier = f"demo-{number}"
        library.import_entries([{
            "id": identifier, "title": title, "podcast": podcast,
            "description": "Synthetic demo: generated tone audio and an imported sample transcript. No speech model was used.",
            "published": f"2026-01-{24-number:02d}T10:00:00Z", "duration": 24,
            "media_url": "", "image": "",
        }])
        audio = library.directory(identifier) / "original.wav"
        synthetic_audio(audio)
        library.update(identifier, audio=str(audio), transcript=TRANSCRIPT, status="available", duration=24)
    return "demo-0"


def run_capture(base, episode_id, workspace, chromium, destination):
    from playwright.sync_api import sync_playwright, expect

    errors = []
    scenes = []
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
        if chromium:
            options["executable_path"] = chromium
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900},
                                      record_video_dir=str(workspace / "video"),
                                      record_video_size={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(base, wait_until="networkidle")
        response = context.request.post(f"/api/episodes/{episode_id}/process", data={})
        assert response.status == 202, response.text()
        wait_episode(page, episode_id, "review", cleaned=False)
        page.goto(base, wait_until="networkidle")
        expect(page.locator("#episodes .episode-card")).to_have_count(4)
        started = time.monotonic()

        def scene(label, duration):
            scenes.append((round(time.monotonic() - started, 2), label))
            print(label, flush=True)
            page.wait_for_timeout(duration * 1000)

        scene("Your listening library", 2.7)
        page.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
        expect(page.locator("#episode-dialog")).to_be_visible()
        wait_js(page, "() => document.querySelector('#audio-player').readyState >= 2", label="original audio")
        page.locator("#playback-speed").select_option("1")
        page.locator("#audio-player").evaluate("audio => audio.play()")
        scene("Listen to the original", 2.8)
        page.locator("#audio-player").evaluate("audio => audio.pause()")
        page.locator("#episode-dialog").evaluate("dialog => dialog.scrollTo({top: 440, behavior: 'smooth'})")
        page.wait_for_timeout(500)
        page.locator("#transcript-search").fill("sponsored")
        scene("Search the timestamped transcript", 2.6)
        page.locator("#transcript-search").fill("")
        page.locator("#tab-cuts").click()
        expect(page.locator("#cut-list .cut-row")).to_have_count(1)
        page.locator("#episode-dialog").evaluate("dialog => dialog.scrollTo({top: 410, behavior: 'smooth'})")
        scene("Review the suggested ad boundaries", 2.5)
        expect(page.get_by_label("Cut 1 start in seconds", exact=True)).to_have_value("6")
        expect(page.get_by_label("Cut 1 end in seconds", exact=True)).to_have_value("14")
        page.locator("#cut-list input[type=checkbox]").check()
        scene("Choose which cuts to remove", 1.8)
        page.locator("#render-cuts").click()
        wait_episode(page, episode_id, "ready", cleaned=True)
        scene("Export a separate listening copy", 1.6)
        page.locator("#episode-dialog").evaluate("dialog => dialog.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(600)
        page.locator("#play-cleaned").click()
        wait_js(page, "() => { const a = document.querySelector('#audio-player'); return a.readyState >= 2 && new URL(a.currentSrc).pathname.endsWith('/cleaned'); }", label="cleaned audio")
        duration = page.locator("#audio-player").evaluate("audio => audio.duration")
        assert 15.9 < duration < 16.2, duration
        page.locator("#audio-player").evaluate("audio => { audio.currentTime = 3; return audio.play(); }")
        scene("Switch to cleaned audio: 24 seconds to 16", 3.0)
        page.locator("#audio-player").evaluate("audio => audio.pause()")
        page.locator("#tab-transcript").click()
        page.locator("#transcript-version").select_option("cleaned")
        expect(page.locator("#transcript-lines .transcript-line")).to_have_count(2)
        page.locator("#episode-dialog").evaluate("dialog => dialog.scrollTo({top: 380, behavior: 'smooth'})")
        scene("Keep a matching transcript on the cleaned timeline", 3.0)
        page.locator("#episode-dialog").evaluate("dialog => dialog.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(450)
        page.locator("#close-detail").click()
        scene("Keep the conversation. Skip the interruptions.", 2.0)
        elapsed = time.monotonic() - started
        video = page.video
        context.close()
        raw_video = Path(video.path())

        still_context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 1260})
        still = still_context.new_page()
        still.goto(base, wait_until="networkidle")
        still.get_by_role("button", name=f"Open {TITLE}", exact=True).click()
        still.locator("#tab-cuts").click()
        still.locator("#play-original").click()
        wait_js(still, "() => document.querySelector('#audio-player').readyState >= 2", label="screenshot waveform")
        still.locator("#playback-speed").select_option("1")
        panel_height = still.locator("#episode-dialog").evaluate("dialog => dialog.scrollHeight")
        still.set_viewport_size({"width": 1440, "height": panel_height + 20})
        still.locator("#episode-dialog").evaluate("dialog => dialog.scrollTop = 0")
        still.wait_for_timeout(500)
        still.locator("#episode-dialog").screenshot(path=str(destination / "images" / "ad-review.png"))
        still_context.close()
        browser.close()
        assert not errors, "Browser errors: " + "\n".join(errors)
    return raw_video, elapsed, scenes


def encode(raw, elapsed, scenes, destination, workspace):
    # Browser recording begins before navigation. Keep the final tour duration.
    total = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(raw)], text=True))
    start = max(0, total - elapsed - 0.1)
    filters = ["scale=1280:800:flags=lanczos", "pad=1280:848:0:0:color=0x10141d"]
    # Font files are copied to a temporary relative path for portable FFmpeg quoting.
    font_candidates = [Path("C:/Windows/Fonts/segoeui.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    font_filter = ""
    if font:
        shutil.copyfile(font, workspace / "caption-font.ttf")
        font_filter = "fontfile=caption-font.ttf:"
    for index, (at, label) in enumerate(scenes):
        (workspace / f"scene-{index}.txt").write_text(label, encoding="utf-8")
        end = scenes[index + 1][0] if index + 1 < len(scenes) else elapsed + 1
        filters.append(f"drawtext={font_filter}textfile=scene-{index}.txt:fontcolor=0xf2d4bd:fontsize=17:x=24:y=815:enable='between(t,{at},{end})'")
    (workspace / "demo-label.txt").write_text("LOCAL DEMO / synthetic audio + imported transcript", encoding="utf-8")
    filters.append(f"drawtext={font_filter}textfile=demo-label.txt:fontcolor=0x99a6b8:fontsize=12:x=w-tw-24:y=817")
    mp4 = destination / "media" / "castwell-tour.mp4"
    gif = destination / "media" / "castwell-tour.gif"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(raw), "-t", str(elapsed), "-vf", ",".join(filters), "-r", "24", "-an", "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], cwd=workspace, check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4), "-filter_complex", "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3", "-loop", "0", str(gif)], check=True)
    assert gif.stat().st_size < 10 * 1024 * 1024, "GIF exceeds 10 MiB"
    for output in (mp4, gif, destination / "images" / "ad-review.png"):
        print(f"Created {output} ({output.stat().st_size:,} bytes)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chromium", default=shutil.which("chromium"), help="Use an existing Chromium executable")
    parser.add_argument("--output", type=Path, default=ROOT / "docs", help="Directory containing images/ and media/")
    args = parser.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            parser.error(f"{tool} must be installed")
    destination = args.output.resolve()
    for folder in ("images", "media"):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="castwell-demo-") as temporary:
        workspace = Path(temporary)
        episode_id = seed(workspace)
        with app_server(workspace, None) as base:
            raw, elapsed, scenes = run_capture(base, episode_id, workspace, args.chromium, destination)
        encode(raw, elapsed, scenes, destination, workspace)


if __name__ == "__main__":
    main()
