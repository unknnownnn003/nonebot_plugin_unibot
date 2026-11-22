"""Historical evidence adapters for the old-cabinet audit."""
from collections import defaultdict
from datetime import date, timedelta
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
        reviewed = json.loads((ROOT / 'tools/old_hdd_reviewed_dates.json').read_text(encoding='utf-8'))
        self.reviewed_sources = reviewed['references']
        self.reviewed_releases = {row['source']: row for row in reviewed['releases']}
        self.we = defaultdict(list)
        self.release = defaultdict(list)
        self.we_release = defaultdict(list)
        self.removed = defaultdict(list)
        self.restored = defaultdict(list)
        self.boundary_removed = defaultdict(list)
        self.dated_rows = defaultdict(list)
        self.levels = defaultdict(list)
        self.max_titles = {}
        self.transition = False
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
            if key in DATE_EDITIONS:
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

    def _page(self, reference, rows):
        section = ''
        last_table = 0
        headers = []
        ordinary = reference in ('wiki-p', 'wiki-lost', 'wiki-aug')
        ordinary_table = 0
        for table, cells, locator in rows:
            if table != last_table:
                last_table, section = table, ''
                headers = cells
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
                self._deleted(cells, locator, section, headers)
            if reference in ('wiki-lost', 'wiki-aug') and len(cells) == 5 and re.fullmatch(r'☆[1-5]', cells[4]):
                title, tag = normalize_name(cells[1]), normalize_name(cells[3])
                self._we(cells[1], cells[3], cells[4], locator)
                self.removed[title, tag, int(cells[4][1:])].append(
                    ('2021-05-13' if reference == 'wiki-lost' else '2021-08-05', locator))

    def _deleted(self, cells, locator, section, headers):
        from history_calendar import JP_RELEASES

        stars = re.fullmatch(r'☆([1-5])', cells[4]) if len(cells) >= 5 and '属性' in headers else None
        if '属性' in headers and not stars:
            return
        title = normalize_name(cells[0])
        key = (title, normalize_name(cells[3]) if stars else '', int(stars[1]) if stars else None)
        if headers[-2:] == ['前配信停止ver.', '復活日付']:
            boundaries = {version: JP_RELEASES[i + 1][1] for i, (version, _, _) in enumerate(JP_RELEASES[:12])}
            for version, period in zip(cells[-2].split(' ・ '), cells[-1].split(' ・ '), strict=False):
                days = re.findall(r'(20\d\d)/(\d{1,2})/(\d{1,2})', period)
                if days:
                    first, *last = [date(*map(int, value)) for value in days]
                    self.restored[key].append((first.isoformat(), locator))
                    if last:
                        self.removed[key].append(((last[0] + timedelta(days=1)).isoformat(), locator))
                    boundary = boundaries.get(version)
                    if boundary and boundary < first.isoformat():
                        self.boundary_removed[key].append((boundary, locator))
            return
        if headers[:3] != ['タイトル', 'アーティスト', 'BPM'] or not section:
            return
        section_version = re.match(r'▼\s*(.+?)\s*まで', section)
        stamp = re.search(r'～(20\d\d)/(\d{1,2})/(\d{1,2})', section)
        if stamp:
            self.removed[key].append(((date(*map(int, stamp.groups())) + timedelta(days=1)).isoformat(), locator))
        elif section_version:
            for index, (version, _start, _source) in enumerate(JP_RELEASES[:12]):
                if version == section_version[1]:
                    self.boundary_removed[key].append((JP_RELEASES[index + 1][1], locator))

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
                self.dated_rows[title, tag, stars].append((version, locator, day, cells[2]))
                self._we(title, tag, cells[4], locator)
            elif len(cells) == 7 and all(re.fullmatch(r'\d{1,2}\+?', c) for c in cells[3:]):
                self.release[title].append((version, locator, day))
                self.dated_rows[title, '', None].append((version, locator, day, cells[2]))
                for diff, level in zip(('BAS', 'ADV', 'EXP', 'MAS'), cells[3:]):
                    self.levels[version, title, diff].append((level, locator))

    def first_release(self, title, tag, stars):
        rows = self.we_release.get((title, tag, stars), ()) if tag else self.release.get(title, ())
        return min(rows, key=lambda row: (row[2] is None, row[2] or '', row[1])) if rows else (None, None, None)

    def reviewed_release(self, music_id, paths):
        matches = [self.reviewed_releases[path] for path in sorted(set(paths)) if path in self.reviewed_releases]
        if len(matches) > 1 or any(row['music_id'] != music_id for row in matches):
            raise ValueError(f'Ambiguous reviewed release for HDD music ID {music_id}')
        return matches[0] if matches else None

    def reviewed_detail(self, row):
        detail = dict(row)
        for field in ('reference', 'secondary_reference', 'official_post'):
            if field in detail:
                detail[field] = self.reviewed_sources[detail[field]]
        if 'rejected_restoration' in detail:
            rejected = dict(detail['rejected_restoration'])
            rejected['reference'] = self.reviewed_sources[rejected['reference']]
            detail['rejected_restoration'] = rejected
        return detail

    def removal(self, title, tag, stars):
        rows = self.removed.get((title, tag, stars), ())
        return rows[0] if rows and len({day for day, _ in rows}) == 1 else (None, None)

    def _we(self, title, tag, stars, locator):
        if tag and re.fullmatch(r'☆[1-5]', stars):
            self.we[normalize_name(title), normalize_name(tag)].append((int(stars[1:]), locator))


def source_lock():
    return json.loads((ROOT / 'tools/history_inputs.json').read_text(encoding='utf-8'))
