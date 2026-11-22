"""Archived Japanese public availability events, independent of HDD flags."""
from collections import defaultdict
import csv
from datetime import date
from hashlib import sha256
import re

from old_hdd_evidence import normalize_name, wiki_rows, wiki_url
from old_hdd_timeline import batch, event, shift


def add_ultima_evidence(db, histories, path):
    if not path:
        return
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            day = row['first_release_date']
            url = row['first_release_source_url']
            digest = row['first_release_source_sha256']
            if not day or not url or not re.fullmatch(r'[0-9a-f]{64}', digest):
                continue
            songs = histories.by_title[normalize_name(row['title'])]
            if len(songs) != 1:
                continue
            charts = histories.songs[next(iter(songs))]
            chart = charts.get((4, 'ULT'))
            if not chart or chart[0] not in histories.first:
                continue
            identity = chart[0]
            reference = f'sdhd-ult:{digest}'
            db.execute('INSERT OR IGNORE INTO references_used VALUES (?,?,?,?)',
                       (reference, url, digest, 'Reviewed ULTIMA first-release evidence'))
            # The audit repeats the same first-release evidence per checkpoint.
            pointer = f'{row["first_release_version"]}: {row["title"]} / ULTIMA'
            histories.release_hints[identity].add((day, pointer, reference))
            if url.startswith('https://info-chunithm.sega.jp/wp-content/') and re.search(r'\.(jpg|jpeg|png|webp)$', url):
                db.execute('INSERT OR IGNORE INTO history_assets VALUES (?,?)', (digest, 'official_publicity'))
                db.execute('INSERT OR IGNORE INTO history_asset_links VALUES (?,?,?,?,?,?,?)',
                           (digest, url, 'official_ultima_announcement', reference, None, None, row['title']))


def read_availability(db, histories, path, calendar):
    if not path:
        return {}
    from bs4 import BeautifulSoup

    raw = path.read_bytes()
    source = 'sdhd-wiki-deleted'
    db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
               (source, wiki_url('wiki-deleted'), sha256(raw).hexdigest(), 'JP stop and restore archive'))
    document = BeautifulSoup(raw, 'html.parser').select_one('#content')
    if document is None:
        raise ValueError('Missing availability evidence content')
    starts = {version: start for version, start, _ in calendar}
    ordered = list(starts)
    boundaries = {version: starts[successor] for version, successor in zip(ordered, ordered[1:])}
    boundaries['PARADISE LOST'] = starts['NEW']
    changes = defaultdict(dict)
    current = None
    section = ''
    for table, cells, locator in wiki_rows(document, source):
        if table not in range(6, 15):
            continue
        if len(cells) == 1:
            section = cells[0]
            match = re.match(r'▼\s*(.+?)\s*まで', section)
            current = match[1].strip() if match else None
            continue
        if len(cells) < 5 or cells[0] == 'タイトル':
            current = None
            continue
        title, artist = map(normalize_name, cells[:2])
        stars = re.fullmatch(r'☆([1-5])', cells[4])
        matches = []
        for candidate in histories.by_title[title]:
            rows = histories.songs[candidate]
            if normalize_name(rows[None][2].get('artist') or '') != artist:
                continue
            if stars and any(key and key[1] == 'WE' and
                             normalize_name(value[2].get('tag') or '') == normalize_name(cells[3]) and
                             value[2].get('stars') == int(stars[1]) for key, value in rows.items()):
                matches.append(candidate)
            if not stars and rows[None][2].get('exType') == '0':
                matches.append(candidate)
        if len(matches) != 1:
            continue
        identity = matches[0]
        events = []
        if table in (7, 10, 14):
            for last_version, segment in zip(cells[-2].split(' ・ '), cells[-1].split(' ・ ')):
                dates = re.findall(r'(20\d\d)/(\d{1,2})/(\d{1,2})', segment)
                if dates:
                    restored, *last = [date(*map(int, d)).isoformat() for d in dates]
                    boundary = boundaries.get(last_version)
                    if boundary and boundary < restored:
                        events.append((boundary, 'removal_boundary_candidate'))
                    events.append((restored, 'restored'))
                    if last:
                        events.append((shift(last[0], 1), 'removed'))
        elif current in starts or current == 'PARADISE LOST':
            stamp = re.search(r'～(20\d\d)/(\d{1,2})/(\d{1,2})', section)
            if stamp:
                events.append((shift(date(*map(int, stamp.groups())).isoformat(), 1), 'removed'))
            elif current in boundaries:
                events.append((boundaries[current], 'removal_boundary_candidate'))
        for day, kind in events:
            edition = next((v for v, start, end in calendar if start <= day <= end), None)
            if edition:
                changes[identity].setdefault(day, set()).add((kind, edition, locator))
    verified = defaultdict(dict)
    for identity, days in sorted(changes.items()):
        for day, rows in sorted(days.items()):
            candidates = [row for row in rows if row[0] == 'removal_boundary_candidate']
            rows = {row for row in rows if row[0] != 'removal_boundary_candidate'}
            if candidates:
                version = min(row[1] for row in candidates)
                key = batch(db, f'day:{day}', version, 'public_day', 'Edition boundary', day=day)
                event(db, identity, key, 'removal_boundary_candidate', 'availability', None, 'removed',
                      None, None, ' | '.join(sorted(row[2] for row in candidates)))
                verified[identity][day] = 'removal_boundary_candidate'
            kinds = {row[0] for row in rows}
            if len(kinds) != 1:
                if kinds:
                    version = min(row[1] for row in rows)
                    db.execute('INSERT INTO sdhd_issues VALUES (?,?,?,?)',
                               (version, identity, 'conflicting_public_availability',
                                ' | '.join(sorted(f'{kind}: {locator}' for kind, _, locator in rows))))
                continue
            kind = next(iter(kinds))
            version = min(row[1] for row in rows)
            pointers = sorted(row[2] for row in rows)
            key = batch(db, f'day:{day}', version, 'public_day', 'Archived JP availability evidence', day=day)
            event(db, identity, key, kind, 'availability', None, kind, None, None,
                  ' | '.join(pointers), day=day)
            verified[identity][day] = kind
    return verified
