"""Historical evidence adapters for the old-cabinet audit."""
from collections import defaultdict
import csv
from datetime import date
from hashlib import sha256
import json
import re
from urllib.parse import quote

from import_paradise_lost import ROOT, TableParser
from _unibot_history.history import HistoryCatalog, normalize_name

WIKI_ROOT = 'https://wikiwiki.jp/chunithmwiki/'
WIKI_PAGES = {
    'wiki-p': 'CHUNITHM PARADISE情報まとめ',
    'wiki-lost': 'CHUNITHM PARADISE LOST情報まとめ',
    'wiki-aug': 'バージョンアップのお知らせ(2021.8.5)',
    'wiki-deleted': '配信終了・日本版未収録楽曲一覧',
    'wiki-we': "WORLD'S END",
    'wiki-cirno': 'チルノのパーフェクトさんすう教室',
    'wiki-p-dates': 'CHUNITHM PARADISE 楽曲一覧(追加日順)',
    'wiki-lost-dates': 'CHUNITHM PARADISE LOST 楽曲一覧(追加日順)',
}
DATE_EDITIONS = {'wiki-p-dates': 'PARADISE', 'wiki-lost-dates': 'PARADISE LOST'}
for number, version in enumerate(('CHUNITHM','PLUS','AIR','AIR PLUS','STAR','STAR PLUS','AMAZON',
                                  'AMAZON PLUS','CRYSTAL','CRYSTAL PLUS'), 1):
    key = f'wiki-dates-{number:02}'
    name = version if version == 'CHUNITHM' else 'CHUNITHM ' + version
    WIKI_PAGES[key] = name + ' 楽曲一覧(追加日順)'
    DATE_EDITIONS[key] = version


def wiki_url(key):
    return WIKI_ROOT + quote(WIKI_PAGES[key], safe='')


def wiki_rows(document, reference):
    """Yield cells with carried rowspan values and a reproducible table locator."""
    for table_no, table in enumerate(document.select('table'), 1):
        carried = {}
        for row_no, row in enumerate(table.select('tr'), 1):
            if row.find_parent('table') != table:
                continue
            values = []
            cells = row.find_all(['td', 'th'], recursive=False)
            for cell in cells:
                while len(values) in carried:
                    values.append(carried[len(values)][0])
                text = cell.get_text(' ', strip=True)
                if re.fullmatch(r'[0-9０-９]+[+＋]?', text):
                    text = normalize_name(text)
                if int(cell.get('rowspan', '1')) > 1:
                    carried[len(values)] = (text, int(cell['rowspan']))
                values.append(text)
            while len(values) in carried:
                values.append(carried[len(values)][0])
            carried = {k: (v, n - 1) for k, (v, n) in carried.items() if n > 1}
            yield table_no, values, f'{reference}:table {table_no}, row {row_no}'


