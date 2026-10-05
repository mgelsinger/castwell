"""End-to-end library workflows using local RSS and real, short WAV audio."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

from castwell.app import create_app


TRANSCRIPT = {"language": "en", "duration": 6.0, "segments": [
    {"id": 0, "start": 0.0, "end": 2.0, "text": "This episode explores the science of sound."},
    {"id": 1, "start": 2.0, "end": 4.0, "text": "This episode is sponsored by Example. Use our code SCIENCE for a free trial."},
    {"id": 2, "start": 4.0, "end": 6.0, "text": "Now back to the show. Sound travels through matter."},
]}

ENVIRONMENT = {
    "CASTWELL_TRANSCRIPTION_MODEL": "", "CASTWELL_MODEL_CACHE": "", "CASTWELL_AI_BASE_URL": "",
    "CASTWELL_AI_MODEL": "", "CASTWELL_AI_KEY": "",
}


class SubscriptionFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/feed.xml":
            root = ET.Element("rss", version="2.0")
            channel = ET.SubElement(root, "channel")
            ET.SubElement(channel, "title").text = "Science & sound"
            ET.SubElement(channel, "description").text = "A local fixture subscription"
            for number in range(self.server.episode_count):
                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "title").text = f"Wave experiment {number + 1} & sound"
                ET.SubElement(item, "guid").text = f"experiment-{number}"
                ET.SubElement(item, "description").text = "Experiments with audio & waves."
                ET.SubElement(item, "enclosure", type="audio/wav", url=self.server.base + f"/audio.wav?media_secret=media-private-{number}")
            body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            content_type = "application/rss+xml"
        elif path == "/audio.wav":
            body = self.server.audio
            content_type = "audio/wav"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required for workflow integration tests")
class WorkflowExtensionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.audio = []
        for frequency in (440, 880):
            path = Path(cls.fixtures.name) / f"{frequency}.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=16000:duration=6",
                "-c:a", "pcm_s16le", str(path),
            ], check=True, capture_output=True)
            cls.audio.append(path.read_bytes())
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SubscriptionFixture)
        cls.server.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.audio = cls.audio[0]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.fixtures.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {**ENVIRONMENT, 'CASTWELL_ALLOWED_HOSTS': 'testserver'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = create_app(self.root)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.server.episode_count = 1

    def upload(self, filename="Wave study.wav", variant=0):
        response = self.client.post("/api/audio", files={"file": (filename, self.audio[variant], "audio/wav")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait_job(self, episode_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.app.state.jobs.lock:
                pending = episode_id in self.app.state.jobs.pending
            if not pending:
                episode = self.client.get(f"/api/episodes/{episode_id}").json()
                self.assertNotEqual(episode["status"], "error", episode.get("error"))
                return episode
            time.sleep(0.01)
        self.fail("The fixture episode did not finish processing")

    def import_transcript(self, episode_id):
        response = self.client.post(f"/api/episodes/{episode_id}/transcript", json=TRANSCRIPT)
        self.assertEqual(response.status_code, 200, response.text)

    def import_feed(self, private=False):
        url = self.server.base + "/feed.xml"
        if private:
            url = url.replace("http://", "http://private-user:private-password@") + "?feed_secret=subscription-private"
        response = self.client.post("/api/feeds", json={"url": url})
        self.assertEqual(response.status_code, 200, response.text)
        listing = self.client.get("/api/episodes").json()["episodes"]
        return url, next(item["id"] for item in listing if item["podcast"] == "Science & sound")

    def test_uploaded_audio_is_playable_and_duplicate_upload_preserves_review_state(self):
        first = self.upload("A & B <science>.wav")
        episode_id = first["id"]
        self.assertEqual(first["source"], "local")
        self.assertEqual(first["duration"], 6)
        self.assertTrue(first["has_audio"])
        self.assertEqual(first["title"], "A & B <science>")
        self.assertEqual(self.client.get(f"/media/{episode_id}/original").content, self.audio[0])
        self.client.patch(f"/api/episodes/{episode_id}/state", json={"favorite": True, "position": 3.5})
        self.import_transcript(episode_id)
        duplicate = self.upload("Renamed recording.wav")
        self.assertEqual(duplicate["id"], episode_id)
        self.assertEqual(duplicate["title"], first["title"])
        self.assertTrue(duplicate["favorite"])
        self.assertEqual(duplicate["position"], 3.5)
        self.assertTrue(duplicate["has_transcript"])
        self.assertEqual(len(self.client.get("/api/episodes").json()["episodes"]), 1)
        self.assertEqual(list((self.root / ".uploads").iterdir()), [])

    def test_invalid_empty_and_cross_origin_uploads_leave_no_episode_or_temporary_file(self):
        invalid = [("not-audio.txt", self.audio[0], "text/plain"), ("empty.wav", b"", "audio/wav"),
                   ("broken.wav", b"not a valid wave recording", "audio/wav")]
        for filename, content, content_type in invalid:
            with self.subTest(filename=filename):
                response = self.client.post("/api/audio", files={"file": (filename, content, content_type)})
                self.assertEqual(response.status_code, 400, response.text)
        for headers in ({"Origin": "https://elsewhere.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                response = self.client.post("/api/audio", files={"file": ("valid.wav", self.audio[0], "audio/wav")}, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get("/api/episodes").json()["episodes"], [])
        self.assertFalse(any(path.is_file() for path in (self.root / ".uploads").glob("*")))
        self.assertFalse((self.root / "episodes").exists())

    def test_episode_preferences_and_clamped_playback_survive_restart(self):
        episode_id = self.upload()["id"]
        response = self.client.patch(f"/api/episodes/{episode_id}/state", json={
            "favorite": True, "archived": True, "played": True, "position": 500,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["position"], 6)
        with TestClient(create_app(self.root)) as reopened:
            saved = reopened.get(f"/api/episodes/{episode_id}").json()
            for field in ("favorite", "archived", "played"):
                self.assertTrue(saved[field])
            self.assertEqual(saved["position"], 6)
            self.assertEqual(reopened.patch(f"/api/episodes/{episode_id}/state", json={"position": -1}).status_code, 422)
            self.assertEqual(reopened.get(f"/api/episodes/{episode_id}").json()["position"], 6)

    def test_waveform_is_derived_from_real_audio_and_cached(self):
        episode_id = self.upload()["id"]
        response = self.client.get(f"/api/episodes/{episode_id}/waveform")
        self.assertEqual(response.status_code, 200, response.text)
        waveform = response.json()
        self.assertEqual(waveform["duration"], 6)
        self.assertEqual(len(waveform["peaks"]), 700)
        self.assertTrue(all(0 <= peak <= 1 for peak in waveform["peaks"]))
        self.assertGreater(max(waveform["peaks"]), 0.1)
        self.assertEqual(self.app.state.library.get(episode_id)["waveform"], waveform)
        with patch("castwell.processing.waveform", side_effect=AssertionError("Existing waveform must be reused")):
            self.assertEqual(self.client.get(f"/api/episodes/{episode_id}/waveform").json(), waveform)
        _, remote_id = self.import_feed()
        self.assertEqual(self.client.get(f"/api/episodes/{remote_id}/waveform").status_code, 404)

    def test_settings_api_validates_preferences_and_protects_environment_credentials(self):
        result = self.client.patch("/api/settings", json={"review_only": True, "language": "fr", "auto_approve_threshold": 0.97})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["review_only"])
        self.assertEqual(self.client.patch("/api/settings", json={"auto_approve_threshold": 3}).status_code, 400)
        self.assertEqual(self.client.patch("/api/settings", json={"ai_base_url": "https://provider.example?secret=private-key"}).status_code, 400)
        response = self.client.patch("/api/settings", json={"ai_key": "private-key"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-key", response.text)
        with patch.dict(os.environ, {"CASTWELL_AI_KEY": "private-key", "CASTWELL_AI_MODEL": "environment-model"}):
            response = self.client.get("/api/settings")
            self.assertTrue(response.json()["key_configured"])
            self.assertIn("ai_model", response.json()["env_overrides"])
            self.assertNotIn("private-key", response.text)
            response = self.client.patch("/api/settings", json={"ai_model": "different-model"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("CASTWELL_AI_MODEL", response.json()["detail"])
        with TestClient(create_app(self.root)) as reopened:
            settings = reopened.get("/api/settings").json()
            self.assertEqual(settings["language"], "fr")
            self.assertEqual(settings["auto_approve_threshold"], 0.97)
            self.assertNotIn("private-key", json.dumps(settings))

    def test_opml_import_deduplicates_nested_subscriptions_and_export_omits_private_urls(self):
        public = self.server.base + "/feed.xml"
        private = self.server.base.replace("http://", "http://private-user:private-password@") + "/feed.xml?feed_secret=subscription-private"
        root = ET.Element("opml", version="2.0")
        body = ET.SubElement(root, "body")
        folder = ET.SubElement(body, "outline", text="Science")
        for url in (public, public, private):
            ET.SubElement(folder, "outline", type="rss", xmlUrl=url)
        response = self.client.post("/api/feeds/opml", json={"xml": ET.tostring(root, encoding="unicode")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"imported": 2, "added": 2, "errors": []})
        feeds = self.client.get("/api/feeds")
        self.assertEqual(len(feeds.json()["feeds"]), 2)
        for secret in ("private-user", "private-password", "subscription-private", "feed_secret"):
            self.assertNotIn(secret, feeds.text)
        exported = self.client.get("/api/feeds/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.headers["X-Castwell-Private-Feeds-Omitted"], "1")
        exported_root = ET.fromstring(exported.content)
        outlines = list(exported_root.iter("outline"))
        self.assertEqual([node.get("xmlUrl") for node in outlines], [public])
        self.assertEqual(outlines[0].get("title"), "Science & sound")
        self.assertNotIn("subscription-private", exported.text)
        self.assertIn("&amp;", exported.text)

    def test_opml_rejects_document_entities_without_making_requests(self):
        document = '<!DOCTYPE opml [<!ENTITY private SYSTEM "file:///etc/passwd">]><opml><body>&private;</body></opml>'
        with patch("castwell.app.read_feed", side_effect=AssertionError("Unsafe XML must be rejected before feed access")):
            response = self.client.post("/api/feeds/opml", json={"xml": document})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/feeds").json()["feeds"], [])

    def test_refresh_adds_new_episodes_and_unsubscribe_preserves_library_without_resurrection(self):
        _, episode_id = self.import_feed()
        feed_id = self.client.get("/api/feeds").json()["feeds"][0]["id"]
        self.client.patch(f"/api/episodes/{episode_id}/state", json={"favorite": True})
        self.server.episode_count = 2
        refreshed = self.client.post(f"/api/feeds/{feed_id}/refresh")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json(), {"added": 1, "total": 2})
        self.assertTrue(self.client.get(f"/api/episodes/{episode_id}").json()["favorite"])
        self.assertIsNotNone(self.client.get("/api/feeds").json()["feeds"][0]["last_checked"])
        removed = self.client.delete(f"/api/feeds/{feed_id}")
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["unsubscribed"])
        self.assertEqual(len(self.client.get("/api/episodes").json()["episodes"]), 2)
        self.assertEqual(self.client.get("/api/feeds").json()["feeds"], [])
        self.assertEqual(self.client.post(f"/api/feeds/{feed_id}/refresh").status_code, 404)
        with TestClient(create_app(self.root)) as reopened:
            self.assertEqual(reopened.get("/api/feeds").json()["feeds"], [])
            self.assertEqual(len(reopened.get("/api/episodes").json()["episodes"]), 2)
            self.assertTrue(reopened.get(f"/api/episodes/{episode_id}").json()["favorite"])

    def test_cut_history_restore_invalidates_export_and_preserves_undo(self):
        episode_id = self.upload()["id"]
        self.import_transcript(episode_id)
        endpoint = f"/api/episodes/{episode_id}"
        first = [{"start": 2, "end": 4, "approved": True, "reason": "Remove commercial", "source": "manual"}]
        second = [{"start": 2.5, "end": 3.5, "approved": True, "reason": "Tighter boundaries", "source": "manual"}]
        self.assertEqual(self.client.post(endpoint + "/cuts", json={"cuts": first}).status_code, 200)
        saved_first = self.client.get(endpoint).json()["cuts"]
        self.assertEqual(self.client.post(endpoint + "/cuts", json={"cuts": second}).status_code, 200)
        self.assertEqual(self.client.post(endpoint + "/render").status_code, 202)
        rendered = self.wait_job(episode_id)
        self.assertTrue(rendered["has_cleaned"])
        history = self.client.get(endpoint + "/revisions").json()["revisions"]
        revision = next(item for item in history if item["cuts"] == saved_first)
        restored = self.client.post(endpoint + f"/revisions/{revision['id']}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["cuts"], saved_first)
        self.assertFalse(restored.json()["has_cleaned"])
        self.assertEqual(restored.json()["removed_seconds"], 0)
        self.assertTrue(restored.json()["analysis_done"])
        self.assertEqual(self.client.get(f"/media/{episode_id}/cleaned").status_code, 404)
        latest = self.client.get(endpoint + "/revisions").json()["revisions"][0]
        self.assertEqual(latest["cuts"], rendered["cuts"])
        self.assertIn("restoring", latest["reason"])
        other_id = self.upload(variant=1)["id"]
        self.assertEqual(self.client.post(f"/api/episodes/{other_id}/revisions/{revision['id']}/restore").status_code, 404)

    def test_empty_saved_cuts_remain_a_reviewed_decision_when_processing_again(self):
        episode_id = self.upload()["id"]
        self.import_transcript(episode_id)
        endpoint = f"/api/episodes/{episode_id}"
        saved = self.client.post(endpoint + "/cuts", json={"cuts": []})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["analysis_done"])
        with patch("castwell.processing.transcribe", side_effect=AssertionError("Imported transcript must be reused")), \
                patch("castwell.processing.detect_ads", side_effect=AssertionError("Empty reviewed decisions must be retained")):
            self.assertEqual(self.client.post(endpoint + "/process", json={}).status_code, 202)
            result = self.wait_job(episode_id)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["cuts"], [])
        self.assertTrue(result["analysis_done"])
        self.assertFalse(result["has_cleaned"])
        self.assertEqual(self.client.get(f"/media/{episode_id}/original").content, self.audio[0])

    def test_decision_export_includes_editable_data_without_subscription_credentials(self):
        _, episode_id = self.import_feed(private=True)
        self.assertEqual(self.client.post(f"/api/episodes/{episode_id}/process", json={"download_only": True}).status_code, 202)
        self.wait_job(episode_id)
        self.import_transcript(episode_id)
        cuts = [{"start": 2, "end": 4, "approved": True, "reason": "A commercial read"}]
        self.client.post(f"/api/episodes/{episode_id}/cuts", json={"cuts": cuts})
        response = self.client.get(f"/api/episodes/{episode_id}/decisions")
        self.assertEqual(response.status_code, 200)
        export = response.json()
        self.assertEqual(export["format"], "castwell-decisions")
        self.assertEqual(export["timeline"], "original")
        self.assertEqual(export["episode"]["id"], episode_id)
        self.assertEqual(export["cuts"][0]["start"], 2)
        self.assertEqual(export["transcript"]["segments"], TRANSCRIPT["segments"])
        self.assertIn("-decisions.json", response.headers["content-disposition"])
        for secret in ("private-user", "private-password", "subscription-private", "media-private", "media_url", "feed_url"):
            self.assertNotIn(secret, response.text)

    def test_rss_export_includes_only_prepared_visible_audio_and_escapes_metadata(self):
        prepared_id = self.upload("A & B <science>.wav")["id"]
        self.import_transcript(prepared_id)
        endpoint = f"/api/episodes/{prepared_id}"
        self.client.post(endpoint + "/cuts", json={"cuts": []})
        self.client.post(endpoint + "/render")
        self.wait_job(prepared_id)
        unprocessed_id = self.upload("Unprocessed.wav", variant=1)["id"]
        self.import_feed()
        exported = self.client.get("/api/export/feed")
        self.assertEqual(exported.status_code, 200)
        root = ET.fromstring(exported.content)
        items = root.findall("channel/item")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].findtext("guid"), prepared_id)
        self.assertEqual(items[0].findtext("title"), "A & B <science>")
        self.assertEqual(items[0].find("enclosure").get("url"), f"http://testserver/media/{prepared_id}/original")
        self.assertEqual(int(items[0].find("enclosure").get("length")), len(self.audio[0]))
        self.assertIn("&amp;", exported.text)
        self.assertIn("&lt;science&gt;", exported.text)
        self.assertNotIn(unprocessed_id, exported.text)
        self.client.post(endpoint + "/cuts", json={"cuts": [{"start": 2, "end": 4, "approved": True}]})
        self.client.post(endpoint + "/render")
        self.wait_job(prepared_id)
        item = ET.fromstring(self.client.get("/api/export/feed").content).find("channel/item")
        self.assertEqual(item.find("enclosure").get("url"), f"http://testserver/media/{prepared_id}/cleaned")
        self.client.patch(endpoint + "/state", json={"archived": True})
        self.assertEqual(ET.fromstring(self.client.get("/api/export/feed").content).findall("channel/item"), [])

    def test_model_preparation_reports_running_then_ready_and_deduplicates_requests(self):
        entered, release = threading.Event(), threading.Event()

        def prepare(settings, progress=None):
            if progress:
                progress("Preparing a fixture model")
            entered.set()
            if not release.wait(5):
                raise AssertionError("Fixture model preparation was not released")
            return {"ok": True, "model": settings["transcription_model"]}

        self.assertEqual(self.client.get("/api/models/status").json()["status"], "idle")
        with patch("castwell.app.warm_transcription_model", side_effect=prepare) as warm:
            try:
                response = self.client.post("/api/models/prepare")
                self.assertEqual(response.status_code, 202)
                self.assertTrue(response.json()["started"])
                self.assertTrue(entered.wait(3))
                status = self.client.get("/api/models/status").json()
                self.assertEqual(status["status"], "running")
                self.assertEqual(status["progress"], "Preparing a fixture model")
                self.assertFalse(self.client.post("/api/models/prepare").json()["started"])
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = self.client.get("/api/models/status").json()
                if status["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(status["status"], "ready", status)
            self.assertIsNone(status["error"])
            warm.assert_called_once()

    def test_classifier_check_and_diagnostics_endpoints_use_safe_settings_contract(self):
        result = {"ok": True, "message": "Synthetic sample classified", "model": "fixture", "detected_ads": 1}
        with patch.object(self.app.state.settings, "test_classifier", return_value=result) as check:
            response = self.client.post("/api/settings/test-classifier")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), result)
            check.assert_called_once_with()
        with patch("requests.get", side_effect=AssertionError("Readiness must not contact providers")), \
                patch("requests.post", side_effect=AssertionError("Readiness must not contact providers")):
            response = self.client.get("/api/diagnostics")
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response.json()["ready"], bool)
            self.assertTrue(response.json()["checks"])
            self.assertTrue(all(item["status"] in {"ok", "warning", "error"} for item in response.json()["checks"]))

    def test_invalid_environment_does_not_leave_model_preparation_stuck_running(self):
        with patch.dict(os.environ, {"CASTWELL_AI_BASE_URL": "https://provider.example/v1?key=private-key"}):
            response = self.client.post("/api/models/prepare")
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("private-key", response.text)
        self.assertNotEqual(self.client.get("/api/models/status").json()["status"], "running")
        with patch("castwell.app.warm_transcription_model", return_value={"ok": True}) as prepare:
            response = self.client.post("/api/models/prepare")
            self.assertEqual(response.status_code, 202)
            self.assertTrue(response.json()["started"])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = self.client.get("/api/models/status").json()
                if status["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(status["status"], "ready")
            prepare.assert_called_once()


if __name__ == "__main__":
    unittest.main()
