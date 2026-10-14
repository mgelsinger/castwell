"""Artifact boundaries that do not require Windows, CUDA, or a build toolchain."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


@unittest.skipIf(sys.version_info < (3, 11), 'Windows build tooling targets CPython 3.12')
class WindowsArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('castwell_windows_build', Path(__file__).resolve().parents[1] / 'scripts' / 'build_windows.py')
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    def test_portable_marker_only_in_archive_and_no_library(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            app = directory / 'app'
            (app / '_internal').mkdir(parents=True)
            (app / 'Castwell.exe').write_bytes(b'application')
            (app / '_internal' / 'runtime.dll').write_bytes(b'runtime')
            target = directory / 'portable.zip'
            self.build.archive_portable(app, target)
            self.assertFalse((app / 'portable.flag').exists())
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(set(archive.namelist()), {'Castwell/Castwell.exe', 'Castwell/_internal/runtime.dll', 'Castwell/portable.flag'})
                self.assertIn(b'adjacent Data', archive.read('Castwell/portable.flag'))
            first = target.read_bytes()
            self.build.archive_portable(app, target)
            self.assertEqual(target.read_bytes(), first)

    def test_native_cuda_stage_checks_architecture_and_excludes_cpu_variants(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            header = bytearray(64)
            header[:2] = b'MZ'
            struct.pack_into('<I', header, 60, 64)
            for name in ('llama-server.exe', 'ggml-cuda.dll', 'ggml-cpu-x64.dll', 'ggml-cpu-haswell.dll', 'cublasLt64_12.dll'):
                (directory / name).write_bytes(header + b'PE\0\0\x64\x86')
            paths = self.build.native_files(directory, {'llama-server.exe', 'ggml-cuda.dll', 'cublasLt64_12.dll'})
            self.assertEqual({path.name for path in paths}, {'llama-server.exe', 'ggml-cuda.dll', 'ggml-cpu-x64.dll', 'cublasLt64_12.dll'})
            (directory / 'ggml-cuda.dll').write_bytes(header + b'PE\0\0\x4c\x01')
            with self.assertRaisesRegex(ValueError, 'x64'):
                self.build.native_files(directory, {'llama-server.exe', 'ggml-cuda.dll'})

    def test_cpu_only_stage_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / 'llama-server.exe').write_bytes(b'not executed')
            with self.assertRaisesRegex(ValueError, 'ggml-cuda.dll'):
                self.build.native_files(directory, {'llama-server.exe', 'ggml-cuda.dll'})

    def test_unmanaged_recursive_removal_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            sentinel = directory / 'library.db'
            sentinel.write_text('preserve', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                self.build.managed_remove(directory)
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserve')

    def test_source_fingerprint_detects_changes_without_reading_library(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / 'castwell').mkdir()
            source = directory / 'castwell' / 'gpu.py'
            source.write_text('original', encoding='utf-8')
            first = self.build.source_fingerprint(directory)
            (directory / 'data').mkdir()
            (directory / 'data' / 'library.sqlite3').write_text('private', encoding='utf-8')
            self.assertEqual(self.build.source_fingerprint(directory), first)
            source.write_text('updated', encoding='utf-8')
            self.assertNotEqual(self.build.source_fingerprint(directory), first)

    def test_project_terms_change_the_frozen_source_identity(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            prior = self.build.source_fingerprint(directory)
            for name in ('LICENSE', 'NOTICE'):
                path = directory / name
                path.write_text('Project terms', encoding='utf-8')
                added = self.build.source_fingerprint(directory)
                self.assertNotEqual(added, prior, name)
                path.write_text('Updated project terms', encoding='utf-8')
                updated = self.build.source_fingerprint(directory)
                self.assertNotEqual(updated, added, name)
                prior = updated

    def test_missing_or_empty_project_terms_stop_before_build_work(self):
        arguments = ['build_windows.py', '--ffmpeg-dir', 'unused', '--llama-dir', 'unused',
                     '--cuda-dir', 'unused', '--notices-dir', 'unused', '--skip-installer']
        for name in ('LICENSE', 'NOTICE'):
            for contents in (None, b' \n\t'):
                with self.subTest(name=name, contents=contents), tempfile.TemporaryDirectory() as root:
                    directory = Path(root)
                    for other in ('LICENSE', 'NOTICE'):
                        (directory / other).write_bytes(b'Project terms')
                    if contents is None:
                        (directory / name).unlink()
                    else:
                        (directory / name).write_bytes(contents)
                    with (mock.patch.object(self.build, 'ROOT', directory),
                          mock.patch.object(self.build.platform, 'system', return_value='Windows'),
                          mock.patch.object(self.build.struct, 'calcsize', return_value=8),
                          mock.patch.object(self.build.sys, 'version_info', (3, 12)),
                          mock.patch.object(self.build.sys, 'argv', arguments),
                          mock.patch.object(self.build, 'native_files') as native,
                          mock.patch.object(self.build, 'run') as command,
                          mock.patch.object(self.build.subprocess, 'run') as process):
                        with self.assertRaisesRegex(ValueError, 'Missing or empty project distribution file: ' + name):
                            self.build.main()
                        native.assert_not_called()
                        command.assert_not_called()
                        process.assert_not_called()
                    self.assertFalse((directory / '.local').exists())

    def test_project_terms_reach_portable_without_changing_dependency_notices(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            app = directory / 'app'
            notices = app / 'THIRD-PARTY-NOTICES'
            notices.mkdir(parents=True)
            dependency = notices / 'dependency.txt'
            dependency.write_bytes(b'Unchanged dependency terms')
            for name in ('LICENSE', 'NOTICE'):
                (directory / name).write_bytes((name + '\nProject terms\n').encode())
            self.build.bundle_project_notices(app, directory)
            target = directory / 'portable.zip'
            self.build.archive_portable(app, target)
            with zipfile.ZipFile(target) as archive:
                for name in ('LICENSE', 'NOTICE'):
                    self.assertEqual(archive.read('Castwell/' + name), (directory / name).read_bytes())
                self.assertEqual(archive.read('Castwell/THIRD-PARTY-NOTICES/dependency.txt'),
                                 b'Unchanged dependency terms')
            self.assertEqual(dependency.read_bytes(), b'Unchanged dependency terms')

    def test_cuda_deduplication_retains_canonical_and_rejects_mismatches(self):
        with tempfile.TemporaryDirectory() as root:
            app = Path(root)
            canonical = app / '_internal' / 'cuda'
            canonical.mkdir(parents=True)
            (canonical / 'cublas.dll').write_bytes(b'cuda')
            duplicate = app / '_internal' / 'cublas.dll'
            duplicate.write_bytes(b'cuda')
            llama_duplicate = app / '_internal' / 'llama' / 'cublas.dll'
            llama_duplicate.parent.mkdir()
            llama_duplicate.write_bytes(b'cuda')
            (app / '_internal' / 'vcruntime.dll').write_bytes(b'runtime')
            self.assertEqual(self.build.remove_duplicate_cuda_dlls(app), ['cublas.dll', 'llama/cublas.dll'])
            self.assertFalse(duplicate.exists())
            self.assertFalse(llama_duplicate.exists())
            self.assertEqual((canonical / 'cublas.dll').read_bytes(), b'cuda')
            self.assertTrue((app / '_internal' / 'vcruntime.dll').exists())
            duplicate.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'Conflicting CUDA'):
                self.build.remove_duplicate_cuda_dlls(app)
            self.assertTrue(duplicate.exists())

    def test_standalone_guides_preserve_local_links_and_publish_history_links(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / 'docs').mkdir()
            (directory / 'docs' / 'windows.md').write_text(
                '[Production](production.md)\nMaintainers can exercise both artifacts: source command', encoding='utf-8')
            (directory / 'docs' / 'production.md').write_text(
                '[Windows](windows.md) [Model](local-models.md#profile) [Proof](experiment.md)', encoding='utf-8')
            evaluations = directory / 'docs' / 'evaluations'
            evaluations.mkdir()
            for extension in ('md', 'json'):
                (evaluations / ('2026-10-13-five-minute-cuda.' + extension)).write_text('current evidence', encoding='utf-8')
            app = directory / 'app'
            app.mkdir()
            self.build.bundle_guides(app, directory)
            quickstart = (app / 'QUICKSTART.md').read_text(encoding='utf-8')
            self.assertEqual(quickstart, '[Production](production.md)\n')
            self.assertEqual((app / 'windows.md').read_text(encoding='utf-8'), quickstart)
            production = (app / 'production.md').read_text(encoding='utf-8')
            self.assertIn('[Windows](windows.md)', production)
            self.assertIn('https://github.com/mgelsinger/castwell/blob/main/docs/local-models.md#profile', production)
            self.assertIn('https://github.com/mgelsinger/castwell/blob/main/docs/experiment.md', production)
            for extension in ('md', 'json'):
                self.assertEqual((app / 'evaluations' / ('2026-10-13-five-minute-cuda.' + extension)).read_text(encoding='utf-8'), 'current evidence')

    def test_pinned_model_notice_is_required_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            notice = directory / 'Silero-VAD-MIT.txt'
            notice.write_bytes(b'upstream license')
            metadata = {'silero': {'kind': 'notice', 'filename': notice.name,
                                   'sha256': self.build.digest(notice)}}
            self.build.validate_notice_pins(directory, metadata)
            notice.write_bytes(b'different license')
            with self.assertRaisesRegex(ValueError, 'unverified third-party notice'):
                self.build.validate_notice_pins(directory, metadata)
            notice.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing'):
                self.build.validate_notice_pins(directory, metadata)
