"""Actual filesystem races in the Windows distribution harness's cleanup."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


class WindowsDistributionCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'smoke_windows_distribution.py'
        spec = importlib.util.spec_from_file_location('castwell_distribution_smoke', script)
        cls.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.smoke)

    def workspace(self):
        return tempfile.TemporaryDirectory(prefix=self.smoke.WORKSPACE_PREFIX)

    @contextmanager
    def after_first_scandir(self, action):
        """Cache real DirEntry metadata, then mutate the real tree on scan exit."""
        original = os.scandir
        invoked = False

        def raced_scan(*args, **kwargs):
            nonlocal invoked
            first = not invoked
            invoked = True
            with original(*args, **kwargs) as stream:
                entries = list(stream)
                for entry in entries:
                    entry.stat(follow_symlinks=False)

            class CachedScan:
                def __init__(self):
                    self.entries = iter(entries)
                    self.closed = False

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self.entries)

                def __enter__(self):
                    return self

                def __exit__(self, *_failure):
                    self.close()

                def close(self):
                    if not self.closed:
                        self.closed = True
                        if first:
                            action()

            return CachedScan()

        with mock.patch.object(os, 'scandir', raced_scan):
            yield
        self.assertTrue(invoked, 'The race must happen during a real directory scan')

    def test_disappearing_installer_directory_reproduces_and_is_tolerated(self):
        # Python 3.13+ itself ignores missing descendants. The hosted 3.12
        # failure is reproduced below on the affected interpreter versions.
        if sys.version_info < (3, 13):
            with self.workspace() as temporary:
                root = Path(temporary)
                installed = root / 'Installed app with spaces'
                installed.mkdir()
                with self.after_first_scandir(installed.rmdir):
                    with self.assertRaises(FileNotFoundError):
                        shutil.rmtree(root)
                self.assertFalse(installed.exists())
                self.assertTrue(root.exists())

        with self.workspace() as temporary:
            root = Path(temporary)
            installed = root / 'Installed app with spaces'
            installed.mkdir()
            with self.after_first_scandir(installed.rmdir):
                self.smoke.cleanup_owned_workspace(root)
            self.assertFalse(root.exists())

    def test_other_filesystem_errors_propagate_and_preserve_remaining_file(self):
        with self.workspace() as temporary:
            root = Path(temporary)
            late_file = root / 'late-file.txt'
            with self.after_first_scandir(lambda: late_file.write_text('Keep this failure visible.', encoding='utf-8')):
                with self.assertRaises(OSError) as failure:
                    self.smoke.cleanup_owned_workspace(root)
            self.assertNotIsInstance(failure.exception, FileNotFoundError)
            self.assertEqual(late_file.read_text(encoding='utf-8'), 'Keep this failure visible.')

    def test_permission_error_is_not_swallowed(self):
        with self.workspace() as temporary:
            root = Path(temporary)
            sentinel = root / 'preserve.txt'
            sentinel.write_text('Preserved.', encoding='utf-8')
            error = PermissionError('Access is denied')
            with mock.patch.object(os, 'scandir', side_effect=error):
                with self.assertRaises(PermissionError) as failure:
                    self.smoke.cleanup_owned_workspace(root)
            self.assertIs(failure.exception, error)
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'Preserved.')

    def test_already_removed_owned_root_is_tolerated(self):
        with self.workspace() as temporary:
            root = Path(temporary)
            root.rmdir()
            self.smoke.cleanup_owned_workspace(root)
            self.assertFalse(root.exists())

    def test_nested_or_unrelated_directory_is_rejected(self):
        with self.workspace() as temporary:
            nested = Path(temporary) / self.smoke.WORKSPACE_PREFIX
            nested.mkdir()
            with self.assertRaises(AssertionError):
                self.smoke.cleanup_owned_workspace(nested)
            self.assertTrue(nested.is_dir())
        with tempfile.TemporaryDirectory(prefix='Unrelated cleanup ') as temporary:
            root = Path(temporary)
            with self.assertRaises(AssertionError):
                self.smoke.cleanup_owned_workspace(root)
            self.assertTrue(root.is_dir())


if __name__ == '__main__':
    unittest.main()
