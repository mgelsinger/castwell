"""Exercise the real HTTP/feed/download/edit pipeline without a model download."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from castwell.app import create_app


TRANSCRIPT = {
    "language": "en",
    "duration": 12.0,
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "Today we explore the history of the telescope."},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "This episode is sponsored by Acme."},
        {"id": 2, "start": 4.0, "end": 6.0, "text": "Use our code STARS for a free trial."},
        {"id": 3, "start": 6.0, "end": 8.0, "text": "Now back to the show."},
        {"id": 4, "start": 8.0, "end": 12.0, "text": "Early astronomers made careful observations of the night sky."},
    ],
}


class FixtureServer(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/feed.xml":
            # Relative artwork on an authenticated subscription must not reveal
            # HTTP credentials when metadata is returned to the browser.
            duration = parse_qs(urlsplit(self.path).query).get("duration", ["12"])[0]
            body = f'''<?xml version="1.0"?><rss version="2.0"
                xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
                <title>The Telescope</title><description>A fixture podcast</description>
                <itunes:image href="/cover.png"/><item><title>Astronomy</title>
                <guid>episode-one</guid><itunes:duration>{duration}</itunes:duration>
                <pubDate>Sat, 03 Oct 2026 10:00:00 GMT</pubDate>
                <enclosure url="{self.server.base}/audio.wav?token=media-secret" type="audio/wav"/>
                </item></channel></rss>'''.encode()
        elif path == "/audio.wav":
            body = self.server.audio
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required for audio integration tests")
class AppIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        audio = Path(cls.fixture_directory.name) / "episode.wav"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=12",
            "-c:a", "pcm_s16le", str(audio),
        ], check=True, capture_output=True)
        cls.audio_bytes = audio.read_bytes()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureServer)
        cls.server.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.audio = cls.audio_bytes
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.fixture_directory.cleanup()

    def setUp(self):
        hosts = patch.dict(os.environ, {'CASTWELL_ALLOWED_HOSTS': 'testserver'})
        hosts.start()
        self.addCleanup(hosts.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def import_feed(self, authenticated=False, duration=None):
        url = self.server.base + "/feed.xml"
        if authenticated:
            url = url.replace("http://", "http://premium-user:premium-secret@") + "?token=feed-secret"
        if duration is not None:
            url += ("&" if "?" in url else "?") + f"duration={duration}"
        response = self.client.post("/api/feeds", json={"url": url})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"added": 1, "total": 1})
        listing = self.client.get("/api/episodes").json()
        return listing["episodes"][0]["id"]

    def wait_for_job(self, episode_id, expected_status=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.app.state.jobs.lock:
                pending = episode_id in self.app.state.jobs.pending
            if not pending:
                episode = self.client.get(f"/api/episodes/{episode_id}").json()
                if expected_status is not None:
                    self.assertEqual(episode["status"], expected_status, episode.get("error"))
                else:
                    self.assertNotEqual(episode["status"], "error", episode.get("error"))
                return episode
            time.sleep(0.01)
        self.fail("Episode processing did not finish within 10 seconds")

    def download(self, episode_id):
        # Model execution is tested separately. This one stub lets the API fetch
        # and probe real audio before a real transcript import is exercised.
        with patch("castwell.processing.transcribe", return_value={"duration": 12.0, "segments": []}) as transcribe:
            response = self.client.post(f"/api/episodes/{episode_id}/process", json={"remove_ads": False})
            self.assertEqual(response.status_code, 202, response.text)
            episode = self.wait_for_job(episode_id)
            transcribe.assert_called_once()
        self.assertTrue(episode["has_audio"])
        self.assertEqual(episode["duration"], 12.0)
        return episode

    def process_imported_transcript(self, episode_id):
        response = self.client.post(f"/api/episodes/{episode_id}/transcript", json=TRANSCRIPT)
        self.assertEqual(response.status_code, 200, response.text)
        with patch("castwell.processing.transcribe", side_effect=AssertionError("Imported transcript should be reused")):
            response = self.client.post(f"/api/episodes/{episode_id}/process", json={"detector": "heuristic"})
            self.assertEqual(response.status_code, 202)
            return self.wait_for_job(episode_id)

    def test_import_deduplicates_and_never_returns_subscription_credentials(self):
        episode_id = self.import_feed(authenticated=True)
        url = self.server.base.replace("http://", "http://premium-user:premium-secret@") + "/feed.xml?token=feed-secret"
        self.assertEqual(self.client.post("/api/feeds", json={"url": url}).json(), {"added": 0, "total": 1})
        for endpoint in ("/api/episodes", f"/api/episodes/{episode_id}"):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            for secret in ("premium-user", "premium-secret", "feed-secret", "media-secret", "feed_url", "media_url"):
                self.assertNotIn(secret, response.text)
        self.assertEqual(self.client.get("/api/episodes").json()["stats"]["episodes"], 1)

    def test_download_import_detect_render_preserves_original_and_correct_duration(self):
        episode_id = self.import_feed()
        self.download(episode_id)
        episode = self.process_imported_transcript(episode_id)
        self.assertEqual(episode["status"], "ready")
        self.assertTrue(episode["has_cleaned"])
        self.assertEqual(episode["removed_seconds"], 4.0)
        self.assertEqual([(cut["start"], cut["end"], cut["approved"]) for cut in episode["cuts"]], [(2.0, 6.0, True)])
        record = self.app.state.library.get(episode_id)
        self.assertEqual(Path(record["audio"]).read_bytes(), self.audio_bytes)
        decoded = subprocess.run([
            "ffmpeg", "-v", "error", "-i", record["cleaned"], "-f", "s16le", "-c:a", "pcm_s16le", "-",
        ], check=True, capture_output=True).stdout
        # Decoded PCM accounts for MP3 encoder padding, unlike container duration.
        self.assertEqual(len(decoded) / (16000 * 2), 8.0)
        original = self.client.get(f"/media/{episode_id}/original")
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.content, self.audio_bytes)
        ranged = self.client.get(f"/media/{episode_id}/original", headers={"Range": "bytes=20-39"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, self.audio_bytes[20:40])
        self.assertEqual(ranged.headers["content-range"], f"bytes 20-39/{len(self.audio_bytes)}")

    def test_transcript_exports_use_the_correct_audio_timeline(self):
        episode_id = self.import_feed()
        self.download(episode_id)
        self.process_imported_transcript(episode_id)
        endpoint = f"/api/episodes/{episode_id}/transcript"
        original = self.client.get(endpoint, params={"format": "json"}).json()
        self.assertEqual(original["duration"], 12.0)
        self.assertEqual(len(original["segments"]), 5)
        cleaned = self.client.get(endpoint, params={"format": "json", "version": "cleaned"}).json()
        self.assertEqual(cleaned["duration"], 8.0)
        self.assertEqual([segment["start"] for segment in cleaned["segments"]], [0, 2, 4])
        self.assertNotIn("sponsored", json.dumps(cleaned))
        for format, marker in (("txt", "Early astronomers"), ("srt", "00:00:04,000 --> 00:00:08,000"), ("vtt", "WEBVTT")):
            with self.subTest(format=format):
                response = self.client.get(endpoint, params={"format": format, "version": "cleaned"})
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.text)
                self.assertNotIn("free trial", response.text)
                self.assertIn(f"-cleaned.{format}", response.headers["content-disposition"])

    def test_manual_changes_invalidate_stale_audio_and_cleaned_transcripts(self):
        episode_id = self.import_feed()
        self.download(episode_id)
        self.process_imported_transcript(episode_id)
        response = self.client.post(f"/api/episodes/{episode_id}/cuts", json={"cuts": [
            {"start": 3, "end": 5, "approved": True, "reason": "Adjusted by listener"},
        ]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_cleaned"])
        self.assertEqual(response.json()["removed_seconds"], 0)
        self.assertEqual(self.client.get(f"/media/{episode_id}/cleaned").status_code, 404)
        self.assertEqual(self.client.get(f"/api/episodes/{episode_id}/transcript?version=cleaned").status_code, 400)
        response = self.client.post(f"/api/episodes/{episode_id}/render")
        self.assertEqual(response.status_code, 202)
        episode = self.wait_for_job(episode_id)
        self.assertTrue(episode["has_cleaned"])
        self.assertEqual(episode["removed_seconds"], 2)
        response = self.client.post(f"/api/episodes/{episode_id}/transcript", json=TRANSCRIPT)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_cleaned"])
        self.assertEqual(response.json()["cuts"], [])

    def test_bad_ids_cuts_and_formats_fail_without_changing_the_library(self):
        self.assertEqual(self.client.get("/api/episodes/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/episodes/missing/process", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/episodes/missing/render").status_code, 404)
        episode_id = self.import_feed()
        self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/render").status_code, 400)
        self.assertEqual(self.client.get(f"/api/episodes/{episode_id}/transcript").status_code, 404)
        self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/process", json={"detector": "unknown"}).status_code, 400)
        for cut in ({"start": -1, "end": 2}, {"start": 1, "end": 13}, {"start": 4, "end": 3}, {"start": 1, "end": 2, "approved": "true"}):
            with self.subTest(cut=cut):
                response = self.client.post(f"/api/episodes/{episode_id}/cuts", json={"cuts": [cut]})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get(f"/api/episodes/{episode_id}").json()["cuts"], [])
        self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/transcript", json=TRANSCRIPT).status_code, 200)
        self.assertEqual(self.client.get(f"/api/episodes/{episode_id}/transcript?format=html").status_code, 400)
        self.assertEqual(self.client.get(f"/media/{episode_id}/unknown").status_code, 404)

    def test_retry_preserves_reviewed_cuts_until_explicit_redetection(self):
        episode_id = self.import_feed()
        self.download(episode_id)
        self.process_imported_transcript(episode_id)
        endpoint = f"/api/episodes/{episode_id}"
        self.assertEqual(self.client.post(endpoint + "/cuts", json={"cuts": [
            {"start": 3, "end": 5, "approved": True, "reason": "Reviewed boundary", "source": "manual"},
        ]}).status_code, 200)
        with patch("castwell.processing.detect_ads", side_effect=AssertionError("Retry must retain reviewed cuts")):
            self.assertEqual(self.client.post(endpoint + "/process", json={}).status_code, 202)
            episode = self.wait_for_job(episode_id)
        self.assertEqual(episode["removed_seconds"], 2)
        self.assertEqual(episode["cuts"][0]["reason"], "Reviewed boundary")
        self.assertEqual(self.client.post(endpoint + "/process", json={"redetect": True, "detector": "heuristic"}).status_code, 202)
        episode = self.wait_for_job(episode_id)
        self.assertEqual(episode["removed_seconds"], 4)
        self.assertEqual(episode["cuts"][0]["source"], "heuristic")

    def test_cross_origin_mutations_are_rejected(self):
        body = {"url": self.server.base + "/feed.xml"}
        self.assertEqual(self.client.post("/api/feeds", json=body, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/feeds", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.get("/api/episodes").json()["stats"]["episodes"], 0)
        self.assertEqual(self.client.post("/api/feeds", json=body, headers={"Origin": "http://testserver"}).status_code, 200)

    def test_imported_transcript_duration_is_reconciled_with_downloaded_audio(self):
        for remove_ads, advertised_duration in ((False, 30), (True, 31)):
            with self.subTest(remove_ads=remove_ads):
                episode_id = self.import_feed(duration=advertised_duration)
                imported = dict(TRANSCRIPT, duration=advertised_duration)
                response = self.client.post(f"/api/episodes/{episode_id}/transcript", json=imported)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["transcript"]["duration"], advertised_duration)
                with patch("castwell.processing.transcribe", side_effect=AssertionError("Imported transcript must be reused")):
                    response = self.client.post(f"/api/episodes/{episode_id}/process", json={"remove_ads": remove_ads, "detector": "heuristic"})
                    self.assertEqual(response.status_code, 202)
                    episode = self.wait_for_job(episode_id)
                self.assertEqual(episode["duration"], 12)
                self.assertEqual(episode["transcript"]["duration"], 12)
                exported = self.client.get(f"/api/episodes/{episode_id}/transcript?format=json").json()
                self.assertEqual(exported["duration"], 12)
                persisted = self.app.state.library.get(episode_id)
                self.assertEqual(persisted["transcript"]["duration"], 12)
                self.assertEqual(episode["has_cleaned"], remove_ads)

    def test_imported_timestamps_beyond_real_audio_fail_even_without_ad_removal(self):
        episode_id = self.import_feed(duration=30)
        imported = dict(TRANSCRIPT, duration=30, segments=[
            {"id": 0, "start": 0, "end": 14, "text": "The RSS estimate is longer than the real recording."},
        ])
        self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/transcript", json=imported).status_code, 200)
        with patch("castwell.processing.transcribe", side_effect=AssertionError("Imported transcript must be validated")):
            self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/process", json={"remove_ads": False}).status_code, 202)
            episode = self.wait_for_job(episode_id, expected_status="error")
        self.assertIn("within the audio duration", episode["error"])
        self.assertEqual(episode["duration"], 12)
        self.assertTrue(episode["has_audio"])
        self.assertFalse(episode["has_cleaned"])
        self.assertEqual(self.client.get(f"/media/{episode_id}/original").content, self.audio_bytes)

    def test_empty_transcript_requires_audio_review(self):
        episode_id = self.import_feed()
        self.download(episode_id)
        with patch("castwell.processing.transcribe", side_effect=AssertionError("Existing empty transcript must be reused")):
            self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/process", json={"detector": "heuristic"}).status_code, 202)
            episode = self.wait_for_job(episode_id, expected_status="review")
        self.assertFalse(episode["has_cleaned"])
        self.assertEqual(episode["cuts"], [])
        self.assertIn("No speech detected", episode["progress"])
        self.assertNotIn("No ads detected", episode["progress"])

    def test_batch_deduplicates_and_rejects_edits_during_processing(self):
        episode_id = self.import_feed()
        entered, release = threading.Event(), threading.Event()

        def transcribe(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release transcription")
            return TRANSCRIPT

        with patch("castwell.processing.transcribe", side_effect=transcribe) as mocked:
            try:
                response = self.client.post("/api/process", json={"ids": [episode_id, episode_id], "remove_ads": False})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["queued"], 1)
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post("/api/process", json={"ids": [episode_id], "remove_ads": False}).json()["queued"], 0)
                self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/cuts", json={"cuts": []}).status_code, 409)
                self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/transcript", json=TRANSCRIPT).status_code, 409)
            finally:
                release.set()
            self.wait_for_job(episode_id)
            mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
