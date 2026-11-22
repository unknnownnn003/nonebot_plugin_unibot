"""Inventory the observed SDBT directories and index XML and referenced assets."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
from xml.etree import ElementTree
from xml.parsers import expat

from history_music_xml import difficulty_for, reject_doctype


HDD_NAMES = tuple(f'SDBT_1.{n:02}' for n in (1, 6, 10, 15, 20, 25, 30, 35, 40, 45, 50))
STARS = {str(2 * n - 1): n for n in range(1, 6)}
IMAGE_EXTENSIONS = {'.dds', '.png', '.webp', '.jpg', '.jpeg', '.bmp', '.tga'}
C2S_LABELS = {b'VERSION', b'MUSIC', b'SEQUENCEID', b'DIFFICULT', b'LEVEL', b'CREATOR'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def integer(text):
    return int(text) if text and re.fullmatch(r'-?[0-9]{1,9}', text) else None


def boolean(text):
    return {'true': True, '1': True, 'false': False, '0': False}.get((text or '').lower())


def raw_constant(level, fraction):
    whole, decimal = integer(level), integer(fraction)
    if whole is not None and 1 <= whole <= 20 and decimal is not None and 0 <= decimal < 100:
        return whole * 100 + decimal
    return None


def display_level(constant):
    if constant is None:
        return None
    whole, decimal = divmod(constant, 100)
    return str(whole) + ('+' if whole >= 7 and decimal >= 70 else '')


def metadata(element):
    return (element.text or '').strip() if not len(element) else [[item.tag, metadata(item)] for item in element]


@dataclass
class Chart:
    slot: int
    difficulty: str
    enabled: bool | None
    level: str | None
    decimal: str | None
    ref: str | None
    designer: str | None
    path: str | None = None
    digest: str | None = None

    @property
    def constant(self):
        if self.enabled and self.difficulty not in ('unknown', 'WE'):
            return raw_constant(self.level, self.decimal)
        return None


@dataclass
class Song:
    source: str
    digest: str
    package: str
    music_id: int
    directory: str
    title: str
    artist: str | None
    disabled: bool | None
    ex_type: int | None
    tag: str | None
    tag_id: str | None
    star: str | None
    fields: dict
    release: str | None
    open_tag: str | None
    cue_ref: str | None
    jacket_ref: str | None
    charts: list
    jacket_path: str | None = None
    jacket_hash: str | None = None
    jacket_status: str = 'missing'


def parse_music(data, source, package, digest):
    if len(data) > 2_000_000:
        raise ValueError('oversized Music.xml')
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.Parse(data, True)
    root = ElementTree.fromstring(data)
    if root.tag != 'MusicData':
        raise ValueError('unexpected XML root')
    get = root.findtext
    music_id, title = integer(get('name/id')), (get('name/str') or '').strip()
    if music_id is None or music_id < 0 or not title:
        raise ValueError('missing music ID or title')
    ex_type = integer(get('exType'))
    charts = []
    for row in root.findall('fumens/MusicFumenData'):
        value = row.findtext
        slot = integer(value('type/id'))
        if slot is None or slot < 0:
            slot = -len(charts) - 1
        chart = Chart(slot, difficulty_for(slot, (value('type/data') or '').replace('`', "'"), 'legacy', ex_type),
                      boolean(value('enable')), value('level'), value('levelDecimal'),
                      value('file/path') or None, value('notesDesigner') or None)
        charts.append(chart)
    return Song(source, digest, package, music_id, PurePosixPath(source).parent.name, title, get('artistName/str'),
                boolean(get('disableFlag')), ex_type, get('worldsEndTagName/str'), get('worldsEndTagName/id'),
                get('starDifType'), {child.tag: metadata(child) for child in root if child.tag != 'fumens'},
                get('releaseTagName/str'), get('netOpenName/str'), get('cueFileName/str') or None,
                get('jaketFile/path') or get('jacketFile/path') or None, charts=charts)


def file_hash(path):
    with path.open('rb') as stream:
        digest = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def c2s_notation_hash(data):
    """Hash serialized notation/timing without known label headers or summary rows."""
    lines = data.splitlines()
    if not lines or not lines[0].startswith(b'VERSION\t'):
        return None
    notation = []
    for line in lines:
        line = line.strip()
        key = line.split(b'\t', 1)[0]
        if line and key not in C2S_LABELS and not key.startswith(b'T_'):
            notation.append(line)
    return sha256(b'\n'.join(notation)).hexdigest() if notation else None


def c2s_creator(data):
    """Read the observed UTF-8 CREATOR header, retaining the exact author text."""
    for line in data.splitlines()[:20]:
        if line.startswith(b'CREATOR\t'):
            try:
                return line.split(b'\t', 1)[1].decode('utf-8').strip() or None
            except UnicodeDecodeError:
                return None
    return None


def asset_metadata(path):
    with path.open('rb') as stream:
        header = stream.read(148)
    if header.startswith(b'DDS ') and len(header) >= 128:
        height, width = struct.unpack_from('<II', header, 12)
        fourcc = header[84:88]
        pixel = fourcc.decode('ascii', 'replace') if fourcc.strip(b'\0') else 'RGB'
        return 'DDS', width, height, pixel
    from PIL import Image
    with Image.open(path) as image:
        return image.format, image.width, image.height, image.mode


def safe_ref(parent, text):
    ref = PurePosixPath(text.replace('\\', '/'))
    if ref.is_absolute() or '..' in ref.parts or ':' in text:
        return None
    return (parent / ref).as_posix()


def asset_ref(song, text, files):
    if not text:
        return None, 'no_reference'
    source = PurePosixPath(song.source)
    candidate = safe_ref(source.parent, text)
    if candidate is None:
        return None, 'unsafe_reference'
    if candidate.casefold() in files:
        return files[candidate.casefold()], 'matched'
    # Look in the same logical XML directory of the same disk only. The
    # caller's index excludes unverified packs for this fallback.
    return None, 'missing'


def log_conflict(db, version, song, diff, kind, detail, other=None):
    db.execute('INSERT INTO conflicts VALUES (?,?,?,?,?,?,?)',
               (version, song.music_id if song else None, diff, kind,
                song.source if song else None, other, detail))


def get_data_version(path):
    if not path.is_file():
        return None
    data = path.read_text(encoding='utf-8-sig', errors='replace')
    parts = [re.search(fr'^Ver{k}\s*=\s*(\d+)', data, re.M | re.I) for k in ('Major', 'Minor', 'Release')]
    return '.'.join(x[1] for x in parts) if all(parts) else None


def layer_class(hdd, name, version):
    if name == 'A000':
        return 'base', 'data/A000, data.conf and bin/project.conf'
    if hdd == 'SDBT_1.50':
        if name == 'A910':
            return 'third_party', 'update.txt records custom music 7000/7001 and 2021-11-09 marker'
        if name == 'A114':
            return 'excluded_revived_content', 'No data.conf; re-enables songs after dated JP removals and supplies historical WE'
        if name in ('A032', 'A033'):
            return 'historical_candidate', 'Compared to dated Wiki song changes; no signed package provenance or data.conf'
        return 'unknown', 'No verified production playability; music contribution withheld'
    if version:
        return 'historical_candidate', 'Versioned data.conf; package provenance is unauthenticated'
    return 'unknown', 'No versioned data.conf'


def discover(db, root, hdd, *, hash_all=False):
    disk = root / hdd
    options = disk / 'bin/option'
    layers = [disk / 'data/A000']
    if options.is_dir():
        layers.extend(sorted((x for x in options.iterdir() if x.is_dir() and x.name != '自制包'), key=lambda x: x.name))
    songs, package_info = defaultdict(list), {}
    source_data = {}
    path_cache = {}
    c2s_seen = set()
    issues = []
    vhd = root / f'{hdd}.vhd'
    if vhd.is_file():
        db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
                   (vhd.name, hdd, None, vhd.stat().st_size, None, 'vhd_container', 'inventory_only'))
    for extra in ('bin/project.conf',):
        path = disk / extra
        if path.is_file():
            loc = path.relative_to(root).as_posix()
            data = path.read_bytes()
            db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
                       (loc, hdd, None, len(data), sha256(data).hexdigest(), 'config', None))

    for layer in layers:
        name = layer.name
        locator = layer.relative_to(root).as_posix()
        if not layer.is_dir():
            issues.append((locator, 'missing package directory'))
            continue
        version = get_data_version(layer / 'data.conf')
        classification, basis = layer_class(hdd, name, version)
        counts = Counter()

        def walk_error(error):
            issues.append((locator, f'directory read failed: {type(error).__name__}: {error.filename}'))

        for loc, folders, names in os.walk(layer, onerror=walk_error, followlinks=False):
            folders[:] = sorted(folder for folder in folders if folder != '自制包')
            for filename in sorted(names):
                path = Path(loc) / filename
                relative = path.relative_to(root).as_posix()
                digest, size, data, issue = None, None, None, None
                suffix = path.suffix.lower()
                file_lower = filename.lower()
                kind = ('music_xml' if file_lower == 'music.xml' else 'xml' if suffix == '.xml' else
                        'c2s' if suffix == '.c2s' else 'image' if suffix in IMAGE_EXTENSIONS else
                        'config' if suffix in ('.conf', '.txt', '.ini') or file_lower.startswith('ver ') else 'other')
                try:
                    size = path.stat().st_size
                    if kind in ('xml', 'music_xml') and size <= 16_000_000:
                        data = path.read_bytes()
                        digest = sha256(data).hexdigest()
                    elif kind == 'c2s' and size <= 8_000_000:
                        chart_data = path.read_bytes()
                        digest = sha256(chart_data).hexdigest()
                        if digest not in c2s_seen:
                            c2s_seen.add(digest)
                            db.execute('INSERT OR IGNORE INTO c2s_sections VALUES (?,?,?)',
                                       (digest, c2s_notation_hash(chart_data), c2s_creator(chart_data)))
                    elif hash_all or kind in ('xml', 'music_xml', 'c2s', 'image', 'config'):
                        digest = file_hash(path)
                except OSError as exc:
                    issue = type(exc).__name__
                    issues.append((relative, issue))
                db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
                           (relative, hdd, name, size, digest, kind, issue))
                if kind in ('xml', 'music_xml'):
                    counts['xml'] += 1
                    if data is not None:
                        db.execute('INSERT INTO raw_xml VALUES (?,?)', (relative, data))
                if kind in ('image', 'c2s') and digest:
                    source_data[relative] = digest
                    path_cache[relative.casefold()] = relative
                if kind == 'music_xml':
                    counts['music_xml'] += 1
                    if data is not None:
                        try:
                            songs[name].append(parse_music(data, relative, name, digest))
                        except (ValueError, expat.ExpatError, ElementTree.ParseError) as exc:
                            issues.append((relative, f'{type(exc).__name__}: {exc}'))
                    elif not issue:
                        issues.append((relative, 'XML not retained, size limit'))
        package_info[name] = (version, classification, basis)
        db.execute('INSERT INTO packages VALUES (?,?,?,?,?,?,?,?)',
                   (hdd, name, locator, version, counts['xml'], counts['music_xml'], classification, basis))
    for relative, issue in issues:
        log_conflict(db, hdd, None, None, 'source_issue', issue, relative)
    for rows in songs.values():
        for song in rows:
            db.execute('INSERT INTO raw_song_index VALUES (?,?,?,?,?,?,?)',
                       (song.source, hdd, song.package, song.music_id, song.title, song.ex_type, song.disabled))
            if song.ex_type == 2:
                db.execute('INSERT INTO raw_we VALUES (?,?,?,?,?,?,?,?,?)',
                           (song.source, hdd, song.package, song.music_id, song.title, song.tag,
                            song.tag_id, song.star, STARS.get(song.star)))
            song.jacket_path, song.jacket_status = asset_ref(song, song.jacket_ref, path_cache)
            song.jacket_hash = source_data.get(song.jacket_path)
            if song.jacket_hash:
                try:
                    image = asset_metadata(root / song.jacket_path)
                    db.execute('INSERT OR IGNORE INTO jackets VALUES (?,?,?,?,?,?)',
                               (song.jacket_hash, *image, song.jacket_path))
                except (ValueError, OSError, ImportError) as exc:
                    song.jacket_status, song.jacket_hash = 'header_failure', None
                    log_conflict(db, hdd, song, None, 'jacket_issue', type(exc).__name__, song.jacket_path)
            for chart in song.charts:
                chart.path, status = asset_ref(song, chart.ref, path_cache)
                chart.digest = source_data.get(chart.path)
                if chart.enabled and status != 'matched':
                    log_conflict(db, hdd, song, chart.difficulty, 'c2s_reference', status)
    return songs, package_info
