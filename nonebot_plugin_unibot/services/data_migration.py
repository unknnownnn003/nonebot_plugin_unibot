"""Offline, copy-and-verify migration. No NoneBot imports or business parsing."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

MARKER = "_unibot_migration_v1.json"


class MigrationError(RuntimeError):
    pass


def _is_link(path):
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    # Covers junctions on Windows versions of Python predating is_junction().
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def inventory(root: Path) -> dict:
    """Include unknown files too; reject links, special files and SQLite journals."""
    result = {}
    if _is_link(root):
        raise MigrationError("Migration roots must not be symbolic links")
    if not root.exists():
        return result
    if not root.is_dir():
        raise MigrationError("Migration root is not a directory")
    for path in sorted(root.rglob("*")):
        if _is_link(path):
            raise MigrationError("Symbolic links require manual review")
        if path.is_dir():
            continue
        if not path.is_file():
            raise MigrationError("Special files require manual review")
        if path.name.endswith(("-wal", "-shm", "-journal")):
            raise MigrationError("SQLite journal found; stop the host and checkpoint safely first")
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        result[path.relative_to(root).as_posix()] = {"bytes": path.stat().st_size, "sha256": checksum.hexdigest()}
    return result


def _roots(source, target):
    source, target = Path(source), Path(target)
    if _is_link(source) or _is_link(target):
        raise MigrationError("Migration roots must not be symbolic links")
    source, target = source.resolve(), target.resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise MigrationError("Source and destination must be separate trees")
    return source, target


def _completed(source, target):
    marker = target / MARKER
    if not marker.exists():
        return None
    if marker.is_symlink():
        raise MigrationError("Invalid migration marker")
    try:
        report = json.loads(marker.read_text(encoding="utf-8"))
        if (report["version"] != 1 or report["state"] != "complete"
                or report["source"] != str(source) or report["target"] != str(target)
                or not isinstance(report["files"], dict)):
            raise ValueError("marker mismatch")
    except (ValueError, KeyError, TypeError) as exc:
        raise MigrationError("Invalid migration marker; review before loading") from exc
    return report


def require_migrated(source, target):
    source, target = _roots(source, target)
    if _completed(source, target) is not None:
        return
    # Startup does not hash/copy data or start a second store silently.
    if source.exists() and next(source.iterdir(), None) is not None:
        raise MigrationError("Legacy UniBot data requires offline migration: python tools/migrate_localstore.py --help")


def plan(source, target):
    source, target = _roots(source, target)
    old = _completed(source, target)
    if old is not None:
        if source.exists() and inventory(source) != old["files"]:
            raise MigrationError("Legacy data changed since migration; review rollback writes before retrying")
        return dict(old, already_complete=True)
    if not source.is_dir():
        raise MigrationError("Legacy source directory does not exist")
    files, existing = inventory(source), inventory(target)
    if MARKER in files:
        raise MigrationError("Reserved migration marker in source")
    conflicts = [name for name in files.keys() & existing.keys() if files[name] != existing[name]]
    if conflicts:
        raise MigrationError(f"Destination has {len(conflicts)} conflicting files; nothing overwritten")
    return dict(version=1, state="planned", source=str(source), target=str(target),
                files=files, existing=existing, file_count=len(files),
                total_bytes=sum(item["bytes"] for item in files.values()))


@contextmanager
def _lock(target):
    lock = target.with_name(f".{target.name}.migration.lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise MigrationError("Migration lock exists; check for running/interrupted migration") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def migrate(source, target):
    """Caller must stop all writers. Originals and previous destination survive."""
    source, target = _roots(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock(target):
        report = plan(source, target)
        if report.get("already_complete"):
            return report
        stage = target.with_name(f".{target.name}.migration-{uuid4().hex}")
        backup = target.with_name(f".{target.name}.before-migration-{uuid4().hex}")
        try:
            if target.exists():
                shutil.copytree(target, stage)
            else:
                stage.mkdir()
            shutil.copytree(source, stage, dirs_exist_ok=True)
            expected = dict(report["existing"], **report["files"])
            if inventory(stage) != expected or inventory(source) != report["files"]:
                raise MigrationError("Source changed or staged copy failed verification")
            if inventory(target) != report["existing"]:
                raise MigrationError("Destination changed during migration")
            report.update(state="complete", completed_at=datetime.now(timezone.utc).isoformat(),
                          previous_target=str(backup) if target.exists() else None)
            (stage / MARKER).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if target.exists():
                target.rename(backup)
            try:
                stage.rename(target)
            except BaseException:
                if backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            return report
        finally:
            # Only this invocation's UUID staging tree, never either data root.
            if stage.exists():
                shutil.rmtree(stage)