class PublicEvidence:
    def __init__(self, directory, db):
        self.we = defaultdict(list)
        self.release = defaultdict(list)
        self.we_release = defaultdict(list)
        self.removed = defaultdict(list)
        self.levels = defaultdict(list)
        self.max_titles = {}
        self.transition = False
        self.constants = {}
        if directory is None:
            return
        from bs4 import BeautifulSoup

        for key in WIKI_PAGES:
            path = directory / f'{key}.html'
            if not path.is_file():
                continue
            data = path.read_bytes()
            document = BeautifulSoup(data, 'html.parser').select_one('#content')
            if document is None:
                raise ValueError(f'Missing Wiki content: {key}')
            url = wiki_url(key)
            db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                       (key, url, sha256(data).hexdigest(), 'Wiki archived HTML, locators relative to #content'))
            if key == 'wiki-lost':
                text = document.get_text(' ', strip=True)
                self.transition = ('更新されていないもの' in text and '各楽曲のレベル・譜面定数の変更' in text)
            if key == 'wiki-cirno':
                self._cirno(document, url)
            elif key in DATE_EDITIONS:
                self._dates(key, wiki_rows(document, key))
            else:
                self._page(key, wiki_rows(document, key))
        max_path = directory / 'max-20211102.html'
        if max_path.is_file():
            data = max_path.read_bytes()
            lock = next(x for x in source_lock()['artifacts'] if x['id'] == 'max-lost')
            if sha256(data).hexdigest() != lock['sha256']:
                raise ValueError('MAX 2021-11-02 source differs from its existing lock')
            parser = TableParser()
            parser.feed(data.decode('utf-8'))
            parser.close()
            self.max_titles = {normalize_name(x['title']): f'max-lost:row {n}' for n, x in enumerate(parser.rows, 1)}
            db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                       ('max-lost', lock['url'], lock['sha256'], 'Ordinary list observed 2021-11-02'))
        end = directory / 'sega-end.html'
        if end.is_file():
            db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                       ('sega-end', 'https://info-chunithm.sega.jp/4017/', sha256(end.read_bytes()).hexdigest(),
                        'SEGA: separate legacy online end'))

    def _cirno(self, document, url):
        title = WIKI_PAGES['wiki-cirno']
        for info in document.select('h4'):
            if info.get_text(strip=True) != 'レベル・譜面定数変更履歴':
                continue
            for sibling in info.next_siblings:
                if sibling.name in ('h2', 'h3', 'h4'):
                    break
                if sibling.name is None:
                    continue
                text = sibling.get_text(' ', strip=True)
                found = re.search(r'譜面定数\s+MAS\s+【[^】]*?(\d{1,2})\.(\d)\(PLUS\)', text)
                if found:
                    whole, tenths = map(int, found.groups())
                    self.constants['PLUS', 96, normalize_name(title), 'MAS'] = (
                        whole * 100 + tenths * 10, url, 'レベル・譜面定数変更履歴 / MAS / PLUS')
                break

    def _page(self, reference, rows):
        section = ''
        last_table = 0
        ordinary = reference in ('wiki-p', 'wiki-lost', 'wiki-aug')
        ordinary_table = 0
        for table, cells, locator in rows:
            if table != last_table:
                last_table, section = table, ''
            if cells[:3] == ['ジャケ', 'タイトル', 'アーティスト'] and cells[3:] == ['B', 'A', 'E', 'M']:
                ordinary_table += 1
            if len(cells) == 1 and cells[0].startswith('▼'):
                section = cells[0]
            if ordinary and len(cells) == 7 and all(re.fullmatch(r'\d{1,2}\+?', c) for c in cells[3:]):
                title = normalize_name(cells[1])
                if reference == 'wiki-aug':
                    self.removed[title, '', None].append(('2021-08-05', locator))
                elif ordinary_table == 1:
                    version = 'PARADISE' if reference == 'wiki-p' else 'PARADISE LOST'
                    self.release[title].append((version, locator, None))
                    for diff, level in zip(('BAS', 'ADV', 'EXP', 'MAS'), cells[3:]):
                        self.levels[version, title, diff].append((level, locator))
            if reference == 'wiki-deleted' and len(cells) == 7 and 'PARADISE LOST まで' in section:
                if '2021/8/4' in section:
                    self.removed[normalize_name(cells[0]), '', None].append(('2021-08-05', locator))
                if '2021/11/3' in section:
                    self.removed[normalize_name(cells[0]), '', None].append(('2021-11-04', locator))
            if reference == 'wiki-we' and len(cells) >= 3:
                self._we(cells[0], cells[1], cells[2], locator)
            if reference == 'wiki-deleted' and len(cells) in (5, 7):
                self._we(cells[0], cells[3], cells[4], locator)
            if reference in ('wiki-lost', 'wiki-aug') and len(cells) == 5 and re.fullmatch(r'☆[1-5]', cells[4]):
                title, tag = normalize_name(cells[1]), normalize_name(cells[3])
                self._we(cells[1], cells[3], cells[4], locator)
                self.removed[title, tag, int(cells[4][1:])].append(
                    ('2021-05-13' if reference == 'wiki-lost' else '2021-08-05', locator))

    def _dates(self, reference, rows):
        version = DATE_EDITIONS[reference]
        day = None
        for _, cells, locator in rows:
            if len(cells) == 1:
                stamp = re.match(r'(20\d{2})/(\d{1,2})/(\d{1,2})\b', cells[0])
                if stamp:
                    day = date(*map(int, stamp.groups())).isoformat()
                continue
            if day is None or len(cells) < 5:
                continue
            title = normalize_name(re.sub(r'\s*\*\d+$', '', cells[1]))
            if re.fullmatch(r'WE(?:\s*\*\d+)?', cells[0]) and len(cells) == 5 and re.fullmatch(r'☆[1-5]', cells[4]):
                tag, stars = normalize_name(cells[3]), int(cells[4][1:])
                self.we_release[title, tag, stars].append((version, locator, day))
                self._we(title, tag, cells[4], locator)
            elif len(cells) == 7 and all(re.fullmatch(r'\d{1,2}\+?', c) for c in cells[3:]):
                self.release[title].append((version, locator, day))
                for diff, level in zip(('BAS', 'ADV', 'EXP', 'MAS'), cells[3:]):
                    self.levels[version, title, diff].append((level, locator))

    def first_release(self, title, tag, stars):
        rows = self.we_release.get((title, tag, stars), ()) if tag else self.release.get(title, ())
        return min(rows, key=lambda row: (row[2] is None, row[2] or '', row[1])) if rows else (None, None, None)

    def removal(self, title, tag, stars):
        rows = self.removed.get((title, tag, stars), ())
        return rows[0] if rows and len({day for day, _ in rows}) == 1 else (None, None)

    def _we(self, title, tag, stars, locator):
        if tag and re.fullmatch(r'☆[1-5]', stars):
            self.we[normalize_name(title), normalize_name(tag)].append((int(stars[1:]), locator))


