"""SDHD physical data adapter. Paths in the catalog remain relative to the input roots."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import struct
from xml.etree import ElementTree
from zipfile import ZipFile

from old_hdd_scan import (
    IMAGE_EXTENSIONS, STARS, c2s_creator, c2s_notation_hash, encoded, file_hash,
    integer, parse_music, raw_constant, safe_ref,
)
from old_hdd_timeline import batch
from history_calendar import JP_RELEASES

SDHD = tuple(zip(JP_RELEASES[12:], (0, 5, 10, 15, 20, 25, 30, 40, 45, 50), strict=True))
CONFIG = re.compile(rb'^Ver(?:Major|Minor|Release)\s*=\s*(\d+)', re.M)
PACK = re.compile(r'A\d{3}')
VHD_OPTION = re.compile(r'SDHD_(A\d{3})_(\d{14})_0')
DIRECTIONS = {'BASIC': 'BAS', 'ADVANCED': 'ADV', 'EXPERT': 'EXP', 'MASTER': 'MAS',
              'ULTIMA': 'ULT', "WORLD'S END": 'WE'}


@dataclass(frozen=True)
class Layer:
    package: str
    path: Path
    stamp: str | None = None
    members: tuple = ()

    def files(self, root, stack, password, opened):
        container = self.path.relative_to(root).as_posix()
        if self.path.is_dir():
            return {p.relative_to(self.path).as_posix(): File(p.relative_to(root).as_posix(), p)
                    for p in sorted(self.path.rglob('*')) if p.is_file() and relevant(p.relative_to(self.path))}
        if self.path not in opened:
            opened[self.path] = stack.enter_context(ZipFile(self.path))
            opened[self.path].setpassword(password)
        archive = opened[self.path]
        return {name: File(f'{container}!/{info.filename}', archive, info)
                for name, info in self.members if relevant(PurePosixPath(name))}


@dataclass(frozen=True)
class File:
    source: str
    location: object
    member: object = None

    def read(self):
        return self.location.read_bytes() if self.member is None else self.location.read(self.member)

    def size(self):
        return self.location.stat().st_size if self.member is None else self.member.file_size


def relevant(path):
    folder = path.parts[0]
    return (path.name == 'data.conf' or folder.lower() in {
        'music', 'event', 'course', 'map', 'maparea', 'ddsbanner', 'ddsimage', 'releasetag', 'gametable'
    } and path.suffix.lower() in IMAGE_EXTENSIONS | {'.xml', '.c2s'})


def discover(root, minor, extras=()):
    candidates = sorted(root.glob(f'SDHD - CHUNITHM */HDD/SDHD_2.{minor:02}.*_0/data/A000'))
    if minor == 15:  # The SUN PLUS local image is 2.16, its data.conf is 2.15.
        candidates = sorted(root.glob('SDHD - CHUNITHM */HDD/SDHD_2.16.*_0/data/A000'))
    if len(candidates) != 1:
        raise ValueError(f'Expected one SDHD base for 2.{minor:02}, found {len(candidates)}')
    base = candidates[0]
    yield Layer('A000', base, base.parents[1].name.split('_')[2])
    for directory in (base.parents[3] / 'option', base.parents[1] / 'bin' / 'option', *extras):
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir()):
            match = VHD_OPTION.fullmatch(item.name)
            if item.is_dir() and (match or PACK.fullmatch(item.name)):
                yield Layer(match[1], item, match[2]) if match else Layer(item.name, item)
            if not item.is_file() or item.suffix.lower() != '.zip':
                continue
            groups = defaultdict(list)
            with ZipFile(item) as archive:
                for info in archive.infolist():
                    name = PurePosixPath(info.filename)
                    if info.is_dir() or name.is_absolute() or '..' in name.parts:
                        continue
                    parts = name.parts[1:] if name.parts and name.parts[0].lower() == 'option' else name.parts
                    if len(parts) >= 2 and PACK.fullmatch(parts[0]):
                        groups[parts[0]].append(('/'.join(parts[1:]), info))
            for package, members in sorted(groups.items()):
                if len({name.casefold() for name, _ in members}) != len(members):
                    raise ValueError(f'Duplicate archive members in {item.name}/{package}')
                yield Layer(package, item, members=tuple(sorted(members)))


def issue(db, version, source, kind, detail):
    db.execute('INSERT INTO sdhd_issues VALUES (?,?,?,?)', (version, source, kind, detail))


def read_layers(db, root, version, minor, start, end, stack, password, extras=()):
    available = []
    containers = set()
    opened = {}
    for layer in discover(root, minor, extras):
        container = layer.path.relative_to(root).as_posix()
        if layer.path.is_file() and container not in containers:
            containers.add(container)
            db.execute('INSERT INTO sdhd_containers VALUES (?,?,?,?)',
                       (version, container, layer.path.stat().st_size, file_hash(layer.path)))
        if container not in containers and layer.stamp:
            extracted = layer.path.parents[1] if layer.package == 'A000' else layer.path
            image = extracted.with_name(extracted.name + '.vhd')
            if image.is_file():
                db.execute('INSERT OR IGNORE INTO sdhd_containers VALUES (?,?,?,NULL)',
                           (version, image.relative_to(root).as_posix(), image.stat().st_size))
        files = layer.files(root, stack, password, opened)
        conf = files.get('data.conf')
        music_count = sum(name.lower().endswith('/music.xml') for name in files)
        reason = None
        revision = ()
        try:
            rev = CONFIG.findall(conf.read()) if conf else []
            revision = tuple(map(int, rev))
            if len(revision) != 3 or revision[:2] != (2, minor):
                reason = 'unverified_data_version'
        except (RuntimeError, NotImplementedError):
            reason = 'encrypted_or_unsupported_archive'
        if reason:
            issue(db, version, container, reason, layer.package)
        parsed_stamp = (date(int(layer.stamp[:4]), int(layer.stamp[4:6]), int(layer.stamp[6:8])).isoformat()
                        if layer.stamp else None)
        db.execute('INSERT INTO sdhd_layers VALUES (?,?,?,?,?,?,?,?)',
                   (version, layer.package, container, '.'.join(map(str, revision)) or None,
                    parsed_stamp, len(files), music_count, reason or 'indexed'))
        if reason is None:
            cache = max(start, parsed_stamp or start)
            if cache > end:
                issue(db, version, container, 'build_after_checkpoint', layer.package)
                continue
            available.append((layer, revision, files, cache))
    return sorted(available, key=lambda row: (row[0].package != 'A000', row[1], row[0].package, row[0].path))


def source(db, file, hdd, package, kind, *, raw=False):
    found = db.execute('SELECT sha256 FROM sources WHERE path=?', (file.source,)).fetchone()
    if found and not raw:
        return found[0], None
    if file.size() > (32 << 20):
        raise ValueError('Referenced data exceeds 32 MiB')
    data = file.read()
    digest = sha256(data).hexdigest()
    if not found:
        db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,NULL)',
                   (file.source, hdd, package, len(data), digest, kind))
        if raw:
            db.execute('INSERT INTO raw_xml VALUES (?,?)', (file.source, data))
    return digest, data


def reference(db, version, files, name, ref, kind):
    if not ref:
        return None, None, None
    path = safe_ref(PurePosixPath(name).parent, ref)
    item = files.get((path or '').casefold())
    if item is None:
        issue(db, version, files[name.casefold()][0].source, f'missing_{kind}', ref)
        return None, None, None
    file, hdd, package = item
    digest, data = source(db, file, hdd, package, kind)
    return file.source, digest, data


def compact_date(value):
    if not re.fullmatch(r'\d{8}', value or ''):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_record(db, version, files, name, threshold):
    file, hdd, package = files[name.casefold()]
    digest, raw = source(db, file, hdd, package, 'music_xml', raw=True)
    song = parse_music(raw, file.source, package, digest)
    node = ElementTree.fromstring(raw)
    song.jacket_path, song.jacket_hash, _ = reference(db, version, files, name, song.jacket_ref, 'image')
    if song.jacket_hash and not db.execute('SELECT 1 FROM jackets WHERE sha256=?', (song.jacket_hash,)).fetchone():
        jacket = files[safe_ref(PurePosixPath(name).parent, song.jacket_ref).casefold()][0].read()
        if jacket.startswith(b'DDS ') and len(jacket) >= 128:
            height, width = struct.unpack_from('<II', jacket, 12)
            code = jacket[84:88].decode('ascii', 'replace').rstrip('\0') or 'RGB'
        else:
            width = height = code = None
        db.execute('INSERT INTO jackets VALUES (?,?,?,?,?,?)',
                   (song.jacket_hash, 'DDS' if width else 'unknown', width, height, code, song.jacket_path))
    chart_rows = []
    for chart, xml in zip(song.charts, node.findall('fumens/MusicFumenData'), strict=True):
        label = xml.findtext('type/data') or ''
        if not chart.enabled and not chart.ref:
            continue
        chart.difficulty = DIRECTIONS.get(label, label or f'SLOT_{chart.slot}')
        chart.path, chart.digest, data = reference(db, version, files, name, chart.ref, 'c2s')
        notation = creator = None
        if chart.digest:
            cached = db.execute('SELECT notation_sha256,creator FROM c2s_sections WHERE sha256=?',
                                (chart.digest,)).fetchone()
            if cached:
                notation, creator = cached
            elif data and PurePosixPath(chart.ref).suffix.lower() == '.c2s':
                notation, creator = c2s_notation_hash(data), c2s_creator(data)
                db.execute('INSERT INTO c2s_sections VALUES (?,?,?)', (chart.digest, notation, creator))
            if notation is None:
                issue(db, version, chart.path, 'unparsed_notation', label)
        if chart.difficulty == 'WE':
            values = {'tag': song.tag, 'tag_id': song.tag_id, 'stars': STARS.get(song.star), 'star_raw': song.star}
        else:
            constant = raw_constant(chart.level, chart.decimal)
            display = None
            if constant is not None and threshold is not None:
                level, fraction = divmod(constant, 100)
                display = str(level) + ('+' if level >= 7 and fraction >= threshold else '')
            values = {'hdd_hundredths': constant, 'display_level': display,
                      'level_raw': chart.level, 'decimal_raw': chart.decimal}
        enabled = bool(chart.enabled and (chart.difficulty != 'ULT' or node.findtext('enableUltima') == 'true'))
        chart_rows.append((chart, values | {
            'enabled': enabled, 'c2s_sha256': chart.digest, 'notation_sha256': notation,
            'notes_designer': chart.designer or creator, 'difficulty_label': label,
            'designer_source': 'music_xml' if chart.designer else 'c2s_header' if creator else None,
        }))
    fields = {key: value for key, value in song.fields.items() if key != 'dataName'}
    payload = fields | {'title': song.title, 'artist': song.artist, 'jacket_sha256': song.jacket_hash}
    return song, payload, chart_rows


def scan_auxiliary(db, version, layer, files, hdd, batch_id, overlay):
    for name, file in files.items():
        path = PurePosixPath(name)
        if path.name.lower() == 'music.xml' or path.parts[0].lower() == 'music':
            continue
        ext = path.suffix.lower()
        kind = 'image' if ext in IMAGE_EXTENSIONS else 'xml' if ext == '.xml' else 'config'
        try:
            digest, raw = source(db, file, hdd, layer.package, kind, raw=(kind == 'xml'))
            if kind == 'xml' and path.name.lower() == 'event.xml':
                node = ElementTree.fromstring(raw)
                db.execute('INSERT OR IGNORE INTO history_game_events VALUES (?,?,?,?,?,?,?)',
                           (file.source, integer(node.findtext('name/id')), node.findtext('name/str'),
                            node.findtext('substances/type'), node.findtext('alwaysOpen') == 'true',
                            node.findtext('substances/music/musicType'), integer(node.findtext('ddsBannerName/id'))))
                for role, xpath in (('music', 'music/musicNames/list/*'), ('map_music', 'map/musicNames/list/*'),
                                    ('recommend_music', 'recommendMusic/musicNames/list/*'),
                                    ('map', 'map/mapName'), ('course', 'information/courseNames/list/*')):
                    for ref in node.findall('substances/' + xpath):
                        target = integer(ref.findtext('id'))
                        if target is not None and target >= 0:
                            db.execute('INSERT OR IGNORE INTO history_game_event_links VALUES (?,?,?,?)',
                                       (file.source, role, target, ref.findtext('str')))
            if kind == 'xml' and path.name.lower() in ('event.xml', 'ddsbanner.xml'):
                if path.name.lower() == 'ddsbanner.xml':
                    node = ElementTree.fromstring(raw)
                    ref = node.findtext('ddsFile/path')
                    asset_kind, relation = 'hdd_game_banner', 'banner_definition'
                else:
                    ref = node.findtext('substances/information/image/path')
                    asset_kind, relation = 'hdd_game_notice', 'event_information_image'
                if ref:
                    location, digest, _ = reference(db, version, overlay, name, ref, 'image')
                    if digest:
                        db.execute('INSERT OR IGNORE INTO history_assets VALUES (?,?)', (digest, asset_kind))
                        db.execute('INSERT OR IGNORE INTO history_asset_links VALUES (?,?,?,?,?,?,?)',
                                   (digest, location, relation, file.source, batch_id, None, node.findtext('name/str')))
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            issue(db, version, file.source, 'unreadable_auxiliary', str(exc))
