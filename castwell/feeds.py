"""Fetch podcast metadata and publish complete media downloads atomically."""

import calendar
import hashlib
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.sax import SAXParseException

import feedparser
import requests


REQUEST_TIMEOUT = (10, 60)
MAX_FEED_BYTES = 20 * 1024 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024


class FeedError(ValueError):
    """An invalid or unavailable podcast feed; messages never include its URL."""


class DownloadError(ValueError):
    """An invalid or incomplete download; messages never include its URL."""


def _http_url(value):
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f]", value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(value):
    parser = _PlainText()
    parser.feed(str(value or ""))
    return " ".join("".join(parser.parts).split())


def _duration(value):
    try:
        parts = str(value or 0).split(":")
        if len(parts) > 3:
            return 0.0
        duration = 0.0
        for part in parts:
            component = float(part)
            if not math.isfinite(component) or component < 0:
                return 0.0
            duration = duration * 60 + component
        return duration
    except (ValueError, TypeError):
        return 0.0


def _image(entry, base_url):
    image = entry.get("image", {})
    value = image.get("href") or image.get("url") if isinstance(image, dict) else image
    if not value:
        thumbnails = entry.get("media_thumbnail", [])
        value = thumbnails[0].get("url") if thumbnails else ""
    url = urljoin(base_url, str(value or "")) if value else ""
    if not _http_url(url):
        return ""
    parsed = urlsplit(url)
    # Artwork is delivered to the browser. Do not expose premium feed HTTP
    # credentials inherited while resolving a relative image URL.
    return "" if parsed.username is not None or parsed.password is not None else url


def _published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return _plain_text(entry.get("published") or entry.get("updated") or "")


def _content_length(response):
    try:
        return max(0, int(response.headers.get("Content-Length", 0)))
    except (TypeError, ValueError):
        return 0


def read_feed(url):
    """Return normalized playable episodes, preserving the feed's entry order.

    Premium URLs with embedded HTTP credentials work through requests. Non-media
    entries are ignored. Local HTTP feeds are supported for private subscriptions.
    """
    if not _http_url(url):
        raise FeedError("Enter a valid HTTP or HTTPS podcast feed URL.")
    try:
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            if _content_length(response) > MAX_FEED_BYTES:
                raise FeedError("The podcast feed exceeds the 20 MB size limit.")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_FEED_BYTES:
                    raise FeedError("The podcast feed exceeds the 20 MB size limit.")
            base_url = response.url
    except requests.RequestException:
        raise FeedError("Could not fetch the podcast feed. Check its address and access credentials.") from None

    feed = feedparser.parse(bytes(data))
    if not feed.get("version") or (feed.get("bozo") and not feed.get("entries")):
        raise FeedError("The response is not a readable RSS or Atom podcast feed.")
    # A namespace warning can occur in otherwise useful feeds, but broken XML is
    # incomplete data and must not silently replace a previously loaded library.
    if feed.get("bozo") and isinstance(feed.get("bozo_exception"), SAXParseException):
        raise FeedError("The podcast feed contains malformed XML.")

    podcast = _plain_text(feed.feed.get("title")) or "Untitled podcast"
    podcast_image = _image(feed.feed, base_url)
    episodes = []
    seen = set()
    for entry in feed.entries:
        media_url = ""
        for enclosure in entry.get("enclosures", []):
            if not enclosure.get("href"):
                continue
            candidate = urljoin(base_url, enclosure.get("href", ""))
            media_type = str(enclosure.get("type", "")).lower()
            if _http_url(candidate) and (not media_type or media_type.startswith(("audio/", "video/")) or media_type == "application/octet-stream"):
                media_url = candidate
                break
        if not media_url:
            continue
        identity = str(entry.get("id") or entry.get("guid") or media_url)
        episode_id = hashlib.sha256((url + "\0" + identity).encode("utf-8")).hexdigest()[:24]
        if episode_id in seen:
            continue
        seen.add(episode_id)
        content = entry.get("content", [])
        description = entry.get("summary") or (content[0].get("value") if content else "")
        episodes.append({
            "id": episode_id,
            "title": _plain_text(entry.get("title")) or "Untitled episode",
            "podcast": podcast,
            "description": _plain_text(description),
            "image": _image(entry, base_url) or podcast_image,
            "published": _published(entry),
            "media_url": media_url,
            "feed_url": url,
            "duration": _duration(entry.get("itunes_duration")),
        })
    return episodes


def download_media(url, destination, progress=None, should_cancel=None):
    """Download up to 2 GiB; only complete files appear at ``destination``.

    Existing files are retained. ``progress`` receives human-readable status
    strings. A failed request removes its temporary file and can safely be retried.
    """
    def check_cancelled():
        if should_cancel is not None and should_cancel():
            from .processing import ProcessingCancelled
            raise ProcessingCancelled("Download cancelled; completed files were retained.")

    check_cancelled()
    if not _http_url(url):
        raise DownloadError("The episode needs a valid HTTP or HTTPS media URL.")
    destination = Path(destination)
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            total = _content_length(response)
            if total > MAX_MEDIA_BYTES:
                raise DownloadError("The episode exceeds the 2 GB download size limit.")
            received = 0
            last_report = -1
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix="." + destination.name + ".", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    check_cancelled()
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_MEDIA_BYTES:
                        raise DownloadError("The episode exceeds the 2 GB download size limit.")
                    output.write(chunk)
                    report = received // (1024 * 1024)
                    if progress and report != last_report:
                        progress(f"Downloaded {received / (1024 * 1024):.1f} MB")
                        last_report = report
                if not received:
                    raise DownloadError("The episode download was empty.")
                if total and not response.headers.get("Content-Encoding") and received != total:
                    raise DownloadError("The episode download was incomplete. Please retry.")
                output.flush()
                os.fsync(output.fileno())
            check_cancelled()
            # Both paths are on the same filesystem. Linking publishes the
            # finished file atomically without overwriting a concurrent download.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if not destination.is_file():
                    raise DownloadError("The download destination is not a file.") from None
            if progress:
                progress("Download complete")
            return destination
    except requests.RequestException:
        raise DownloadError("Could not download the episode. Check the media address and access credentials, then retry.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