def source_lock():
    return json.loads((ROOT / 'tools/history_inputs.json').read_text(encoding='utf-8'))


class B5Evidence:
    def __init__(self, path, output):
        self.catalog = HistoryCatalog(path) if path is not None else None
        self.sources = {x['id']: x for x in source_lock()['artifacts']}
        self.b51_rows = set()
        if self.catalog is None:
            return
        path = self.catalog.path
        with path.open('rb') as file:
            digest = sha256()
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                digest.update(chunk)
        output.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                       ('B5', f'local:{path.parent.name}/{path.name}', digest.hexdigest(), 'Explicit immutable baseline'))
        path = ROOT / 'docs/b51-continuity/b51_continuity_candidates.csv'
        data = path.read_bytes()
        output.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                       ('B5.1', 'local:docs/b51-continuity/b51_continuity_candidates.csv', sha256(data).hexdigest(),
                        'Carry candidates compared by chart revision and edition'))
        for row in csv.DictReader(data.decode('utf-8-sig').splitlines()):
            self.b51_rows.add((row['version'], row['revision_id']))

    def snapshot(self, version):
        rows = defaultdict(list)
        if self.catalog is not None:
            for row in self.catalog.get_snapshot('JP', version)['charts']:
                rows[normalize_name(row['title']), row['difficulty']].append(row)
        return rows

    def compare(self, db, version, song, chart, constant, snapshot, unique, state, reviewed=None):
        candidates = snapshot.get((normalize_name(song.title), chart.difficulty), ())
        row = candidates[0] if unique and len(candidates) == 1 else None
        if row is None:
            if self.catalog is not None:
                db.execute('INSERT INTO unmatched_records VALUES (?,?,?,?,?,?,?)',
                           (version, song.music_id, song.title, chart.difficulty, song.source,
                            'b5_ambiguous' if candidates else 'b5_missing',
                            json.dumps([r['revision_id'] for r in candidates])))
            return None, None, False
        trusted = row['constant_tenths'] * 10 if row['trusted_constant'] is not None else None
        known_gap = row['constant_kind'] in ('unknown', 'display_only')
        wiki_value, wiki_url_, wiki_locator = None, None, None
        if constant is None:
            result = 'hdd_unknown'
        elif trusted == constant:
            result = 'same'
        elif trusted is not None:
            result = 'conflict'
            artifact = self.sources.get(row['constant_artifact'], {})
            url = artifact.get('url', '')
            # Preserve historical Wiki counterevidence alongside the HDD value.
            # A contemporary unqualified value is not evidence for this edition.
            if reviewed is not None:
                wiki_value, wiki_url_, wiki_locator = reviewed
            elif url.startswith(WIKI_ROOT) and row['constant_artifact'] in ('genesis-evidence', 'daydream-evidence'):
                wiki_value, wiki_url_, wiki_locator = trusted, url, row['constant_locator']
        elif known_gap and state == 'present':
            if row['state'] == 'playable':
                result = 'b51_unknown_hdd_value' if (version, row['revision_id']) in self.b51_rows else 'b5_unknown_hdd_value'
            elif row['state'] == 'unknown':
                result = 'b5_availability_unknown_hdd_value'
            else:
                result = 'b5_unavailable_hdd_value'
        else:
            result = 'b5_unconfirmed'
        db.execute('INSERT INTO comparison VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (version, song.music_id, song.title, chart.difficulty, song.source, row['revision_id'],
                    constant, trusted, row['state'], row.get('constant_status', row['constant_kind']), result, wiki_value, wiki_url_,
                    wiki_locator, 'unique normalized ordinary title and difficulty; B5 revision is not C2S certified'))
        return row['revision_id'], wiki_value, result == 'conflict'
