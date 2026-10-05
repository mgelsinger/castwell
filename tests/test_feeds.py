import base64
import contextlib
import io
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from download_podcast import download_feed
from castwell.feeds import DownloadError, FeedError, download_media, read_feed
from castwell.processing import ProcessingCancelled


MEDIA = b"fixture podcast audio\x00\x01\x02"
RSS = b'''<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Test &amp; Talk</title><link>https://example.test</link>
<description>Test feed</description><itunes:image href="/cover.jpg"/>
<item><title>First: a &amp; b?</title><guid>stable-one</guid>
<pubDate>Sat, 03 Oct 2026 10:00:00 GMT</pubDate><itunes:duration>1:02:03</itunes:duration>
<description><![CDATA[<p>Readable <b>description</b></p><script>bad()</script>]]></description>
<enclosure url="/audio.mp3?signature=abc" type="audio/mpeg" length="25"/></item>
<item><title>No media or link</title></item>
<item><title>Empty enclosure</title><enclosure type="audio/mpeg"/></item>
<item><title>Unsafe media</title><enclosure url="file:///tmp/private.mp3" type="audio/mpeg"/></item>
<item><title>Second</title><guid>stable-two</guid><itunes:duration>30</itunes:duration>
<itunes:image href="javascript:alert(1)"/>
<enclosure url="/second.mp3" type="audio/mpeg"/></item>
</channel></rss>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Authorization")))
        if self.path == "/auth.xml":
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"user:secret").decode():
                self.send_error(401)
                return
        if self.path in ("/feed.xml", "/auth.xml"):
            content = RSS
        elif self.path == "/bad.xml":
            content = b"<rss><channel><item><title>truncated"
        elif self.path == "/html":
            content = b"<html><body>Not a podcast</body></html>"
        elif self.path == "/empty.xml":
            content = b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'
        elif self.path == "/truncated.mp3":
            self.send_response(200)
            self.send_header("Content-Length", "99999")
            self.end_headers()
            self.wfile.write(MEDIA)
            self.close_connection = True
            return
        elif self.path == "/empty.mp3":
            content = b""
        elif self.path.startswith("/audio.mp3") or self.path == "/second.mp3":
            content = MEDIA
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass


class FeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_normalizes_metadata_and_skips_missing_enclosures(self):
        episodes = read_feed(self.base + "/feed.xml")
        self.assertEqual(len(episodes), 2)
        first = episodes[0]
        self.assertEqual(first["title"], "First: a & b?")
        self.assertEqual(first["podcast"], "Test & Talk")
        self.assertEqual(first["description"], "Readable description")
        self.assertEqual(first["duration"], 3723)
        self.assertEqual(first["published"], "2026-10-03T10:00:00+00:00")
        self.assertEqual(first["image"], self.base + "/cover.jpg")
        self.assertEqual(episodes[1]["image"], first["image"])
        self.assertEqual(first["media_url"], self.base + "/audio.mp3?signature=abc")
        self.assertRegex(first["id"], r"^[a-f0-9]{24}$")
        self.assertEqual(read_feed(self.base + "/feed.xml")[0]["id"], first["id"])
        self.assertNotEqual(episodes[1]["id"], first["id"])

    def test_authenticated_feed_uses_existing_url_credentials(self):
        url = self.base.replace("http://", "http://user:secret@") + "/auth.xml"
        entries = read_feed(url)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry["image"] == "" for entry in entries))

    def test_rejects_invalid_feed_without_exposing_credentials(self):
        for path in ("/bad.xml", "/html", "/missing"):
            url = self.base.replace("http://", "http://user:secret@") + path + "?token=topsecret"
            with self.subTest(path=path), self.assertRaises(FeedError) as caught:
                read_feed(url)
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("127.0.0.1", str(caught.exception))
        # Validate malformed responses directly, without the auth query suffix.
        for path in ("/bad.xml", "/html"):
            with self.subTest(path=path), self.assertRaises(FeedError):
                read_feed(self.base + path)
        self.assertEqual(read_feed(self.base + "/empty.xml"), [])

    def test_rejects_non_http_urls_and_oversized_feeds(self):
        for url in ("file:///tmp/feed.xml", "javascript:alert(1)", "https://", "http://bad\nhost/feed"):
            with self.subTest(url=url), self.assertRaises(FeedError):
                read_feed(url)
        with patch("castwell.feeds.MAX_FEED_BYTES", 10), self.assertRaises(FeedError):
            read_feed(self.base + "/feed.xml")

    def test_media_download_is_exact_and_rerun_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "episode.mp3"
            progress = []
            self.assertEqual(download_media(self.base + "/audio.mp3", target, progress.append), target)
            self.assertEqual(target.read_bytes(), MEDIA)
            self.assertEqual(progress[-1], "Download complete")
            count = len(self.server.requests)
            download_media(self.base + "/missing", target)
            self.assertEqual(len(self.server.requests), count)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_failed_download_never_leaves_final_or_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "episode.mp3"
            for path in ("/truncated.mp3", "/missing", "/empty.mp3"):
                with self.subTest(path=path), self.assertRaises(DownloadError):
                    download_media(self.base + path, target)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
            with patch("castwell.feeds.MAX_MEDIA_BYTES", 4), self.assertRaises(DownloadError):
                download_media(self.base + "/audio.mp3", target)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancelled_download_preserves_previous_output_without_request(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "episode.mp3"
            target.write_bytes(MEDIA)
            with patch("castwell.feeds.requests.get") as request:
                with self.assertRaises(ProcessingCancelled):
                    download_media(self.base + "/audio.mp3", target, should_cancel=lambda: True)
            request.assert_not_called()
            self.assertEqual(target.read_bytes(), MEDIA)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_cancelled_stream_removes_partial_and_closes_response(self):
        cancelled = False
        consumed = []
        def chunks(chunk_size):
            for index in range(3):
                consumed.append(index)
                yield MEDIA
        def progress(message):
            nonlocal cancelled
            cancelled = True
        response = Mock(headers={"Content-Length": str(len(MEDIA) * 3)})
        response.iter_content.side_effect = chunks
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "episode.mp3"
            with patch("castwell.feeds.requests.get", return_value=context):
                with self.assertRaises(ProcessingCancelled):
                    download_media(self.base + "/audio.mp3", target, progress=progress,
                                   should_cancel=lambda: cancelled)
            self.assertEqual(consumed, [0, 1])
            self.assertEqual(list(Path(directory).iterdir()), [])
            context.__exit__.assert_called_once()

    def test_cancel_after_last_download_chunk_does_not_publish_completed_file(self):
        cancelled = False
        progress_messages = []
        def progress(message):
            nonlocal cancelled
            progress_messages.append(message)
            cancelled = True
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "episode.mp3"
            with self.assertRaises(ProcessingCancelled):
                download_media(self.base + "/audio.mp3", target, progress=progress,
                               should_cancel=lambda: cancelled)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertNotIn("Download complete", progress_messages)
            # A subsequent uncancelled retry starts cleanly and publishes exact bytes.
            download_media(self.base + "/audio.mp3", target)
            self.assertEqual(target.read_bytes(), MEDIA)

    def test_legacy_cli_downloads_correct_names_and_skips_repeat(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            download_feed(self.base + "/feed.xml", directory)
            paths = sorted(Path(directory).glob("*.mp3"))
            self.assertEqual([path.name for path in paths], ["20261003_First_a_b.mp3", "Second.mp3"])
            self.assertTrue(all(path.read_bytes() == MEDIA for path in paths))
            media_before = sum(".mp3" in path for path, _ in self.server.requests)
            download_feed(self.base + "/feed.xml", directory)
            self.assertEqual(sum(".mp3" in path for path, _ in self.server.requests), media_before)


if __name__ == "__main__":
    unittest.main()
