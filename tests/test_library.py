"""Durability, migration, and privacy contracts for the local podcast library."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from castwell.library import Library


def entry(episode_id='one', **values):
    return {
        'id': episode_id, 'title': 'Episode one', 'podcast': 'The telescope',
        'feed_url': 'https://podcasts.example/feed.xml',
        'media_url': 'https://podcasts.example/one.mp3',
        'image': 'https://podcasts.example/cover.jpg',
        'published': '2026-10-03T12:00:00+00:00', 'duration': 120.0,
        **values,
    }


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = Library(self.root / 'library')

    def test_existing_v02_library_migrates_without_losing_audio_or_cuts(self):
        legacy_root = self.root / 'legacy'
        legacy_root.mkdir()
        original = legacy_root / 'original.mp3'
        original.write_bytes(b'original recording')
        cuts = [{'start': 10, 'end': 20, 'approved': True}]
        transcript = {'duration': 120, 'segments': []}
        with closing(sqlite3.connect(legacy_root / 'library.sqlite3')) as db, db:
            db.execute('''CREATE TABLE episodes (
                id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available', error TEXT,
                progress TEXT NOT NULL DEFAULT '', audio TEXT, cleaned TEXT,
                transcript TEXT, cuts TEXT NOT NULL DEFAULT '[]',
                duration REAL NOT NULL DEFAULT 0, removed_seconds REAL NOT NULL DEFAULT 0
            )''')
            db.execute('''INSERT INTO episodes (id,metadata,status,audio,transcript,cuts,duration)
                VALUES (?,?,?,?,?,?,?)''', ('one', json.dumps(entry()), 'review', str(original),
                json.dumps(transcript), json.dumps(cuts), 120))
            db.execute('INSERT INTO episodes (id,metadata) VALUES (?,?)',
                ('local', json.dumps(entry('local', feed_url=''))))
            db.execute('INSERT INTO episodes (id,metadata,status,progress) VALUES (?,?,?,?)',
                ('cleared', json.dumps(entry('cleared', feed_url='')), 'review', 'Cut changes saved. Export to apply them.'))
            db.execute('INSERT INTO episodes (id,metadata,status,progress) VALUES (?,?,?,?)',
                ('transcribed', json.dumps(entry('transcribed', feed_url='')), 'ready', 'Transcript ready'))

        migrated = Library(legacy_root)
        episode = migrated.get('one')
        self.assertEqual(episode['status'], 'review')
        self.assertEqual(episode['audio'], str(original))
        self.assertEqual(episode['transcript'], transcript)
        self.assertEqual(episode['cuts'], cuts)
        self.assertFalse(episode['favorite'])
        self.assertFalse(episode['analysis_done'])
        self.assertEqual(episode['position'], 0)
        self.assertIsNone(episode['waveform'])
        self.assertEqual(original.read_bytes(), b'original recording')
        feeds = migrated.feeds()
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]['episode_count'], 1)
        self.assertEqual(episode['feed_id'], feeds[0]['id'])
        self.assertIsNone(migrated.get('local')['feed_id'])
        self.assertTrue(migrated.get('cleared')['analysis_done'])
        self.assertFalse(migrated.get('transcribed')['analysis_done'])

        migrated.delete_feed(feeds[0]['id'])
        reopened = Library(legacy_root)
        self.assertEqual(reopened.feeds(), [])
        self.assertEqual(reopened.get('one')['cuts'], cuts)

    def test_refresh_is_idempotent_and_preserves_listening_and_processing_state(self):
        self.assertEqual(self.library.import_entries([entry()]), 1)
        audio = self.root / 'original.mp3'
        audio.write_bytes(b'keep me')
        cuts = [{'start': 10, 'end': 20, 'approved': True, 'source': 'manual'}]
        waveform = {'duration': 120, 'peaks': [0.2, 1.0, 0.3]}
        self.library.update('one', audio=str(audio), cuts=cuts, analysis_done=True,
            status='review', waveform=waveform, duration=118)
        self.library.set_episode_flags('one', favorite=True, played=True, archived=True, position=42.5)
        self.assertEqual(self.library.import_entries([
            entry(title='Corrected title', media_url='https://podcasts.example/one.mp3?new=signed', duration=999),
            entry(title='Corrected title', media_url='https://podcasts.example/one.mp3?new=signed', duration=999),
        ]), 0)
        reopened = Library(self.root / 'library')
        record = reopened.get('one')
        self.assertEqual(record['title'], 'Corrected title')
        self.assertEqual(record['media_url'], 'https://podcasts.example/one.mp3?new=signed')
        self.assertEqual(record['duration'], 118)
        self.assertEqual(record['position'], 42.5)
        self.assertEqual(record['status'], 'review')
        self.assertEqual(record['waveform'], waveform)
        self.assertEqual(record['cuts'], cuts)
        self.assertEqual(record['audio'], str(audio))
        self.assertEqual(audio.read_bytes(), b'keep me')
        for flag in ('favorite', 'played', 'archived', 'analysis_done'):
            self.assertIs(record[flag], True)
            self.assertIs(reopened.public(record)[flag], True)
        self.assertEqual(len(reopened.all()), 1)
        self.assertEqual(reopened.feeds()[0]['episode_count'], 1)

    def test_subscriptions_deduplicate_and_keep_credentials_private(self):
        url = 'https://premium-user:premium-secret@podcasts.example/private/feed-token?token=query-secret'
        image = 'https://podcasts.example/cover?token=query-secret'
        feed = self.library.add_feed(url, 'Subscriber podcast', image)
        self.library.add_feed(url)
        self.assertEqual(feed['id'], hashlib.sha256(url.encode()).hexdigest()[:24])
        self.library.import_entries([entry(feed_url=url, image=image)])
        self.library.mark_feed(feed['id'], error='Connection failed for ' + url, last_checked='2026-10-03')
        private = self.library.feeds(public=False)
        self.assertEqual(private[0]['url'], url)
        self.assertEqual(self.library.get_feed(feed['id'])['url'], url)
        public = self.library.feeds()
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]['url_display'], 'podcasts.example')
        self.assertEqual(public[0]['episode_count'], 1)
        self.assertEqual(public[0]['last_checked'], '2026-10-03')
        self.assertEqual(public[0]['image'], '')
        serialized = json.dumps(public) + json.dumps(self.library.public(self.library.get('one'), detail=True))
        for secret in ('premium-user', 'premium-secret', 'feed-token', 'query-secret', 'feed_url', 'media_url'):
            self.assertNotIn(secret, serialized)

    def test_local_import_does_not_create_subscription_and_flags_validate(self):
        self.library.import_entries([entry(feed_url='', media_url='')])
        self.assertEqual(self.library.feeds(), [])
        for values in ({'favorite': 'true'}, {'archived': 1}, {'position': -1},
                {'position': float('nan')}, {'position': float('inf')}, {'position': True}, {'status': 'ready'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.library.set_episode_flags('one', **values)
        self.assertEqual(self.library.get('one')['position'], 0)
        self.assertFalse(self.library.get('one')['favorite'])

    def test_unsubscribe_keeps_episodes_and_can_be_readded(self):
        self.library.import_entries([entry(), entry('two')])
        feed = self.library.feeds()[0]
        self.library.save_revision('one', [], 'Original')
        self.library.delete_feed(feed['id'])
        self.assertEqual(len(self.library.all()), 2)
        self.assertEqual(len(self.library.revisions('one')), 1)
        self.assertEqual(Library(self.root / 'library').feeds(), [])
        restored = self.library.add_feed(entry()['feed_url'])
        self.assertEqual(restored['id'], feed['id'])
        self.assertEqual(restored['episode_count'], 2)

    def test_cut_history_is_bounded_per_episode_and_revisions_are_isolated(self):
        self.library.import_entries([entry(), entry('two')])
        other = self.library.save_revision('two', [], 'Other episode')
        revisions = []
        for index in range(35):
            cuts = [{'start': index, 'end': index + 1, 'approved': True}]
            revisions.append(self.library.save_revision('one', cuts, f'Edit {index}'))
            cuts[0]['start'] = 999
        stored = self.library.revisions('one')
        self.assertEqual(len(stored), 30)
        self.assertEqual([revision['reason'] for revision in stored], [f'Edit {i}' for i in range(34, 4, -1)])
        self.assertEqual(stored[0]['cuts'][0]['start'], 34)
        self.assertEqual(self.library.revision('one', revisions[-1]['id']), stored[0])
        self.assertEqual(self.library.revision('two', other['id'])['reason'], 'Other episode')
        with self.assertRaises(KeyError):
            self.library.revision('one', other['id'])
        with self.assertRaises(KeyError):
            self.library.revision('one', revisions[0]['id'])
        self.assertEqual(self.library.get('one')['cuts'], [])

    def test_settings_merge_and_survive_restart(self):
        self.assertEqual(self.library.get_settings(), {})
        self.library.update_settings({'model': 'small', 'auto_refresh': False})
        self.assertEqual(self.library.update_settings({'model': 'medium'}), {'model': 'medium', 'auto_refresh': False})
        self.assertEqual(Library(self.root / 'library').get_settings(), {'model': 'medium', 'auto_refresh': False})
        with self.assertRaises(ValueError):
            self.library.update_settings({'invalid': float('nan')})
        self.assertNotIn('invalid', self.library.get_settings())

    def test_gallery_summaries_exclude_transcripts_waveforms_and_private_paths(self):
        self.library.import_entries([entry()])
        self.library.update('one', transcript={'segments': [{'text': 'private spoken words'}]},
            waveform={'duration': 120, 'peaks': [0.5] * 700}, cuts=[{'start': 2, 'end': 4}])
        summary = self.library.summaries()[0]
        self.assertTrue(summary['has_transcript'])
        self.assertEqual(summary['ad_count'], 1)
        for field in ('transcript', 'waveform', 'cuts', 'audio', 'cleaned', 'feed_url', 'media_url'):
            self.assertNotIn(field, summary)
        self.assertNotIn('private spoken words', json.dumps(summary))

    def test_recent_listening_time_is_durable_and_unrelated_flags_do_not_change_it(self):
        self.library.import_entries([entry()])
        self.assertIsNone(self.library.get('one')['last_played'])
        self.library.update('one', position=12, last_played='2026-10-03T12:00:00+00:00')
        played_at = self.library.get('one')['last_played']
        self.assertTrue(played_at)
        self.library.set_episode_flags('one', favorite=True, archived=True)
        self.assertEqual(Library(self.root / 'library').get('one')['last_played'], played_at)
        self.assertEqual(played_at, '2026-10-03T12:00:00+00:00')

    def test_backup_captures_committed_wal_data_without_altering_live_library(self):
        self.library.import_entries([entry()])
        # An open reader keeps WAL pages available so copying only the live
        # database file would not be an adequate backup.
        reader = sqlite3.connect(self.library.db)
        self.addCleanup(reader.close)
        reader.execute('BEGIN')
        reader.execute('SELECT count(*) FROM episodes').fetchone()
        self.library.set_episode_flags('one', favorite=True, position=33)
        self.library.save_revision('one', [], 'Before first edit')
        self.library.update_settings({'model': 'small'})
        target = self.root / 'snapshot' / 'library.sqlite3'
        self.assertEqual(self.library.backup(target), target)
        with closing(sqlite3.connect(target)) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(backup.execute('SELECT favorite,position FROM episodes').fetchone(), (1, 33))
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM cut_revisions').fetchone()[0], 1)
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM feeds').fetchone()[0], 1)
        self.assertTrue(self.library.get('one')['favorite'])
        self.assertEqual(Library(target.parent).get_settings(), {'model': 'small'})
        self.assertEqual(list(target.parent.glob('*.part')), [])
        with self.assertRaises(ValueError):
            self.library.backup(self.library.db)

    def test_missing_records_and_unsupported_updates_fail_explicitly(self):
        for operation in (
            lambda: self.library.get('missing'),
            lambda: self.library.update('missing', favorite=True),
            lambda: self.library.get_feed('missing'),
            lambda: self.library.mark_feed('missing', error=None),
            lambda: self.library.delete_feed('missing'),
            lambda: self.library.save_revision('missing', [], 'No episode'),
            lambda: self.library.revisions('missing'),
        ):
            with self.assertRaises(KeyError):
                operation()
        for operation in (
            lambda: self.library.update('missing', metadata={}),
            lambda: self.library.mark_feed('missing', url='https://example.com'),
            lambda: self.library.add_feed('file:///etc/passwd'),
        ):
            with self.assertRaises(ValueError):
                operation()


if __name__ == '__main__':
    unittest.main()
