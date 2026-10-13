"""Durable library with additive migrations and short SQLite transactions."""

import hashlib
from copy import deepcopy
import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .ad_scope import REMOVAL_SCOPE


ACTIVE_STATUSES = ('queued', 'downloading', 'transcribing', 'detecting', 'rendering')
BOOLEAN_FIELDS = ('favorite', 'archived', 'played', 'analysis_done', 'transcript_review')
SUMMARY_DESCRIPTION_LIMIT = 256
ORIGINAL_MEDIA_EXTENSIONS = {'.mp3', '.m4a', '.mp4', '.ogg', '.opus', '.wav', '.flac', '.aac', '.audio'}
EPISODE_ADDITIONS = {
    'favorite': 'INTEGER NOT NULL DEFAULT 0',
    'archived': 'INTEGER NOT NULL DEFAULT 0',
    'played': 'INTEGER NOT NULL DEFAULT 0',
    'position': 'REAL NOT NULL DEFAULT 0',
    'analysis_done': 'INTEGER NOT NULL DEFAULT 0',
    'analysis_profile': 'TEXT',
    'transcript_review': 'INTEGER NOT NULL DEFAULT 0',
    'transcript_version': 'TEXT',
    'coverage_review': 'TEXT',
    'waveform': 'TEXT',
    'cleaned_duration': 'REAL NOT NULL DEFAULT 0',
    'feed_id': 'TEXT',
    'last_played': 'TEXT',
    'pending_job': 'TEXT',
    'podcast_id': 'TEXT',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def transcript_needs_review(transcript, duration=0):
    """Coverage uncertainty survives summary queries without loading the text."""
    if transcript is None:
        return False
    if not isinstance(transcript, dict):
        return True
    quality = transcript.get('quality')
    if isinstance(quality, dict) and isinstance(quality.get('requires_review'), bool):
        return quality['requires_review']
    from .transcript_quality import analyze_transcript
    source = dict(transcript)
    if not source.get('duration') and duration:
        source['duration'] = duration
    try:
        return analyze_transcript(source)['requires_review']
    except (ValueError, TypeError, KeyError):
        return True


def _quality_for(transcript, duration=0):
    quality = transcript.get('quality') if isinstance(transcript, dict) else None
    if isinstance(quality, dict):
        return deepcopy(quality)
    from .transcript_quality import analyze_transcript
    source = dict(transcript or {})
    if not source.get('duration') and duration:
        source['duration'] = duration
    try:
        return analyze_transcript(source)
    except (ValueError, TypeError, KeyError):
        return {'version': 1, 'method': 'timestamp-gaps', 'requires_review': True,
                'gap_count': 0, 'gaps': [],
                'warning': 'Transcript timestamps could not be checked. Review the original audio or regenerate the transcript before trusting ad coverage.'}


def _transcript_version(transcript, duration=0):
    if not isinstance(transcript, dict):
        return None
    source = deepcopy(transcript)
    source.setdefault('duration', duration)
    # Reusing a valid transcript normalizes integer timestamps to floats. That
    # formatting change must not invalidate a listener's review of the same text.
    try:
        from .processing import validate_transcript
        source = validate_transcript(source)
    except (ValueError, TypeError, KeyError):
        pass
    quality = _quality_for(transcript, duration)
    # A listener acknowledgement changes resolution, not the reviewed evidence.
    quality.pop('user_review', None)
    quality.pop('requires_review', None)
    source['quality'] = quality
    try:
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        return None
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _feed_identity(url):
    if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f]', url):
        raise ValueError('Enter a valid HTTP or HTTPS podcast feed URL.')
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Enter a valid HTTP or HTTPS podcast feed URL.')
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]


def _podcast_identity(metadata):
    """Feed identities survive unsubscribe; recordings share a local shelf."""
    if metadata.get('feed_url'):
        return _feed_identity(metadata['feed_url'])
    title = metadata.get('podcast') or 'Local uploads'
    return 'local-' + hashlib.sha256(title.strip().casefold().encode('utf-8')).hexdigest()[:24]


