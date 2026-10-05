#!/usr/bin/env python
"""
download_podcast.py  –  Download all episodes from a podcast RSS feed.

Usage:
    python download_podcast.py http://omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/A91018A4-EA4F-4130-BF55-AE270180C327/44710ECC-10BB-48D1-93C7-AE270180C33E/podcast.rss ./StuffYouShouldKnow
"""
import sys, os, re, html
from pathlib import Path
from urllib.parse import urlsplit

from castwell.feeds import DownloadError, FeedError, download_media, read_feed

def slugify(text):
    text = html.unescape(text)
    text = re.sub(r"[^\w\-_. ]", "", text)
    return "_".join(text.strip().split())

def download_feed(feed_url, outdir):
    os.makedirs(outdir, exist_ok=True)
    try:
        episodes = read_feed(feed_url)
    except FeedError as exc:
        print("⚠️  Problem reading feed:", exc)
        return

    print(f"Found {len(episodes)} playable entries in feed «{episodes[0]['podcast'] if episodes else '?'}»")

    for entry in reversed(episodes):          # oldest→newest
        title = entry["title"]
        url = entry["media_url"]
        ext = Path(urlsplit(url).path).suffix
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", ext):
            ext = ".mp3"
        date = entry["published"]
        prefix = date[:10].replace("-", "") + "_" if re.match(r"^\d{4}-\d{2}-\d{2}", date) else ""
        filename = os.path.join(outdir, prefix + (slugify(title) or "untitled") + ext)

        if os.path.exists(filename):
            print("✔️  Already have", filename)
            continue

        try:
            download_media(url, Path(filename), progress=lambda message: print(f"{title[:40]}: {message}"))
        except DownloadError as exc:
            print(f"❌  Could not download “{title}”: {exc}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python download_podcast.py <feed_url> <output_dir>")
        sys.exit(1)
    download_feed(sys.argv[1], sys.argv[2])
