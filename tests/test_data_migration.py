import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("migration", Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/services/data_migration.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / "legacy", self.root / "store"
        (self.source / "score").mkdir(parents=True)
        (self.source / "songlist.json").write_bytes(b'{"songs":[]}')
        (self.source / "score/fixture.json").write_bytes(b'[{"score":1009000}]')
        (self.source / "empty").mkdir()

    def test_plan_is_read_only_and_complete(self):
        report = m.plan(self.source, self.target)
        self.assertEqual(report["file_count"], 2)
        self.assertFalse(self.target.exists())
        self.assertEqual(report["files"], m.inventory(self.source))

    def test_copy_verified_originals_retained_and_rerun_preserves_new_writes(self):
        before = m.inventory(self.source)
        result = m.migrate(self.source, self.target)
        self.assertEqual(before, m.inventory(self.source))
        self.assertEqual(before, {k: v for k, v in m.inventory(self.target).items() if k != m.MARKER})
        self.assertTrue((self.target / "empty").is_dir())
        m.require_migrated(self.source, self.target)
        (self.target / "songlist.json").write_bytes(b'new runtime data')
        self.assertTrue(m.migrate(self.source, self.target)["already_complete"])
        self.assertEqual((self.target / "songlist.json").read_bytes(), b'new runtime data')
        self.assertEqual(result["state"], "complete")

    def test_existing_receipts_and_identical_files_preserved_with_backup(self):
        self.target.mkdir()
        (self.target / "songlist.json").write_bytes((self.source / "songlist.json").read_bytes())
        with closing(sqlite3.connect(self.target / "native_commands.sqlite3")) as db, db:
            db.execute("CREATE TABLE fixture (value TEXT)")
            db.execute("INSERT INTO fixture VALUES ('receipt')")
        before = m.inventory(self.target)
        report = m.migrate(self.source, self.target)
        self.assertEqual(m.inventory(Path(report["previous_target"])), before)
        with closing(sqlite3.connect(self.target / "native_commands.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT value FROM fixture").fetchone()[0], "receipt")

    def test_legacy_writes_after_rollback_require_review(self):
        m.migrate(self.source, self.target)
        before = m.inventory(self.target)
        (self.source / 'score/fixture.json').write_bytes(b'new scores after rollback')
        with self.assertRaisesRegex(m.MigrationError, 'Legacy data changed'):
            m.migrate(self.source, self.target)
        self.assertEqual(m.inventory(self.target), before)

    def test_conflicting_target_rejected_without_writes(self):
        self.target.mkdir()
        (self.target / "songlist.json").write_bytes(b'current data')
        before = m.inventory(self.target)
        with self.assertRaises(m.MigrationError):
            m.migrate(self.source, self.target)
        self.assertEqual(m.inventory(self.target), before)
        self.assertFalse(list(self.root.glob('.*migration*')))

    def test_incomplete_startup_and_bad_marker_fail_closed(self):
        with self.assertRaises(m.MigrationError):
            m.require_migrated(self.source, self.target)
        self.target.mkdir()
        (self.target / m.MARKER).write_text('{}')
        with self.assertRaises(m.MigrationError):
            m.require_migrated(self.source, self.target)

    def test_fresh_install_without_legacy_is_allowed(self):
        m.require_migrated(self.root / 'absent', self.target)

    def test_nested_or_same_roots_rejected(self):
        for target in (self.source, self.source / 'child', self.root):
            with self.assertRaises(m.MigrationError):
                m.plan(self.source, target)

    def test_link_to_external_data_is_not_copied(self):
        external = self.root / 'private.txt'
        external.write_text('do not follow')
        try:
            (self.source / 'link.txt').symlink_to(external)
        except OSError:
            self.skipTest('Symbolic link creation is unavailable for this account')
        with self.assertRaises(m.MigrationError):
            m.migrate(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_windows_reparse_attributes_rejected_without_is_junction_api(self):
        path = Mock(spec=['is_symlink', 'lstat'])
        path.is_symlink.return_value = False
        path.lstat.return_value.st_file_attributes = 0x400
        self.assertTrue(m._is_link(path))

    def test_sqlite_journal_rejected(self):
        (self.source / 'native_jobs.sqlite3-wal').write_bytes(b'wal')
        with self.assertRaises(m.MigrationError):
            m.migrate(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_partial_copy_never_published(self):
        original = m.shutil.copytree
        def failing_copy(src, dst, *args, **kwargs):
            original(src, dst, *args, **kwargs)
            raise OSError('fixture disk failure')
        with patch.object(m.shutil, 'copytree', side_effect=failing_copy):
            with self.assertRaises(OSError):
                m.migrate(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(len(m.inventory(self.source)), 2)
        self.assertFalse(list(self.root.glob('.*migration*')))

    def test_source_changed_during_copy_rejected(self):
        original = m.shutil.copytree
        def changing_copy(src, dst, *args, **kwargs):
            result = original(src, dst, *args, **kwargs)
            (self.source / 'songlist.json').write_bytes(b'changed')
            return result
        with patch.object(m.shutil, 'copytree', side_effect=changing_copy):
            with self.assertRaises(m.MigrationError):
                m.migrate(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_publication_failure_restores_previous_target(self):
        self.target.mkdir()
        (self.target / 'existing').write_bytes(b'keep')
        before = m.inventory(self.target)
        original = Path.rename
        def fail_stage(path, target):
            if '.migration-' in path.name:
                raise OSError('fixture publication failure')
            return original(path, target)
        with patch.object(Path, 'rename', fail_stage):
            with self.assertRaises(OSError):
                m.migrate(self.source, self.target)
        self.assertEqual(m.inventory(self.target), before)

    def test_other_migration_lock_not_removed(self):
        lock = self.root / '.store.migration.lock'
        lock.write_text('other process')
        with self.assertRaises(m.MigrationError):
            m.migrate(self.source, self.target)
        self.assertEqual(lock.read_text(), 'other process')


if __name__ == '__main__':
    unittest.main()
