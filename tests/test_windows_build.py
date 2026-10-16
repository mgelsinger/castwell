"""Artifact boundaries that do not require Windows, CUDA, or a build toolchain."""
import importlib.util
import json
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

    def test_release_source_refuses_dirty_staged_and_untracked_inputs_before_head_lookup(self):
        for status in (' M castwell/private-name.py\0', 'M  packaging/private-name.spec\0',
                       '?? castwell/private-name.py\0'):
            with self.subTest(status=status), mock.patch.object(self.build.subprocess, 'run') as process:
                process.return_value = mock.Mock(returncode=0, stdout=status)
                with self.assertRaisesRegex(ValueError, 'uncommitted changes') as error:
                    self.build.clean_source_revision(Path('fixture'))
                self.assertNotIn('private-name', str(error.exception))
                self.assertEqual(process.call_count, 1)
                command = process.call_args.args[0]
                self.assertEqual(command[:6], ['git', 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--'])
                for path in ('castwell', 'packaging', 'README.md', 'LICENSE', 'NOTICE',
                             'scripts/build_windows.py', 'scripts/desktop_entry.py',
                             'docs/windows.md', 'docs/third-party-sources.json',
                             'docs/evaluations/2026-10-14-tolerance-comparison.md'):
                    self.assertIn(path, command)
                self.assertNotIn('tests', command)

    def test_release_source_requires_full_revision_and_rejects_head_changes(self):
        revision = 'a' * 40
        for value in (revision + '\n', 'short', 'g' * 40, revision + '\n' + revision):
            with self.subTest(value=value), mock.patch.object(self.build.subprocess, 'run') as process:
                process.side_effect = [mock.Mock(returncode=0, stdout=''), mock.Mock(returncode=0, stdout=value)]
                if value == revision + '\n':
                    self.assertEqual(self.build.clean_source_revision(expected=revision), revision)
                else:
                    with self.assertRaisesRegex(ValueError, 'exact project source revision'):
                        self.build.clean_source_revision()
                self.assertEqual(process.call_args.args[0], ['git', 'rev-parse', '--verify', 'HEAD'])
        with mock.patch.object(self.build.subprocess, 'run') as process:
            process.side_effect = [mock.Mock(returncode=0, stdout=''), mock.Mock(returncode=0, stdout='b' * 40)]
            with self.assertRaisesRegex(ValueError, 'revision changed'):
                self.build.clean_source_revision(expected=revision)

    def test_release_source_verification_errors_hide_git_output(self):
        with mock.patch.object(self.build.subprocess, 'run') as process:
            process.return_value = mock.Mock(returncode=1, stdout='private path', stderr='private credentials')
            with self.assertRaisesRegex(ValueError, '^Cannot verify committed release inputs$'):
                self.build.clean_source_revision()

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
                '[Production](production.md) [Terms](../LICENSE) [Notice](../NOTICE#terms)\n'
                'Maintainers can exercise both artifacts: source command', encoding='utf-8')
            (directory / 'docs' / 'production.md').write_text(
                '[Windows](windows.md) [Sources](third-party-sources.md) '
                '[Model](local-models.md#profile) [Proof](experiment.md)', encoding='utf-8')
            (directory / 'packaging').mkdir()
            (directory / 'packaging' / 'windows-sources.lock.json').write_text('{}', encoding='utf-8')
            (directory / 'docs' / 'third-party-sources.md').write_text(
                '[Source lock](../packaging/windows-sources.lock.json)', encoding='utf-8')
            (directory / 'docs' / 'third-party-sources.json').write_text('{"complete": false}', encoding='utf-8')
            (directory / 'docs' / 'source-history.md').write_text('source history', encoding='utf-8')
            evaluations = directory / 'docs' / 'evaluations'
            evaluations.mkdir()
            for extension in ('md', 'json'):
                (evaluations / ('2026-10-13-five-minute-cuda.' + extension)).write_text('current evidence', encoding='utf-8')
                (evaluations / ('2026-10-14-tolerance-comparison.' + extension)).write_text('tolerance evidence', encoding='utf-8')
            (evaluations / '2026-10-13-five-minute-cuda.md').write_text(
                '[History](../source-history.md#results) [Saved](2026-10-13-saved-cut-validation.json)', encoding='utf-8')
            (evaluations / '2026-10-13-saved-cut-validation.json').write_text('saved cuts', encoding='utf-8')
            app = directory / 'app'
            app.mkdir()
            self.build.bundle_guides(app, directory)
            quickstart = (app / 'QUICKSTART.md').read_text(encoding='utf-8')
            self.assertEqual(quickstart, '[Production](production.md) [Terms](LICENSE) [Notice](NOTICE#terms)\n')
            self.assertEqual((app / 'windows.md').read_text(encoding='utf-8'), quickstart)
            self.assertIn('[Terms](../LICENSE)', (directory / 'docs' / 'windows.md').read_text(encoding='utf-8'))
            production = (app / 'production.md').read_text(encoding='utf-8')
            self.assertIn('[Windows](windows.md)', production)
            self.assertIn('[Sources](third-party-sources.md)', production)
            self.assertIn('https://github.com/mgelsinger/castwell/blob/main/docs/local-models.md#profile', production)
            self.assertIn('https://github.com/mgelsinger/castwell/blob/main/docs/experiment.md', production)
            for extension in ('md', 'json'):
                self.assertEqual((app / 'evaluations' / ('2026-10-14-tolerance-comparison.' + extension)).read_text(encoding='utf-8'), 'tolerance evidence')
            evidence = (app / 'evaluations' / '2026-10-13-five-minute-cuda.md').read_text(encoding='utf-8')
            self.assertIn('https://github.com/mgelsinger/castwell/blob/main/docs/source-history.md#results', evidence)
            self.assertIn('[Saved](2026-10-13-saved-cut-validation.json)', evidence)
            self.assertEqual((app / 'evaluations' / '2026-10-13-five-minute-cuda.json').read_text(encoding='utf-8'), 'current evidence')
            self.assertIn('[History](../source-history.md#results)',
                          (evaluations / '2026-10-13-five-minute-cuda.md').read_text(encoding='utf-8'))
            self.assertEqual((app / 'evaluations' / '2026-10-13-saved-cut-validation.json').read_text(encoding='utf-8'), 'saved cuts')
            self.assertEqual((app / 'third-party-sources.md').read_text(encoding='utf-8'),
                             '[Source lock](https://github.com/mgelsinger/castwell/blob/main/packaging/windows-sources.lock.json)')
            self.assertEqual((app / 'third-party-sources.json').read_bytes(),
                             (app / 'THIRD-PARTY-NOTICES' / 'source-inventory.json').read_bytes())
            revision = 'a' * 40
            self.build.bundle_guides(app, directory, source_ref=revision)
            production = (app / 'production.md').read_text(encoding='utf-8')
            self.assertIn('https://github.com/mgelsinger/castwell/blob/' + revision + '/docs/experiment.md', production)
            evidence = (app / 'evaluations' / '2026-10-13-five-minute-cuda.md').read_text(encoding='utf-8')
            self.assertIn('https://github.com/mgelsinger/castwell/blob/' + revision + '/docs/source-history.md#results', evidence)
            self.assertNotIn('/blob/main/', production + evidence)
            self.assertIn('/blob/' + revision + '/packaging/windows-sources.lock.json',
                          (app / 'third-party-sources.md').read_text(encoding='utf-8'))

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

    def test_freezer_path_excludes_unrelated_inherited_app_paths(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            python = directory / 'venv/Scripts/python.exe'
            stage = directory / 'vendor/vc-runtime'
            system = directory / 'Windows'
            with mock.patch.dict(self.build.os.environ, {'PATH': 'unrelated-app-runtime'}):
                path = self.build.isolated_build_path(python, [stage, stage], system)
            self.assertNotIn('unrelated-app-runtime', path)
            self.assertEqual(path.split(self.build.os.pathsep).count(str(stage.resolve())), 1)
            self.assertIn(str((system / 'System32').resolve()), path)

    def test_collected_compiler_runtime_uses_reviewed_bytes_everywhere(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            source = directory / 'vcruntime140.dll'
            source.write_bytes(b'reviewed runtime')
            app = directory / 'app'
            nested = app / '_internal/native/vcruntime140.dll'
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b'incidental search-path runtime')
            replacements = self.build.normalize_vc_runtime(app, [source])
            self.assertEqual(set(replacements), {'vcruntime140.dll', 'native/vcruntime140.dll'})
            self.assertEqual(nested.read_bytes(), source.read_bytes())
            self.assertEqual((app / '_internal/vcruntime140.dll').read_bytes(), source.read_bytes())

    def pyav_receipt_fixture(self, directory):
        root = directory / 'source'
        (root / 'packaging').mkdir(parents=True)
        (root / 'scripts').mkdir()
        filename = 'av-16.1.0+castwell1-cp311-abi3-win_amd64.whl'
        (root / 'packaging/windows-pyav.lock.json').write_text(json.dumps({
            'version': '16.1.0+castwell1', 'wheel_filename': filename}), encoding='utf-8')
        files = {'lock_sha256': root / 'packaging/windows-pyav.lock.json',
                 'recipe_sha256': root / 'scripts/build_windows_pyav.py',
                 'media_lock_sha256': root / 'packaging/windows-media.lock.json',
                 'media_recipe_sha256': root / 'scripts/build_windows_media.py'}
        for key, path in files.items():
            if key != 'lock_sha256':
                path.write_bytes(key.encode())
        output = directory / 'build'
        output.mkdir()
        wheel = output / filename
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('av-16.1.0+castwell1.dist-info/METADATA',
                             'Name: av\nVersion: 16.1.0+castwell1\n')
        receipt = output / 'build-manifest.json'
        data = {'schema_version': 1, 'name': 'av', 'version': '16.1.0+castwell1',
                **{key: self.build.digest(path) for key, path in files.items()},
                'wheel': {'filename': filename, 'relative_path': filename,
                          'sha256': self.build.digest(wheel), 'bytes': wheel.stat().st_size}}
        receipt.write_text(json.dumps(data), encoding='utf-8')
        return root, receipt, wheel, data, files

    def test_source_built_pyav_receipt_accepts_only_matching_reviewed_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, receipt, wheel, data, files = self.pyav_receipt_fixture(Path(temporary))
            checked, path = self.build.verify_pyav_build(receipt, root)
            self.assertEqual(checked, data)
            self.assertEqual(path, wheel.resolve())
            for key, source in files.items():
                original = source.read_bytes()
                source.write_bytes(original + b'\n')
                with self.subTest(input=key), self.assertRaisesRegex(ValueError, 'receipt is stale: ' + key):
                    self.build.verify_pyav_build(receipt, root)
                source.write_bytes(original)

    def test_source_built_pyav_receipt_rejects_wrong_schema_name_or_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, receipt, wheel, data, files = self.pyav_receipt_fixture(Path(temporary))
            for key, value in (('schema_version', 2), ('name', 'other'), ('version', '16.1.0')):
                changed = dict(data, **{key: value})
                receipt.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'Unexpected source-built PyAV identity'):
                    self.build.verify_pyav_build(receipt, root)

    def test_source_built_pyav_wheel_requires_exact_bytes_and_owned_receipt_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, receipt, wheel, data, files = self.pyav_receipt_fixture(Path(temporary))
            original = wheel.read_bytes()
            wheel.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, 'wheel is missing or changed'):
                self.build.verify_pyav_build(receipt, root)
            wheel.write_bytes(original)
            external = receipt.parent.parent / wheel.name
            external.write_bytes(original)
            for values in ({'relative_path': '../' + wheel.name}, {'bytes': len(original) + 1},
                           {'sha256': '0' * 64}, {'filename': 'different.whl'}):
                changed = dict(data, wheel=dict(data['wheel'], **values))
                receipt.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(change=values), self.assertRaisesRegex(ValueError, 'wheel is missing or changed'):
                    self.build.verify_pyav_build(receipt, root)
            receipt.write_text(json.dumps(data), encoding='utf-8')
            wheel.unlink()
            with self.assertRaisesRegex(ValueError, 'wheel is missing or changed'):
                self.build.verify_pyav_build(receipt, root)

    def test_source_built_pyav_rejects_metadata_mismatch_or_multiple_metadata_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, receipt, wheel, data, files = self.pyav_receipt_fixture(Path(temporary))
            for name, version, duplicate, error in (
                    ('other', data['version'], False, 'identity differs'),
                    ('av', '16.1.0', False, 'identity differs'),
                    ('av', data['version'], True, 'metadata is ambiguous')):
                with zipfile.ZipFile(wheel, 'w') as archive:
                    archive.writestr('av.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
                    if duplicate:
                        archive.writestr('other.dist-info/METADATA', 'Name: other\nVersion: 1\n')
                changed = dict(data, wheel=dict(data['wheel'], bytes=wheel.stat().st_size,
                                               sha256=self.build.digest(wheel)))
                receipt.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(name=name, version=version, duplicate=duplicate), self.assertRaisesRegex(ValueError, error):
                    self.build.verify_pyav_build(receipt, root)
            for headers in ('Version: 16.1.0+castwell1\n',
                            'Name: av\nName: other\nVersion: 16.1.0+castwell1\n',
                            'Name: av\nVersion: 16.1.0+castwell1\nVersion: 0\n'):
                with zipfile.ZipFile(wheel, 'w') as archive:
                    archive.writestr('av.dist-info/METADATA', headers)
                changed = dict(data, wheel=dict(data['wheel'], bytes=wheel.stat().st_size,
                                               sha256=self.build.digest(wheel)))
                receipt.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, 'metadata is ambiguous'):
                    self.build.verify_pyav_build(receipt, root)

    def test_source_built_pyav_must_remain_separate_from_upstream_wheel_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / 'upstream.lock'
            lock.write_text('av==16.1.0 --hash=sha256:' + '0' * 64 + '\n', encoding='utf-8')
            with mock.patch.object(self.build, 'LOCK', lock):
                with self.assertRaisesRegex(ValueError, 'separate from the upstream wheel lock'):
                    self.build.wheel_notices(directory, directory / 'notices', {
                        'name': 'av', 'version': '16.1.0+castwell1', 'wheel': {'sha256': '1' * 64}})
