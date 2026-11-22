"""Decode JP HDD notice candidates, cache OCR, and propose reviewable object links."""
from collections import defaultdict
from contextlib import ExitStack, closing
from datetime import date
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path, PurePosixPath
import json
import os
import re
import sqlite3
import unicodedata
from zipfile import BadZipFile

from PIL import Image

from hdd_image_inventory import Reader, arguments, fingerprint, inputs, open_catalog

CANDIDATES = '''flags & 1 AND (flags & 2 OR folder IN
    ('event','ddsbanner','ddsmap','aimecollab','texture','dds','rightsinfo')) AND scope!='custom' '''
WEEKDAYS = '月火水木金土日'
DATE = re.compile(r'(?:(\d{4})\s*年\s*)?(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:\(([月火水木金土日])(?:曜(?:日)?)?\))?')
SLASH_DATE = re.compile(r'(?<![\d/])(20\d{2})/(\d{1,2})/(\d{1,2})(?![\d/])')
TYPES = {
    'song_add': r'楽曲.{0,8}追加|新曲|新規楽曲', 'ultima': r'ULTIMA',
    'we': r'WORLD[’\' ]?S\s*END', 'event': r'イベント開催', 'course': r'コース|クラス認定', 'map': r'マップ|マップ解放',
    'general_open': r'通常開放|通常解放|一般開放', 'limited': r'期間限定|限(?:定|時)|まで[!！]?',
    'early': r'先行|早期', 'removed': r'配信終了|削除', 'restored': r'復活|再配信',
    'revision': r'譜面変更|譜面修正|レベル調整|レベル変更|定数',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS image (
    sha256 TEXT PRIMARY KEY, width INTEGER, height INTEGER, format TEXT,
    pixels_sha256 TEXT, visual_hash TEXT, issue TEXT
);
CREATE TABLE IF NOT EXISTS image_ocr (
    sha256 TEXT, engine TEXT, lines TEXT NOT NULL, PRIMARY KEY(sha256,engine)
);
'''


def folded(text):
    return unicodedata.normalize('NFKC', text).casefold()


def dates(text):
    normalized = unicodedata.normalize('NFKC', text)
    matches = sorted((match for pattern in (DATE, SLASH_DATE) for match in pattern.finditer(normalized)),
                     key=lambda match: match.start())
    for match in matches:
        year, month, day, *weekdays = match.groups()
        weekday = weekdays[0] if weekdays else None
        try:
            date(int(year or 2000), int(month), int(day))
        except ValueError:
            continue
        yield {'printed': match[0].strip(), 'year': int(year) if year else None,
               'month': int(month), 'day': int(day), 'weekday': weekday}


def possible_days(printed, ranges):
    result = set()
    for lo, hi in ranges:
        for year in range(lo.year, hi.year + 1):
            if printed['year'] not in (None, year):
                continue
            try:
                day = date(year, printed['month'], printed['day'])
            except ValueError:
                continue
            if lo <= day <= hi and printed['weekday'] in (None, WEEKDAYS[day.weekday()]):
                result.add(day.isoformat())
    return sorted(result)


def normalized_frame(image):
    rgba = image.convert('RGBA')
    background = Image.new('RGBA', rgba.size, (245, 245, 245, 255))
    return Image.alpha_composite(background, rgba).convert('RGB')


def visual_hash(image):
    values = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    result = sum((values[y*9+x] > values[y*9+x+1]) << (y*8+x) for y in range(8) for x in range(8))
    return f'{result:016x}'


def decode(data):
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 30_000_000:
            raise ValueError('pixel limit')
        return source.format, normalized_frame(source)


def compare(first, second):
    """Whole-frame and center-crop appearance. Similarity alone makes no event claim."""
    def variants(image):
        yield image
        for keep in (.98, .95, .9, .8):
            dx, dy = int(image.width * (1-keep)/2), int(image.height * (1-keep)/2)
            yield image.crop((dx, dy, image.width-dx, image.height-dy))
    def sample(image):
        return image.convert('RGB').resize((48, 48), Image.Resampling.LANCZOS).tobytes()
    def difference(left, right):
        return sum(abs(a-b) for a, b in zip(left, right, strict=True)) / (48*48*3*255)
    left, right = [sample(i) for i in variants(first)], [sample(i) for i in variants(second)]
    return {'whole_frame_mean_error': round(difference(left[0], right[0]), 5),
            'center_crop_mean_error': round(min(min(difference(left[0], r) for r in right),
                           min(difference(l, right[0]) for l in left)), 5)}


def decode_candidates(db, reader, output):
    entries, issues, digests = defaultdict(list), [], set()
    preview = output / 'preview'
    preview.mkdir(exist_ok=True)
    for item in db.execute('SELECT * FROM resource WHERE ' + CANDIDATES +
                           " ORDER BY (lower(path||member) LIKE '%chu_info_%') DESC,cabinet,path,ordinal"):
        locator = {key: item[key] for key in ('cabinet', 'volume', 'package', 'scope', 'folder', 'path', 'member', 'ordinal')}
        try:
            digest, raw = fingerprint(db, item, reader)
        except (OSError, ValueError, RuntimeError, BadZipFile, KeyError, NotImplementedError) as exc:
            issues.append({**locator, 'issue': type(exc).__name__, 'encrypted': item['encrypted'] == 1})
            continue
        entries[digest].append(locator)
        if digest in digests:
            continue
        digests.add(digest)
        target = preview / f'{digest}.png'
        cached = db.execute('SELECT issue FROM image WHERE sha256=?', (digest,)).fetchone()
        if cached and (cached[0] or target.is_file()):
            continue
        try:
            if raw is None:
                raw = reader.read(item)
                if sha256(raw).hexdigest() != digest:
                    raise ValueError('resource content changed since hashing')
            fmt, image = decode(raw)
            pixel_hash = sha256(f'{image.width}x{image.height}:'.encode() + image.tobytes()).hexdigest()
            db.execute('INSERT OR REPLACE INTO image VALUES (?,?,?,?,?,?,NULL)',
                       (digest, image.width, image.height, fmt, pixel_hash, visual_hash(image)))
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            temporary = target.with_suffix('.tmp')
            image.save(temporary, format='PNG', compress_level=1)
            temporary.replace(target)
        except (OSError, ValueError, NotImplementedError, Image.DecompressionBombError) as exc:
            db.execute('INSERT OR REPLACE INTO image VALUES (?,NULL,NULL,NULL,NULL,NULL,?)',
                       (digest, type(exc).__name__))
    return entries, issues


def xml_references(catalog, entries):
    exact = defaultdict(set)
    for digest, items in entries.items():
        for item in items:
            location = item['path'] + ('!/' + item['member'] if item['member'] else '')
            exact[location].add(digest)
    result = defaultdict(list)
    for location, asset, relation, source, batch in catalog.execute(
            'SELECT location,asset,relation,source,batch FROM history_asset_links'):
        if asset in exact.get(location, ()):
            result[asset].append({'source': source, 'batch': batch, 'relation': relation, 'location': location})
    return result


def titles(catalog):
    result = defaultdict(set)
    for title, identity in catalog.execute("""SELECT DISTINCT json_extract(r.payload,'$.title'),r.identity
        FROM history_revisions r JOIN history_entities e USING(identity)
        WHERE e.kind='song' AND json_extract(r.payload,'$.title') IS NOT NULL"""):
        result[folded(title).strip()].add(identity)
    return result


def suggestions(catalog, names, lines, references, ranges):
    text = '\n'.join(line['text'] for line in lines)
    types = [kind for kind, pattern in TYPES.items() if re.search(pattern, text, re.I)]
    printed = [fragment | {'calendar_candidates': possible_days(fragment, ranges)} for fragment in dates(text)]
    title_candidates = []
    for line in lines:
        key = folded(line['text']).strip()
        if key not in names:
            continue
        for identity in sorted(names[key]):
            charts = []
            for package in sorted({ref['batch'] for ref in references if ref['batch']}):
                for chart in catalog.execute("""SELECT e.identity,e.kind,e.difficulty,e.music_id,o.source
                    FROM history_observations o JOIN history_entities e USING(identity)
                    WHERE o.batch=? AND o.state='hdd_enabled' AND (e.identity=? OR e.parent=?)""",
                                             (package, identity, identity)):
                    charts.append(dict(chart) | {'batch': package})
            title_candidates.append({'ocr': line['text'], 'song_identity': identity, 'hdd_witnesses': charts})
    return {'ocr': lines, 'dates': printed, 'types': types, 'title_candidates': title_candidates,
            'review_required': True}


def calendar_by_asset(catalog):
    rows = catalog.execute("""SELECT a.asset,v.start_on,v.end_on FROM history_asset_links a
        JOIN history_batches b USING(batch) JOIN versions v USING(version) GROUP BY 1,2,3""")
    result = defaultdict(list)
    for asset, low, high in rows:
        result[asset].append((date.fromisoformat(low), date.fromisoformat(high)))
    return result


def recognize(preview, engine):
    result = engine(str(preview))
    if not result.txts:
        return []
    return [{'text': text, 'score': float(score), 'box': box.tolist()}
            for text, score, box in zip(result.txts, result.scores, result.boxes, strict=True)]


def make_ocr():
    from importlib.metadata import version
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    if version('rapidocr') != '3.9.2':
        raise ValueError('Use RapidOCR 3.9.2 or update the OCR cache profile')
    return RapidOCR(params={'Rec.lang_type': LangRec.JAPAN, 'Rec.ocr_version': OCRVersion.PPOCRV4,
                            'Rec.model_type': ModelType.MOBILE, 'EngineConfig.onnxruntime.intra_op_num_threads': 2,
                            'EngineConfig.onnxruntime.inter_op_num_threads': 1})


def gallery(output, rows):
    pages = []
    for i in range(0, len(rows), 100):
        parts = []
        for row in rows[i:i+100]:
            digest = row['sha256']
            name = PurePosixPath(row['locations'][0]['member'] or row['locations'][0]['path']).name
            label = escape(name + ' | ' + row['locations'][0]['volume'])
            text = escape('\n'.join(line['text'] for line in row.get('ocr', [])))
            parts.append(f'<article><a href="preview/{digest}.png"><img loading="lazy" src="preview/{digest}.png"></a>'
                         f'<p>{label}</p><code>{digest[:16]}</code><pre>{text}</pre></article>')
        page = f'gallery-{i//100:02}.html'
        (output / page).write_text('<meta charset="utf-8"><title>HDD notices</title>'
            '<style>body{font:14px system-ui;background:#f5f5f5}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}article{background:white;padding:12px;overflow-wrap:anywhere}img{width:100%;height:200px;object-fit:contain}pre{white-space:pre-wrap}</style>'
            '<a href="gallery.html">Index</a><main>' + ''.join(parts) + '</main>', encoding='utf-8')
        pages.append(page)
    (output / 'gallery.html').write_text('<meta charset="utf-8"><title>HDD notice candidates</title><ul>'
        + ''.join(f'<li><a href="{page}">{page}</a></li>' for page in pages) + '</ul>', encoding='utf-8')


def validate_ocr(reviews, rows):
    by_digest = {row['sha256']: row for row in rows}
    results = []
    for claim in reviews:
        row = by_digest.get(claim['sha256'], {})
        lines = {folded(line['text']).strip() for line in row.get('ocr', [])}
        month, day = map(int, claim['day'].split('-')[1:])
        category = {'standard': 'song_add', 'map': 'map', 'event': 'event'}[claim['mode']]
        matches = [{'title': target['title'], 'difficulty': target.get('difficulty'),
                    'literal_title': folded(target['title']).strip() in lines,
                    'literal_difficulty': folded(target['difficulty_label']) in lines if 'difficulty_label' in target else None}
                   for target in claim['targets']]
        results.append({'sha256': claim['sha256'], 'has_ocr': 'ocr' in row, 'announcement': claim['announcement'],
                        'date_match': any((x['month'], x['day']) == (month, day) for x in row.get('dates', [])),
                        'category_match': category in row.get('types', []), 'targets': matches})
    return results


def compare_official(output, pairs, rows):
    decoded = {row['sha256'] for row in rows if not row['issue']}
    results = []
    for pair in json.loads(pairs.read_text(encoding='utf-8')):
        digest = pair['sha256']
        if digest not in decoded:
            continue
        path = (pairs.parent / pair['file']).resolve(strict=True)
        if not path.is_relative_to(pairs.parent.resolve()):
            raise ValueError('Official image path escapes local evidence folder')
        data = path.read_bytes()
        _, official = decode(data)
        with Image.open(output / 'preview' / f'{digest}.png') as hdd:
            metrics = compare(normalized_frame(hdd), official)
        results.append({key: pair[key] for key in ('sha256', 'article', 'url')} |
                       {'official_sha256': sha256(data).hexdigest(), **metrics})
    return results


def main():
    parser = arguments(__doc__)
    parser.add_argument('--password-file', type=Path)
    parser.add_argument('--ocr', choices=('named', 'all'))
    parser.add_argument('--ocr-limit', type=int)
    parser.add_argument('--review-set', type=Path, help='Score OCR against a separately reviewed JSON manifest')
    parser.add_argument('--official-pairs', type=Path,
                        help='JSON array of {sha256, article, url, file}; compares downloaded images locally')
    args = parser.parse_args()
    roots, output = inputs(args)
    password = (args.password_file.read_text(encoding='utf-8-sig').rstrip('\r\n') if args.password_file
                else os.getenv('UNIBOT_HDD_ZIP_PASSWORD'))
    with ExitStack() as stack:
        db = stack.enter_context(closing(sqlite3.connect(output / 'images.sqlite')))
        db.row_factory = sqlite3.Row
        catalog = stack.enter_context(closing(open_catalog(args.catalog)))
        db.executescript(SCHEMA)
        if {row[0]: Path(row[1]) for row in db.execute('SELECT * FROM inventory_root')} != roots:
            raise ValueError('Run inventory against these HDD roots first')
        with db:
            entries, issues = decode_candidates(db, Reader(roots, password.encode() if password else None, stack), output)
        references = xml_references(catalog, entries)
        names = titles(catalog)
        calendar = calendar_by_asset(catalog)
        engine, engine_key, count = None, 'rapidocr-3.9.2-japan-v4-mobile-preview-v1', 0
        rows = []
        for digest, locations in entries.items():
            item = dict(db.execute('SELECT * FROM image WHERE sha256=?', (digest,)).fetchone())
            item.update(locations=locations, references=references[digest])
            rows.append(item)
        def priority(item):
            paths = [loc['member'] or loc['path'] for loc in item['locations']]
            return (not any('chu_info_music' in name.casefold() for name in paths),
                    not any('chu_info_' in name.casefold() for name in paths), paths[0])
        rows.sort(key=priority)
        for item in rows:
            digest = item['sha256']
            cached = db.execute('SELECT lines FROM image_ocr WHERE sha256=? AND engine=?', (digest, engine_key)).fetchone()
            if cached:
                lines = json.loads(cached[0])
            elif (args.ocr and not item['issue'] and (args.ocr == 'all' or not priority(item)[1])
                  and (args.ocr_limit is None or count < args.ocr_limit)):
                if engine is None:
                    engine = make_ocr()
                lines = recognize(output / 'preview' / f'{digest}.png', engine)
                with db:
                    db.execute('INSERT OR IGNORE INTO image_ocr VALUES (?,?,?)',
                               (digest, engine_key, json.dumps(lines, ensure_ascii=False)))
                count += 1
            else:
                continue
            item.update(suggestions(catalog, names, lines, item['references'], calendar[digest]))
        gallery(output, [row for row in rows if not row['issue']])
        result = {'official_comparisons': compare_official(output, args.official_pairs, rows)
                  if args.official_pairs else [], 'decoded': sum(not row['issue'] for row in rows), 'new_ocr': count, 'issues': issues, 'images': rows}
        if args.review_set:
            reviews = json.loads(args.review_set.read_text(encoding='utf-8'))['reviews']
            result['validation'] = validate_ocr(reviews, rows)
        (output / 'candidates.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in result.items()}))


if __name__ == '__main__':
    main()
