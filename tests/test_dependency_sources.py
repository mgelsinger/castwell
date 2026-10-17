import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location(
    'dependency_sources', Path(__file__).resolve().parents[1] / 'scripts/bundle_dependency_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


class DependencySourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'packaging').mkdir()
        for name in sources.PIN_FILES:
            (self.root / name).write_bytes(name.encode())
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        (self.cache / 'upstream.tar.gz').write_bytes(b'exact upstream source bytes')
        self.data = {
            'schema_version': 1,
            'release_inputs': {name: sources.digest(self.root / name) for name in sources.PIN_FILES},
            'artifacts': [{'id': 'upstream', 'path': 'upstream.tar.gz',
                           'url': 'https://example.org/source.tar.gz', 'bytes': 27,
                           'sha256': sources.digest(self.cache / 'upstream.tar.gz')}],
            'components': [{'id': 'media', 'status': 'source-collected',
                            'basis': 'Pinned source for the shipped media binary.', 'artifacts': ['upstream']}],
            'binary_bindings': [{'path': '_internal/bin/ffmpeg.exe', 'sha256': 'a' * 64}],
            'remaining': [],
        }
        self.lock = self.root / 'packaging/windows-sources.lock.json'
        self.output = self.root / 'sources.zip'
        self.save()

    def save(self):
        self.lock.write_text(json.dumps(self.data), encoding='utf-8')

    def test_offline_round_trip_has_only_reviewed_files_and_is_stable(self):
        (self.cache / 'private.txt').write_bytes(b'must not enter the release')
        first = sources.bundle(self.lock, self.cache, self.output, root=self.root, require_complete=True)
        verified = sources.verify_bundle(self.output, self.lock, self.root, require_complete=True)
        self.assertEqual(first['sha256'], verified['sha256'])
        second = sources.bundle(self.lock, self.cache, self.output, root=self.root, require_complete=True)
        self.assertEqual(first, second)
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any('private' in name for name in archive.namelist()))
            self.assertTrue(json.loads(archive.read(sources.PREFIX + 'manifest.json'))['source_requirements_complete'])

    def test_incomplete_review_can_be_collected_but_cannot_pass_release_gate(self):
        self.data['components'][0].update(status='incomplete', remaining=['Matching codec patch is not available.'])
        self.save()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            sources.bundle(self.lock, self.cache, self.output, root=self.root, require_complete=True)
        self.assertFalse(self.output.exists())
        report = sources.bundle(self.lock, self.cache, self.output, root=self.root)
        self.assertFalse(report['source_requirements_complete'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            sources.verify_bundle(self.output, self.lock, self.root, require_complete=True)

    def test_stale_runtime_and_corrupt_cache_fail_before_archiving(self):
        (self.cache / 'upstream.tar.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            sources.bundle(self.lock, self.cache, self.output, root=self.root)
        self.assertFalse(self.output.exists())
        (self.root / sources.PIN_FILES[0]).write_bytes(b'changed runtime version')
        with self.assertRaisesRegex(ValueError, 'stale'):
            sources.read_lock(self.lock, self.root)

    def test_unsafe_paths_and_duplicate_case_aliases_are_rejected(self):
        for value in ('../private.txt', '/private.txt', 'C:/private.txt', 'a\\b', 'a/./b', 'a//b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sources.safe_relative(value)
        self.data['artifacts'].append(dict(self.data['artifacts'][0], id='alias', path='UPSTREAM.TAR.GZ'))
        self.save()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            sources.read_lock(self.lock, self.root)

    def test_changed_archive_source_bytes_are_rejected(self):
        sources.bundle(self.lock, self.cache, self.output, root=self.root)
        with zipfile.ZipFile(self.output) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members[sources.PREFIX + 'sources/upstream.tar.gz'] = b'x' * 27
        with zipfile.ZipFile(self.output, 'w') as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            sources.verify_bundle(self.output, self.lock, self.root)

    def test_binary_replacement_invalidates_source_correspondence(self):
        app = self.root / 'app'
        binary = app / '_internal/bin/ffmpeg.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'known binary')
        self.data['binary_bindings'][0]['sha256'] = sources.digest(binary)
        sources.verify_binaries(self.data, app)
        binary.write_bytes(b'new binary version')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            sources.verify_binaries(self.data, app)

    def test_source_bound_native_directory_rejects_additional_legacy_dll(self):
        app = self.root / 'app'
        binary = app / '_internal/av.libs/avcodec-controlled.dll'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'controlled codec')
        self.data['binary_bindings'] = [{'path': binary.relative_to(app).as_posix(),
                                        'sha256': sources.digest(binary)}]
        self.data['exclusive_native_directories'] = ['_internal/av.libs']
        (binary.parent / 'COPYING.txt').write_bytes(b'allowed non-native notice')
        sources.verify_binaries(self.data, app)
        unexpected = binary.parent / 'nested/libx264-legacy.DLL'
        unexpected.parent.mkdir()
        unexpected.write_bytes(b'old unreviewed vendor codec')
        with self.assertRaisesRegex(ValueError, 'Unexpected native files in source-bound directory'):
            sources.verify_binaries(self.data, app)
        unexpected.unlink()
        sources.verify_binaries(self.data, app)

    def test_local_build_recipes_are_included_and_pinned(self):
        recipe = self.root / 'packaging/build-media.sh'
        recipe.write_bytes(b'configure and build the known source')
        self.data['artifacts'].append({'id': 'recipe', 'path': 'packaging/build-media.sh',
                                      'kind': 'repository', 'sha256': sources.digest(recipe),
                                      'bytes': recipe.stat().st_size})
        self.save()
        sources.bundle(self.lock, self.cache, self.output, root=self.root)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read(sources.PREFIX + 'sources/packaging/build-media.sh'), recipe.read_bytes())
        recipe.write_bytes(b'changed build instructions')
        with self.assertRaisesRegex(ValueError, 'recipe is missing or changed'):
            sources.read_lock(self.lock, self.root)


if __name__ == '__main__':
    unittest.main()
