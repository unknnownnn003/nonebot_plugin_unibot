"""Read-only physical inventory of JP HDD files and archive members."""
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from hashlib import sha256
from pathlib import Path, PurePosixPath
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
from zipfile import ZipFile, BadZipFile

BITMAPS = {'.dds', '.png', '.jpg', '.jpeg', '.bmp', '.tga', '.gif', '.webp', '.tif', '.tiff'}
NAME_SIGNAL = re.compile(r'(?:chu_(?:info|banner)|music.?add|ultima|world.?s.?end|release|notice|update)', re.I)
OPAQUE = {'.srd', '.svo', '.uvc', '.afb', '.bin', '.enc', '.texlist', '.usm', '.led', '.rfz', '.emtbytes'}
LIMIT = 64 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_root (cabinet TEXT PRIMARY KEY, root TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resource (
    cabinet TEXT, path TEXT, member TEXT, ordinal INTEGER,
    volume TEXT, package TEXT, scope TEXT, folder TEXT, extension TEXT,
    bytes INTEGER, mtime INTEGER, crc INTEGER, encrypted INTEGER, flags INTEGER,
    PRIMARY KEY(cabinet,path,member,ordinal)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS digest (
    cabinet TEXT, path TEXT, member TEXT, ordinal INTEGER,
    bytes INTEGER, mtime INTEGER, crc INTEGER, sha256 TEXT NOT NULL,
    PRIMARY KEY(cabinet,path,member,ordinal)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS digest_hash ON digest(sha256);
CREATE TABLE IF NOT EXISTS inventory_issue (cabinet TEXT, path TEXT, issue TEXT);
"""


def locators(path, member):
    parts = PurePosixPath(path).parts
    content = PurePosixPath(member).parts if member else parts[1:]
    package = next((match[1] for part in content
                    if (match := re.fullmatch(r'(?:SDHD_)?(A\d{3})(?:_\d{14}_0)?', part))), '')
    scope = ('custom' if '自制包' in parts else 'option' if package and package != 'A000'
             or 'option' in parts else 'base' if package == 'A000' else 'other')
    parents = content[:-1]
    folder = next((parents[i + 1] for i, part in enumerate(parents[:-1])
                   if re.fullmatch(r'(?:SDHD_)?A\d{3}(?:_\d{14}_0)?', part)),
                  parents[-1] if parents else '')
    return parts[0], package, scope, folder.casefold()


def row(cabinet, path, size, mtime, member='', ordinal=-1, crc=-1, encrypted=-1):
    ext = PurePosixPath(member or path).suffix.casefold()
    name = PurePosixPath(member or path).stem
    flags = (ext in BITMAPS) + 2 * bool(NAME_SIGNAL.search(name)) + 4 * (ext in OPAQUE)
    return cabinet, path, member, ordinal, *locators(path, member), ext, size, mtime, crc, encrypted, flags


def walk(cabinet, root, issue):
    if not root.is_dir():
        raise ValueError(f'Missing HDD root: {root}')
    for directory, folders, files in os.walk(root, onerror=lambda exc: issue(exc.filename, type(exc).__name__)):
        parent = Path(directory)
        folders[:] = sorted(name for name in folders if not (parent / name).is_symlink()
                            and not (parent / name).is_junction())
        for name in sorted(files):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            try:
                if path.is_symlink():
                    issue(rel, 'symlink_skipped')
                    continue
                stat = path.stat()
                yield row(cabinet, rel, stat.st_size, stat.st_mtime_ns)
                if path.suffix.casefold() == '.zip':
                    with ZipFile(path) as archive:
                        for entry in archive.infolist():
                            if not entry.is_dir():
                                yield row(cabinet, rel, entry.file_size, stat.st_mtime_ns, entry.filename,
                                          entry.header_offset, entry.CRC, entry.flag_bits & 1)
                elif path.suffix.casefold() == '.rar':
                    tar = shutil.which('tar.exe') or shutil.which('bsdtar')
                    if not tar:
                        issue(rel, 'rar_listing_unavailable')
                        continue
                    result = subprocess.run([tar, '-tf', str(path)], capture_output=True, check=False)
                    if result.returncode:
                        issue(rel, 'rar_listing_failed')
                        continue
                    for index, entry in enumerate(result.stdout.decode('utf-8', 'replace').splitlines()):
                        if not entry.endswith('/'):
                            yield row(cabinet, rel, -1, stat.st_mtime_ns, entry, index)
            except (OSError, BadZipFile, UnicodeError) as exc:
                issue(rel, type(exc).__name__)


def inventory(db, roots):
    db.executescript(SCHEMA)
    with db:
        for cabinet, root in roots.items():
            previous = db.execute('SELECT root FROM inventory_root WHERE cabinet=?', (cabinet,)).fetchone()
            if not previous or Path(previous[0]) != root:
                db.execute('DELETE FROM digest WHERE cabinet=?', (cabinet,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='embedded_probe'").fetchone():
                    db.execute('DELETE FROM embedded_probe WHERE cabinet=?', (cabinet,))
            db.execute('INSERT OR REPLACE INTO inventory_root VALUES (?,?)', (cabinet, str(root)))
        db.execute('DELETE FROM resource')
        db.execute('DELETE FROM inventory_issue')
        for cabinet, root in roots.items():
            def issue(path, reason):
                db.execute('INSERT INTO inventory_issue VALUES (?,?,?)', (cabinet, path, reason))
            db.executemany('INSERT INTO resource VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', walk(cabinet, root, issue))


class Reader:
    def __init__(self, roots, password, stack):
        self.roots, self.password, self.stack = roots, password, stack
        self.archives = {}

    def live_path(self, item):
        root = self.roots[item['cabinet']]
        locator = Path(item['path'])
        if locator.anchor or '..' in locator.parts:
            raise ValueError('invalid inventory locator')
        path = root / locator
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('resource escapes HDD root')
        stat = path.stat()
        if stat.st_mtime_ns != item['mtime'] or (not item['member'] and stat.st_size != item['bytes']):
            raise ValueError('resource changed since inventory')
        return path

    @contextmanager
    def open(self, item):
        path = self.live_path(item)
        if not item['member']:
            with path.open('rb') as source:
                yield source
            return
        if path.suffix.casefold() != '.zip':
            raise ValueError('archive is list-only')
        if path not in self.archives:
            archive = self.stack.enter_context(ZipFile(path))
            self.archives[path] = archive, {entry.header_offset: entry for entry in archive.infolist()}
        archive, entries = self.archives[path]
        entry = entries[item['ordinal']]
        if (entry.filename, entry.CRC, entry.file_size) != (item['member'], item['crc'], item['bytes']):
            raise ValueError('member changed since inventory')
        with archive.open(entry, pwd=self.password) as source:
            yield source

    def read(self, item):
        if not 0 <= item['bytes'] <= LIMIT:
            raise ValueError('resource exceeds decode limit')
        with self.open(item) as source:
            data = source.read(LIMIT + 1)
        if len(data) != item['bytes']:
            raise ValueError('resource size changed')
        return data


def fingerprint(db, item, reader):
    key = tuple(item[name] for name in ('cabinet', 'path', 'member', 'ordinal', 'bytes', 'mtime', 'crc'))
    cached = db.execute('SELECT sha256 FROM digest WHERE cabinet=? AND path=? AND member=? AND ordinal=? '
                        'AND bytes=? AND mtime=? AND crc=?', key).fetchone()
    if cached:
        reader.live_path(item)
        return cached[0], None
    raw = reader.read(item)
    digest = sha256(raw).hexdigest()
    db.execute('INSERT OR REPLACE INTO digest VALUES (?,?,?,?,?,?,?,?)', (*key, digest))
    return digest, raw


def summary(db, catalog):
    inventories = {}
    for cabinet in ('SDBT', 'SDHD'):
        files = db.execute('SELECT * FROM resource WHERE cabinet=?', (cabinet,))
        indexed = {row[0] for row in catalog.execute('SELECT path FROM sources WHERE kind=? AND hdd LIKE ?',
                                                     ('image', cabinet + '%'))}
        extensions, image_folders, unindexed, archives, sealed = (Counter() for _ in range(5))
        for item in files:
            ext = item['extension']
            extensions[ext] += 1
            if ext in {'.vhd', '.zip', '.rar'}:
                archives[ext] += 1
            if item['flags'] & 1:
                image_folders[(item['scope'], item['folder'], ext)] += 1
                path = item['path'] + ('!/' + item['member'] if item['member'] else '')
                if path not in indexed:
                    unindexed[(item['scope'], item['folder'], ext)] += 1
                if item['encrypted'] == 1:
                    sealed[ext] += 1
        inventories[cabinet] = {'extensions': extensions, 'archives': archives, 'encrypted_images': sealed,
                                'image_folders': sorted([*key, count] for key, count in image_folders.items()),
                                'absent_from_sources': sorted([*key, count] for key, count in unindexed.items())}
    return {'inventory': inventories, 'issues': [dict(row) for row in db.execute('SELECT * FROM inventory_issue')]}


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--old-root', type=Path, required=True)
    parser.add_argument('--new-root', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    return parser


def open_catalog(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def inputs(args):
    roots = {'SDBT': args.old_root.resolve(), 'SDHD': args.new_root.resolve()}
    output = args.output.resolve()
    if any(output == root or output.is_relative_to(root) for root in roots.values()):
        raise ValueError('Output must be outside both HDD roots')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('images.sqlite', 'inventory.json', 'candidates.json', 'preview'):
        child = output / name
        if child.is_symlink() or child.is_junction() or not child.resolve().is_relative_to(output):
            raise ValueError(f'Unexpected output link: {child}')
    if (output / 'images.sqlite').exists() and (output / 'images.sqlite').samefile(args.catalog):
        raise ValueError('Inventory aliases input catalog')
    return roots, output


def main():
    args = arguments(__doc__).parse_args()
    roots, output = inputs(args)
    with ExitStack() as stack:
        db = stack.enter_context(closing(sqlite3.connect(output / 'images.sqlite')))
        catalog = stack.enter_context(closing(open_catalog(args.catalog)))
        db.row_factory = sqlite3.Row
        inventory(db, roots)
        result = summary(db, catalog)
    (output / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
