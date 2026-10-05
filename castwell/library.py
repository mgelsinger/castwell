"""Durable library with additive migrations and short SQLite transactions."""

import hashlib
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


ACTIVE_STATUSES = ('queued', 'downloading', 'transcribing', 'detecting', 'rendering')
BOOLEAN_FIELDS = ('favorite', 'archived', 'played', 'analysis_done')
EPISODE_ADDITIONS = {
    'favorite': 'INTEGER NOT NULL DEFAULT 0',
    'archived': 'INTEGER NOT NULL DEFAULT 0',
    'played': 'INTEGER NOT NULL DEFAULT 0',
    'position': 'REAL NOT NULL DEFAULT 0',
    'analysis_done': 'INTEGER NOT NULL DEFAULT 0',
    'waveform': 'TEXT',
    'cleaned_duration': 'REAL NOT NULL DEFAULT 0',
    'feed_id': 'TEXT',
    'last_played': 'TEXT',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


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
            db.execute('CREATE INDEX IF NOT EXISTS episodes_feed_id ON episodes(feed_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
                last_checked TEXT, error TEXT, created_at TEXT NOT NULL
            )''')
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
    def _upsert_feed(db, url, title='', image=''):
        feed_id = _feed_identity(url)
        db.execute('''INSERT INTO feeds (id,url,title,image,created_at) VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=CASE WHEN excluded.title != '' THEN excluded.title ELSE feeds.title END,
                image=CASE WHEN excluded.image != '' THEN excluded.image ELSE feeds.image END''',
            (feed_id, url, title or '', image or '', _now()))
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
                metadata['feed_id'] = feed_id
                result = db.execute('INSERT OR IGNORE INTO episodes (id,metadata,duration,feed_id) VALUES (?,?,?,?)',
                    (entry['id'], json.dumps(metadata), entry.get('duration', 0), feed_id))
                added += result.rowcount
                db.execute('UPDATE episodes SET metadata=?,feed_id=? WHERE id=?', (json.dumps(metadata), feed_id, entry['id']))
        return added

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError('Episode not found')
        record = dict(row)
        metadata = json.loads(record.pop('metadata'))
        for field in ('transcript', 'waveform'):
            record[field] = json.loads(record[field]) if record[field] else None
        record['cuts'] = json.loads(record['cuts'])
        for field in BOOLEAN_FIELDS:
            record[field] = bool(record[field])
        return {**metadata, **record}

    def get(self, episode_id):
        with self.connect() as db:
            return self.decode(db.execute('SELECT * FROM episodes WHERE id=?', (episode_id,)).fetchone())

    def all(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM episodes ORDER BY rowid DESC').fetchall()
        return sorted((self.decode(row) for row in rows), key=lambda item: item.get('published') or '', reverse=True)

    def summaries(self):
        """Gallery polling never reads large transcripts or cached waveform data."""
        columns = ['id', 'metadata', 'status', 'error', 'progress', 'audio', 'cleaned',
                   'cuts', 'duration', 'removed_seconds', *[key for key in EPISODE_ADDITIONS if key != 'waveform']]
        with self.connect() as db:
            rows = db.execute('SELECT ' + ','.join(columns) + ", CASE WHEN transcript IS NULL THEN NULL ELSE '{}' END AS transcript, NULL AS waveform FROM episodes ORDER BY rowid DESC").fetchall()
        episodes = sorted((self.decode(row) for row in rows), key=lambda item: item.get('published') or '', reverse=True)
        return [self.public(episode) for episode in episodes]

    def update(self, episode_id, **values):
        allowed = {'status', 'error', 'progress', 'audio', 'cleaned', 'transcript', 'cuts', 'duration', 'removed_seconds', *EPISODE_ADDITIONS}
        if not values or not values.keys() <= allowed:
            raise ValueError('Unsupported library update')
        for field in ('transcript', 'cuts', 'waveform'):
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
        result = {key: value for key, value in episode.items() if key not in {'feed_url', 'media_url', 'audio', 'cleaned', 'transcript', 'cuts', 'waveform'}}
        result['image'] = _public_image(result.get('image'), episode.get('feed_url'))
        for field in BOOLEAN_FIELDS:
            result[field] = bool(episode.get(field))
        result.update(
            has_audio=bool(episode['audio'] and Path(episode['audio']).is_file()),
            has_cleaned=bool(episode['cleaned'] and Path(episode['cleaned']).is_file()),
            has_transcript=episode['transcript'] is not None,
            ad_count=len(episode['cuts']),
        )
        for field, path_key in (('media_version', 'cleaned'), ('original_media_version', 'audio')):
            try:
                result[field] = str(Path(episode[path_key]).stat().st_mtime_ns) if episode[path_key] else ''
            except OSError:
                result[field] = ''
        if detail:
            result.update(transcript=episode['transcript'], cuts=episode['cuts'])
        return result