def _public_image(value, feed_url=''):
    """Keep artwork usable without forwarding credentials from a subscription."""
    try:
        image = urlsplit(value or '')
        if image.scheme.lower() not in ('http', 'https') or not image.hostname:
            return ''
        if image.username is not None or image.password is not None:
            return ''
        feed = urlsplit(feed_url or '')
        # Relative artwork can inherit a query token. A subscription credential
        # must not become a browser-visible artwork request.
        private_values = {v for _, v in parse_qsl(feed.query) if v}
        private_values.update(v for v in (feed.username, feed.password) if v)
        if any(secret in (value or '') for secret in private_values):
            return ''
        return value
    except (ValueError, TypeError):
        return ''


class Library:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'library.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available', error TEXT,
                progress TEXT NOT NULL DEFAULT '', audio TEXT, cleaned TEXT,
                transcript TEXT, cuts TEXT NOT NULL DEFAULT '[]',
                duration REAL NOT NULL DEFAULT 0, removed_seconds REAL NOT NULL DEFAULT 0
            )''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(episodes)')}
            for name, definition in EPISODE_ADDITIONS.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE episodes ADD COLUMN {name} {definition}')
            if 'transcript_review' not in columns:
                for row in db.execute('SELECT id,transcript,duration FROM episodes WHERE transcript IS NOT NULL').fetchall():
                    try:
                        transcript = json.loads(row['transcript'])
                    except (ValueError, TypeError):
                        transcript = {}
                    db.execute('UPDATE episodes SET transcript_review=? WHERE id=?',
                               (transcript_needs_review(transcript, row['duration']), row['id']))
            if 'transcript_version' not in columns:
                for row in db.execute('SELECT id,transcript,duration FROM episodes WHERE transcript IS NOT NULL').fetchall():
                    try:
                        transcript = json.loads(row['transcript'])
                    except (ValueError, TypeError):
                        transcript = None
                    db.execute('UPDATE episodes SET transcript_version=? WHERE id=?',
                               (_transcript_version(transcript, row['duration']), row['id']))
            db.execute('CREATE INDEX IF NOT EXISTS episodes_feed_id ON episodes(feed_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
                last_checked TEXT, error TEXT, created_at TEXT NOT NULL
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS podcasts (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                image TEXT NOT NULL DEFAULT '', ad_mode TEXT NOT NULL DEFAULT 'remove'
                    CHECK(ad_mode IN ('remove','keep')), created_at TEXT NOT NULL
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS episodes_podcast_id ON episodes(podcast_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS cut_revisions (
                id TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
                created_at TEXT NOT NULL, cuts TEXT NOT NULL, reason TEXT NOT NULL
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS revisions_episode_id ON cut_revisions(episode_id)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)')
            if 'analysis_done' not in columns:
                # Preserve explicit decisions from the previous schema, including
                # intentionally empty edits. Merely transcribed episodes differ.
                db.execute('''UPDATE episodes SET analysis_done=1 WHERE progress IN (
                    'Cut changes saved. Export to apply them.',
                    'No cuts selected; original audio retained',
                    'No ads detected; original audio retained',
                    'Approved cuts exported', 'Review suggested cuts'
                )''')
            # Only backfill once. Repeating this would silently resubscribe to
            # feeds a listener removed while retaining their downloaded episodes.
            if not db.execute("SELECT 1 FROM migrations WHERE name='subscriptions-v1'").fetchone():
                for row in db.execute('SELECT id, metadata FROM episodes').fetchall():
                    metadata = json.loads(row['metadata'])
                    url = metadata.get('feed_url')
                    if not url:
                        continue
                    try:
                        feed_id = self._upsert_feed(db, url, metadata.get('podcast', ''), metadata.get('image', ''))
                    except ValueError:
                        continue
                    db.execute('UPDATE episodes SET feed_id=? WHERE id=?', (feed_id, row['id']))
                db.execute("INSERT INTO migrations (name) VALUES ('subscriptions-v1')")
            if not db.execute("SELECT 1 FROM migrations WHERE name='podcast-preferences-v1'").fetchone():
                for row in db.execute('SELECT id,metadata FROM episodes').fetchall():
                    metadata = json.loads(row['metadata'])
                    try:
                        podcast_id = _podcast_identity(metadata)
                    except ValueError:
                        podcast_id = _podcast_identity({**metadata, 'feed_url': ''})
                    self._upsert_podcast(db, podcast_id, metadata.get('podcast') or 'Local uploads', metadata.get('image', ''))
                    db.execute('UPDATE episodes SET podcast_id=? WHERE id=?', (podcast_id, row['id']))
                for row in db.execute('SELECT id,title,image FROM feeds').fetchall():
                    self._upsert_podcast(db, row['id'], row['title'], row['image'])
                db.execute("INSERT INTO migrations (name) VALUES ('podcast-preferences-v1')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE episodes SET status='error', error='Processing was interrupted. Retry to resume.', progress='' WHERE status IN (?,?,?,?,?)", ACTIVE_STATUSES)

    @staticmethod
    def _upsert_podcast(db, podcast_id, title='', image=''):
        db.execute('''INSERT INTO podcasts (id,title,image,created_at) VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=CASE WHEN excluded.title != '' THEN excluded.title ELSE podcasts.title END,
                image=CASE WHEN excluded.image != '' THEN excluded.image ELSE podcasts.image END''',
            (podcast_id, title or '', image or '', _now()))

    @staticmethod
    def _upsert_feed(db, url, title='', image=''):
        feed_id = _feed_identity(url)
        db.execute('''INSERT INTO feeds (id,url,title,image,created_at) VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=CASE WHEN excluded.title != '' THEN excluded.title ELSE feeds.title END,
                image=CASE WHEN excluded.image != '' THEN excluded.image ELSE feeds.image END''',
            (feed_id, url, title or '', image or '', _now()))
        Library._upsert_podcast(db, feed_id, title, image)
        return feed_id

    def import_entries(self, entries):
        """Refresh feed metadata without resetting downloaded audio or review state."""
        added = 0
        with self.connect() as db:
            for entry in entries:
                entry = dict(entry)
                existing = db.execute('SELECT metadata FROM episodes WHERE id=?', (entry['id'],)).fetchone()
                metadata = json.loads(existing['metadata']) if existing else {}
                metadata.update(entry)
                url = metadata.get('feed_url')
                feed_id = self._upsert_feed(db, url, metadata.get('podcast', ''), metadata.get('image', '')) if url else None
                podcast_id = _podcast_identity(metadata)
                self._upsert_podcast(db, podcast_id, metadata.get('podcast') or 'Local uploads', metadata.get('image', ''))
                metadata['feed_id'] = feed_id
                result = db.execute('INSERT OR IGNORE INTO episodes (id,metadata,duration,feed_id,podcast_id) VALUES (?,?,?,?,?)',
                    (entry['id'], json.dumps(metadata), entry.get('duration', 0), feed_id, podcast_id))
                added += result.rowcount
                db.execute('UPDATE episodes SET metadata=?,feed_id=?,podcast_id=? WHERE id=?', (json.dumps(metadata), feed_id, podcast_id, entry['id']))
        return added

    def _owned_media_path(self, episode_id, field, value):
        """Resolve a moved library's canonical media without rewriting its DB."""
        if (not isinstance(value, str) or not value or not isinstance(episode_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]+', episode_id)):
            return value
        try:
            stored = Path(value)
            if (not stored.is_absolute() or '..' in stored.parts or re.search(r'[\x00-\x1f]', value)
                    or stored.parent.name != episode_id or stored.parent.parent.name.casefold() != 'episodes'):
                return value
            filename = stored.name
            if field == 'audio':
                if stored.stem != 'original' or stored.suffix not in ORIGINAL_MEDIA_EXTENSIONS:
                    return value
            elif field != 'cleaned' or filename != 'cleaned.mp3':
                return value
            candidate = self.root / 'episodes' / episode_id / filename
            resolved = candidate.resolve()
            # An episode folder or media symlink must not redirect this rebasing
            # to another recording, even if that target is also in the library.
            if resolved != candidate or not resolved.is_relative_to(self.root) or not resolved.is_file():
                return value
            return str(resolved)
        except (OSError, ValueError):
            return value

    def decode(self, row):
        if row is None:
            raise KeyError('Episode not found')
        record = dict(row)
        metadata = json.loads(record.pop('metadata'))
        for field in ('transcript', 'waveform', 'pending_job', 'analysis_profile', 'coverage_review'):
            record[field] = json.loads(record[field]) if record[field] else None
        record['cuts'] = json.loads(record['cuts'])
        for field in BOOLEAN_FIELDS:
            record[field] = bool(record[field])
        for field in ('audio', 'cleaned'):
            record[field] = self._owned_media_path(record['id'], field, record[field])
        quality = record['transcript'].get('quality') if isinstance(record['transcript'], dict) else None
        has_user_review = isinstance(quality, dict) and bool(quality.get('user_review'))
        if (record.get('coverage_review') or has_user_review) and not self.coverage_review_current(record):
            record['transcript_review'] = True
            if has_user_review:
                record['transcript'] = deepcopy(record['transcript'])
                record['transcript']['quality']['requires_review'] = True
        # Legacy rows may have claimed completion before coverage warnings were
        # part of readiness. Lightweight RSS reads must see the same exception
        # status as the gallery, without changing a Keep-original preference.
        if (record.get('ad_mode', 'remove') == 'remove' and record['status'] == 'ready'
                and (record.get('transcript_review') or any(cut.get('approved') is not True for cut in record['cuts']))):
            record['status'] = 'review'
        return {**metadata, **record}

    def _original_version(self, episode):
        try:
            path = Path(episode.get('audio') or '').resolve(strict=True)
            if not path.is_relative_to(self.root) or not path.is_file():
                return None
            metadata = path.stat()
            return {'path': path.relative_to(self.root).as_posix(), 'bytes': metadata.st_size,
                    'modified_ns': metadata.st_mtime_ns, 'created_ns': metadata.st_ctime_ns}
        except (OSError, ValueError):
            return None

    def coverage_review_token(self, episode):
        original = self._original_version(episode)
        version = episode.get('transcript_version')
        if original is None or not version or episode.get('transcript') is None:
            return None
        encoded = json.dumps({'episode_id': episode['id'], 'transcript_version': version, 'original': original},
                             sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    def coverage_review_current(self, episode):
        review = episode.get('coverage_review')
        return bool(isinstance(review, dict) and review.get('listener_reported_listening') is True
                    and review.get('review_token') == self.coverage_review_token(episode))

    def acknowledge_coverage(self, episode_id, review_token):
        episode = self.get(episode_id)
        token = self.coverage_review_token(episode)
        if token is None:
            raise ValueError('An existing managed original and transcript are required before reviewing coverage')
        if token != review_token:
            raise ValueError('The original audio or transcript changed. Reopen the episode and review its current coverage.')
        transcript = deepcopy(episode['transcript'])
        quality = _quality_for(transcript, episode['duration'])
        review = {'method': 'explicit-listener-acknowledgement', 'reviewed_at': _now(),
                  'listener_reported_listening': True, 'model_verified': False,
                  'human_listening_verified': False, 'original_requires_review': quality.get('requires_review'),
                  'assertion': 'The listener reports having listened and addressed any missed advertisements.'}
        quality['requires_review'] = False
        quality['user_review'] = review
        transcript['quality'] = quality
        self.update(episode_id, transcript=transcript, coverage_review={**review, 'review_token': token},
                    status='review', progress='Coverage reviewed by you. Render saved cuts to finish this episode.')
        return self.get(episode_id)

    def get(self, episode_id, include_large=True):
        with self.connect() as db:
            if include_large:
                columns = 'episodes.*'
            else:
                names = ['id', 'metadata', 'status', 'error', 'progress', 'audio', 'cleaned',
                         'cuts', 'duration', 'removed_seconds', *[name for name in EPISODE_ADDITIONS if name != 'waveform']]
                columns = ','.join('episodes.' + name for name in names) + ", CASE WHEN transcript IS NULL THEN NULL ELSE '{}' END AS transcript,NULL AS waveform"
            return self.decode(db.execute("SELECT " + columns + ",COALESCE(podcasts.ad_mode,'remove') AS ad_mode FROM episodes LEFT JOIN podcasts ON podcasts.id=episodes.podcast_id WHERE episodes.id=?", (episode_id,)).fetchone())

    def all(self, include_large=True):
        with self.connect() as db:
            if include_large:
                columns = 'episodes.*'
            else:
                names = ['id', 'metadata', 'status', 'error', 'progress', 'audio', 'cleaned',
                         'cuts', 'duration', 'removed_seconds', *[name for name in EPISODE_ADDITIONS if name != 'waveform']]
                columns = ','.join('episodes.' + name for name in names) + ",CASE WHEN transcript IS NULL THEN NULL ELSE '{}' END AS transcript,NULL AS waveform"
            rows = db.execute("SELECT " + columns + ",COALESCE(podcasts.ad_mode,'remove') AS ad_mode FROM episodes LEFT JOIN podcasts ON podcasts.id=episodes.podcast_id ORDER BY episodes.rowid DESC").fetchall()
        return sorted((self.decode(row) for row in rows), key=lambda item: item.get('published') or '', reverse=True)

    def summaries(self):
        """Gallery polling never reads large transcripts or cached waveform data."""
        columns = ['id', 'metadata', 'status', 'error', 'progress', 'audio', 'cleaned',
                   'cuts', 'duration', 'removed_seconds', *[key for key in EPISODE_ADDITIONS if key != 'waveform']]
        with self.connect() as db:
            rows = db.execute('SELECT ' + ','.join('episodes.' + column for column in columns) + ", CASE WHEN transcript IS NULL THEN NULL ELSE '{}' END AS transcript, NULL AS waveform,COALESCE(podcasts.ad_mode,'remove') AS ad_mode FROM episodes LEFT JOIN podcasts ON podcasts.id=episodes.podcast_id ORDER BY episodes.rowid DESC").fetchall()
        episodes = sorted((self.decode(row) for row in rows), key=lambda item: item.get('published') or '', reverse=True)
        return [self.public(episode) for episode in episodes]

    def update(self, episode_id, **values):
        allowed = {'status', 'error', 'progress', 'audio', 'cleaned', 'transcript', 'cuts', 'duration', 'removed_seconds', *EPISODE_ADDITIONS}
        if not values or not values.keys() <= allowed:
            raise ValueError('Unsupported library update')
        if 'transcript' in values:
            duration = values.get('duration')
            if duration is None:
                duration = self.get(episode_id, include_large=False)['duration']
            values['transcript_review'] = transcript_needs_review(values['transcript'], duration)
            values['transcript_version'] = _transcript_version(values['transcript'], duration)
        if {'audio', 'transcript'} & values.keys():
            current = self.get(episode_id, include_large=False)
            candidate = {**current, **values}
            incoming = values.get('transcript')
            incoming_quality = incoming.get('quality') if isinstance(incoming, dict) else None
            has_review = isinstance(incoming_quality, dict) and bool(incoming_quality.get('user_review'))
            retained_review = self.coverage_review_current(candidate)
            if 'transcript' in values and not has_review and 'coverage_review' not in values:
                values['coverage_review'] = None
                retained_review = False
            pending_review = values.get('coverage_review', candidate.get('coverage_review'))
            if (has_review or pending_review) and not retained_review:
                values['transcript_review'] = True
        # Explicit saved edits are the listener's decisions, not a reusable
        # detector result. Detection supplies its own profile in the same write.
        if 'cuts' in values and 'analysis_profile' not in values:
            values['analysis_profile'] = None
        for field in ('transcript', 'cuts', 'waveform', 'pending_job', 'analysis_profile', 'coverage_review'):
            if field in values and values[field] is not None:
                values[field] = json.dumps(values[field], allow_nan=False)
        with self.connect() as db:
            result = db.execute('UPDATE episodes SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), episode_id))
            if not result.rowcount:
                raise KeyError('Episode not found')

    def set_episode_flags(self, episode_id, **values):
        if not values or not values.keys() <= {'favorite', 'archived', 'played', 'position'}:
            raise ValueError('Unsupported episode preference')
        for key, value in values.items():
            if key == 'position':
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError('Playback position must be a finite, non-negative number.')
            elif not isinstance(value, bool):
                raise ValueError('Episode flags must be true or false.')
        self.update(episode_id, **values)
        return self.get(episode_id)

    def add_feed(self, url, title='', image=''):
        with self.connect() as db:
            feed_id = self._upsert_feed(db, url, title, image)
        return self.get_feed(feed_id)

    def podcasts(self):
        """Catalogue includes retained episodes even after a subscription ends."""
        with self.connect() as db:
            rows = db.execute('''SELECT podcasts.*,feeds.url AS subscription_url,
                EXISTS(SELECT 1 FROM feeds WHERE feeds.id=podcasts.id) AS subscribed,
                (SELECT metadata FROM episodes WHERE podcast_id=podcasts.id ORDER BY rowid DESC LIMIT 1) AS sample_metadata
                FROM podcasts LEFT JOIN feeds ON feeds.id=podcasts.id
                ORDER BY podcasts.title COLLATE NOCASE,podcasts.created_at''').fetchall()
        episodes = self.summaries()
        grouped = {}
        for episode in episodes:
            grouped.setdefault(episode['podcast_id'], []).append(episode)
        result = []
        for row in rows:
            record = dict(row)
            sample = json.loads(record.pop('sample_metadata') or '{}')
            url = record.pop('subscription_url') or sample.get('feed_url', '')
            record['image'] = _public_image(record['image'], url)
            record['subscribed'] = bool(record['subscribed'])
            record['source'] = 'local' if record['id'].startswith('local-') else 'feed'
            shelf = grouped.get(record['id'], [])
            # Empty unsubscribed shelves are harmless, but not useful catalogue entries.
            if not shelf and not record['subscribed']:
                continue
            record.update(episode_count=len(shelf), downloaded_count=sum(item['has_audio'] for item in shelf),
                          ready_count=sum(item['export_ready'] for item in shelf),
                          review_count=sum(item['status'] == 'review' for item in shelf),
                          unplayed_count=sum(not item['played'] and not item['archived'] for item in shelf),
                          favorite_count=sum(item['favorite'] for item in shelf))
            result.append(record)
        return result

    def set_podcast_mode(self, podcast_ids, ad_mode):
        if ad_mode not in ('remove', 'keep'):
            raise ValueError('Ad mode must be remove or keep.')
        with self.connect() as db:
            ids = list(dict.fromkeys(podcast_ids))
            if any(not db.execute('SELECT 1 FROM podcasts WHERE id=?', (podcast_id,)).fetchone() for podcast_id in ids):
                raise KeyError('Podcast not found')
            db.executemany('UPDATE podcasts SET ad_mode=? WHERE id=?', [(ad_mode, podcast_id) for podcast_id in ids])
        return ids

    def batch_episode_flags(self, episode_ids, **values):
        """Apply validated preferences in one transaction; missing rows cannot partly commit."""
        if not values or not values.keys() <= {'favorite', 'archived', 'played'}:
            raise ValueError('Choose favorite, archived, or played for a batch update.')
        if any(not isinstance(value, bool) for value in values.values()):
            raise ValueError('Episode flags must be true or false.')
        ids = list(dict.fromkeys(episode_ids))
        with self.connect() as db:
            if any(not db.execute('SELECT 1 FROM episodes WHERE id=?', (episode_id,)).fetchone() for episode_id in ids):
                raise KeyError('Episode not found')
            updates = dict(values)
            if updates.get('played'):
                updates['last_played'] = _now()
            db.executemany('UPDATE episodes SET ' + ','.join(f'{key}=?' for key in updates) + ' WHERE id=?',
                           [(*updates.values(), episode_id) for episode_id in ids])
        return ids

    def get_feed(self, feed_id):
        with self.connect() as db:
            row = db.execute('''SELECT feeds.*, (SELECT COUNT(*) FROM episodes WHERE feed_id=feeds.id) AS episode_count
                FROM feeds WHERE id=?''', (feed_id,)).fetchone()
        if row is None:
            raise KeyError('Subscription not found')
        return dict(row)

    def feeds(self, public=True):
        with self.connect() as db:
            rows = db.execute('''SELECT feeds.*, (SELECT COUNT(*) FROM episodes WHERE feed_id=feeds.id) AS episode_count
                FROM feeds ORDER BY title COLLATE NOCASE,created_at''').fetchall()
        records = [dict(row) for row in rows]
        if not public:
            return records
        for record in records:
            url = record.pop('url')
            record['url_display'] = urlsplit(url).hostname or ''
            record['image'] = _public_image(record['image'], url)
            # Request exceptions can contain complete premium URLs; errors in a
            # public subscription list never echo arbitrary stored exception text.
            if record['error']:
                record['error'] = 'Could not refresh this subscription. Check its address and access credentials.'
        return records

    def mark_feed(self, feed_id, **fields):
        if not fields or not fields.keys() <= {'title', 'image', 'last_checked', 'error'}:
            raise ValueError('Unsupported subscription update')
        with self.connect() as db:
            result = db.execute('UPDATE feeds SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?', (*fields.values(), feed_id))
            if not result.rowcount:
                raise KeyError('Subscription not found')
            feed = db.execute('SELECT title,image FROM feeds WHERE id=?', (feed_id,)).fetchone()
            self._upsert_podcast(db, feed_id, feed['title'], feed['image'])
        return self.get_feed(feed_id)

    def delete_feed(self, feed_id):
        """Unsubscribe without deleting any episodes, audio, or review history."""
        with self.connect() as db:
            result = db.execute('DELETE FROM feeds WHERE id=?', (feed_id,))
            if not result.rowcount:
                raise KeyError('Subscription not found')

    def save_revision(self, episode_id, cuts, reason):
        revision = {'id': uuid.uuid4().hex, 'episode_id': episode_id, 'created_at': _now(), 'cuts': cuts, 'reason': reason}
        encoded = json.dumps(cuts, allow_nan=False)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM episodes WHERE id=?', (episode_id,)).fetchone():
                raise KeyError('Episode not found')
            db.execute('INSERT INTO cut_revisions (id,episode_id,created_at,cuts,reason) VALUES (?,?,?,?,?)',
                (revision['id'], episode_id, revision['created_at'], encoded, reason))
            db.execute('''DELETE FROM cut_revisions WHERE id IN (
                SELECT id FROM cut_revisions WHERE episode_id=? ORDER BY rowid DESC LIMIT -1 OFFSET 30
            )''', (episode_id,))
        return revision

    @staticmethod
    def _decode_revision(row):
        if row is None:
            raise KeyError('Cut revision not found')
        record = dict(row)
        record['cuts'] = json.loads(record['cuts'])
        return record

    def revisions(self, episode_id):
        self.get(episode_id)
        with self.connect() as db:
            return [self._decode_revision(row) for row in db.execute(
                'SELECT * FROM cut_revisions WHERE episode_id=? ORDER BY rowid DESC', (episode_id,))]

    def revision(self, episode_id, revision_id):
        with self.connect() as db:
            return self._decode_revision(db.execute('SELECT * FROM cut_revisions WHERE episode_id=? AND id=?', (episode_id, revision_id)).fetchone())

    def get_settings(self):
        with self.connect() as db:
            return {row['key']: json.loads(row['value']) for row in db.execute('SELECT key,value FROM settings')}

    def update_settings(self, values):
        if not isinstance(values, dict) or any(not isinstance(key, str) or not key for key in values):
            raise ValueError('Settings must be a dictionary with non-empty string keys.')
        encoded = [(key, json.dumps(value, allow_nan=False)) for key, value in values.items()]
        with self.connect() as db:
            db.executemany('INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', encoded)
        return self.get_settings()

    def backup(self, destination):
        """Atomically save a complete SQLite snapshot, including uncheckpointed WAL."""
        destination = Path(destination).expanduser().resolve()
        if destination == self.db.resolve():
            raise ValueError('Choose a backup destination outside the live database.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.part')
        try:
            with self.connect() as source:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target)
                finally:
                    target.close()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def directory(self, episode_id):
        self.get(episode_id)
        path = self.root / 'episodes' / episode_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def public(self, episode, detail=False):
        # Feed URLs can contain premium-feed credentials; they never leave the server.
        result = {key: value for key, value in episode.items() if key not in {'feed_url', 'media_url', 'audio', 'cleaned', 'transcript', 'cuts', 'waveform', 'pending_job', 'analysis_profile', 'coverage_review', 'transcript_version'}}
        result['image'] = _public_image(result.get('image'), episode.get('feed_url'))
        if not detail:
            description = str(result.get('description') or '')
            result['description'] = description[:SUMMARY_DESCRIPTION_LIMIT]
            result['description_truncated'] = len(description) > SUMMARY_DESCRIPTION_LIMIT
        for field in BOOLEAN_FIELDS:
            result[field] = bool(episode.get(field))
        result.update(
            has_audio=bool(episode['audio'] and Path(episode['audio']).is_file()),
            has_cleaned=bool(episode['cleaned'] and Path(episode['cleaned']).is_file()),
            has_transcript=episode['transcript'] is not None,
            ad_count=len(episode['cuts']),
        )
        result['ad_mode'] = episode.get('ad_mode', 'remove')
        profile = episode.get('analysis_profile')
        scope_review = isinstance(profile, dict) and profile.get('removal_scope') != REMOVAL_SCOPE
        unresolved = bool(scope_review or episode.get('transcript_review')
                          or any(cut.get('approved') is not True for cut in episode['cuts']))
        if result['ad_mode'] == 'remove' and unresolved and result['status'] == 'ready':
            result['status'] = 'review'
            if scope_review:
                result['progress'] = 'Ad-removal rules changed. Find ads again or review and save these cuts before exporting.'
        approved_copy = (result['has_cleaned'] and not unresolved and result['status'] == 'ready'
                         and any(cut.get('approved') is True for cut in episode['cuts']))
        result['preferred_version'] = 'cleaned' if result['ad_mode'] == 'remove' and approved_copy else 'original'
        completed_original = episode.get('analysis_done') and not episode['cuts'] and episode['status'] == 'ready'
        result['export_ready'] = bool(result['has_audio'] and (result['ad_mode'] == 'keep'
                                      or not unresolved and (approved_copy or completed_original)))
        for field, path_key in (('media_version', 'cleaned'), ('original_media_version', 'audio')):
            try:
                result[field] = str(Path(episode[path_key]).stat().st_mtime_ns) if episode[path_key] else ''
            except OSError:
                result[field] = ''
        if detail:
            transcript = episode['transcript']
            if isinstance(transcript, dict) and not isinstance(transcript.get('quality'), dict):
                # Older/imported transcripts still need coverage warnings. This
                # is a read-only view: reviewed cuts and stored speech stay intact.
                from .transcript_quality import analyze_transcript

                diagnostic_source = dict(transcript)
                if not diagnostic_source.get('duration') and episode.get('duration'):
                    diagnostic_source['duration'] = episode['duration']
                try:
                    quality = analyze_transcript(diagnostic_source)
                except (ValueError, TypeError, KeyError):
                    quality = {'version': 1, 'method': 'timestamp-gaps', 'requires_review': True,
                               'gap_count': 0, 'gaps': [],
                               'warning': 'Transcript timestamps could not be checked. Review the original audio or regenerate the transcript before trusting ad coverage.'}
                transcript = dict(transcript, quality=quality)
            result.update(transcript=transcript, cuts=episode['cuts'])
            result['coverage_review_token'] = self.coverage_review_token(episode)
            result['coverage_review_acknowledged'] = self.coverage_review_current(episode)
        return result
