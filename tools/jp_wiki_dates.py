"""Interpret dated JP Wiki song witnesses without importing chart metadata."""
from datetime import date
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import quote

BASE = 'https://wikiwiki.jp/chunithmwiki/'
DATE = re.compile(r'(?<!\d)(?:(\d{4})[/.\-年])?(\d{1,2})[/.\-月](\d{1,2})(?:日)?(?!\d)')
LABEL = re.compile(r'(?:WORLD[’\x27]S\s*END|(?<![A-Za-z])WE(?![A-Za-z]))', re.I)
VERSION = re.compile(r'\b(?:X-VERSE-X|X-VERSE|VERSE|MATE|LUMINOUS|SUN|NEW|PARADISE|CRYSTAL|AMAZON|STAR|AIR)(?:\s+(?:PLUS|LOST))?\b', re.I)


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value or '').casefold() if c.isalnum())


def page_url(title):
    return BASE + quote(title, safe='')


def plain(value):
    value = unescape(value).replace('&br;', '\n')
    value = re.sub(r'\(\(.*?\)\)', '', value)
    value = re.sub(r'\[\[([^>\]]+)(?:>[^\]]+)?\]\]', r'\1', value)
    markup = re.compile(r'&(?:color|size|ruby)\([^)]*\)\{([^{}]*)\};')
    while markup.search(value):
        value = markup.sub(r'\1', value)
    return re.sub(r"(?:BGCOLOR|COLOR)\([^)]*\):|''", '', value)


class DetailHTML(HTMLParser):
    """Extract only visible PukiWiki body headings, table rows and prose."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = []
        self.depth = 0
        self.line = []
        self.heading = False
        self.row = False
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            self.depth += bool(self.depth or dict(attrs).get('id') == 'body')
        if not self.depth:
            return
        if tag in ('script', 'style'):
            self.skip = True
        if tag in ('h2', 'h3', 'h4'):
            self.flush()
            self.heading = True
        if tag == 'tr':
            self.flush()
            self.row = True
        if tag == 'br':
            self.line.append('&br;')

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in ('script', 'style'):
            self.skip = False
        if tag in ('td', 'th') and self.row:
            self.line.append('|')
        if tag in ('h2', 'h3', 'h4', 'tr', 'li', 'p', 'div'):
            self.flush()
        if tag == 'div':
            self.depth -= 1

    def handle_data(self, value):
        if self.depth and not self.skip and value.strip():
            self.line.append(value.strip())

    def flush(self):
        value = ''.join(self.line).strip()
        if value:
            prefix = '*' if self.heading else '|~' if self.row else ''
            self.body.append(prefix + value)
        self.line = []
        self.heading = False
        self.row = False


def classify(text, header):
    if re.search(r'(?:移植先|移植元|海外版|国際版|アジア版|中国版|CHUNITHM以外|maimai|ロケテスト)', text, re.I):
        return 'other_game'
    if re.search(r'配信再?停止|配信終了|削除された\s*$', text):
        return 'removed'
    if re.search(r'復活(?:\s*$|\s*[(（]|として)|再配信', text):
        return 'restored'
    if re.search(r'通常解禁|一般解禁|通常開放|通常配信に(?:移行|変更)|常駐化', text):
        return 'general_unlock'
    if re.search(r'先行(?:配信|解禁|追加)(?!曲用|譜面用)', text):
        return 'advance_release'
    if '全国解禁ポイント' in text and '解禁曲' in text:
        return 'first_release'
    if re.search(r'譜面.{0,16}(?:修正|変更|修訂)|(?:修正|変更)された譜面', text):
        return 'chart_revision'
    if re.search(r'(?:初めて)?(?:プレイ|選曲)(?:可能|できる)(?:に|ように)|プレイ可能日', text):
        return 'first_playable'
    if re.search(r'新曲追加|初(?:出|回追加|登場)|配信開始|初配信|追加|実装', text):
        if re.search(r'(?:イベント|マップ|コース)(?:が|を|の)?(?:追加|開催|開始|開放)', text):
            return 'event_open'
        return 'first_release'
    if '通常配信' in text:
        return 'regular_distribution'
    if re.search(r'(?:イベント|マップ|コース).{0,20}(?:開放|開催|開始)', text):
        return 'event_open'
    if header and re.search(r'課題曲|解禁|イベント|マップ', text):
        return 'conditional_access'
    return 'unclassified'


def parse(lines):
    """Keep dated clauses with their original row, heading and date spelling."""
    title = artist = ''
    section = '楽曲情報'
    result = []
    for number, original in sorted(lines.items(), key=lambda item: int(item[0])):
        line = original.strip()
        if not line or line.startswith('//'):
            continue
        if line.startswith('*'):
            section = re.sub(r'\s*\[#.*', '', line.lstrip('* ').strip())
            continue
        header = re.match(r'^\|\s*~?(タイトル|曲名|アーティスト|解禁方法|配信日|追加日)\|(?:>\|)*(.+?)\|', line)
        if header and header[1] in ('タイトル', '曲名', 'アーティスト'):
            if header[1] == 'アーティスト':
                artist = plain(header[2])
            else:
                title = plain(header[2])
            continue
        if header and header[1] in ('配信日', '追加日') and section != '楽曲情報':
            continue
        if not header and (line.startswith('|') or not re.search(r'情報|配信|履歴|変更|備考|解禁|詳細', section)):
            continue
        value = plain(header[2] if header else line)
        for clause in re.split(r'[。\n；;]', value):
            matches = list(DATE.finditer(clause))
            until = False
            for index, match in enumerate(matches):
                year, month, day = match.groups()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
                between = clause[match.end():end]
                dated = between.strip()
                prefix = clause[:match.start()] if index == 0 else ''
                range_start = index + 1 < len(matches) and dated in ('～', '〜', '-', '–')
                scope = prefix + (clause[matches[index + 1].end():] if range_start else between)
                chart_context = scope + (clause[:match.start()] if index else '')
                difficulty = 'WE' if LABEL.search(chart_context) else 'ULT' if re.search(
                    r'(?<![A-Za-z])ULT(?:IMA)?(?![A-Za-z])', chart_context, re.I) else None
                kind = classify(scope, bool(header))
                if header and header[1] in ('配信日', '追加日') and kind == 'unclassified':
                    kind = 'first_release'
                if until:
                    kind = 'period_end'
                elif index and kind == 'regular_distribution' and re.search(r'→|⇒', clause[:match.start()]):
                    kind = 'general_unlock'
                until = range_start
                if kind in ('unclassified', 'other_game'):
                    continue
                try:
                    resolved = date(int(year), int(month), int(day)).isoformat() if year else None
                except ValueError:
                    resolved = None
                version = VERSION.search(scope)
                result.append(dict(line=int(number), section=section, raw=original, date_text=match[0],
                                   day=resolved, kind=kind, difficulty=difficulty,
                                   version=version[0].upper() if version else None))
    return title, artist, result
