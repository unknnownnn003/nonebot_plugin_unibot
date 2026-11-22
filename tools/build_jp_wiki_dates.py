"""Build an auditable JP release-date catalog from cached Wiki song pages."""
import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from apply_hdd_notice_reviews import normal_launch_witnesses
from hdd_image_inventory import open_catalog
from jp_wiki_dates import BASE, DetailHTML, normalize, page_url, parse
from old_hdd_scan import encoded
from old_hdd_timeline import batch, event

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jp_wiki_pages (
    url TEXT, digest TEXT, title TEXT, obtained_at TEXT, format TEXT NOT NULL,
    line_count INTEGER, PRIMARY KEY(url,digest)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS jp_wiki_dates (
    url TEXT, digest TEXT, identity TEXT REFERENCES history_entities,
    line INTEGER, date_text TEXT, kind TEXT, day TEXT, status TEXT NOT NULL,
    evidence TEXT NOT NULL, PRIMARY KEY(url,digest,identity,line,kind,date_text),
    FOREIGN KEY(url,digest) REFERENCES jp_wiki_pages
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS jp_date_review (
    identity TEXT REFERENCES history_entities, day TEXT, source TEXT, status TEXT,
    previous TEXT, evidence TEXT NOT NULL, PRIMARY KEY(identity,day,source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS jp_wiki_review (
    url TEXT, title TEXT, status TEXT, detail TEXT, PRIMARY KEY(url,title)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS jp_wiki_unmatched (
    url TEXT, digest TEXT, line INTEGER, date_text TEXT, kind TEXT, evidence TEXT NOT NULL,
    PRIMARY KEY(url,digest,line,date_text,kind), FOREIGN KEY(url,digest) REFERENCES jp_wiki_pages
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS jp_date_identity ON jp_wiki_dates(identity,kind,day);
CREATE INDEX IF NOT EXISTS jp_history_parent ON history_entities(parent,difficulty);
CREATE VIEW IF NOT EXISTS jp_date_evidence AS
    SELECT identity, exact_on AS day, 'first_release' AS kind, basis AS source, 'selected' AS status
      FROM sdhd_first_release WHERE exact_on IS NOT NULL
    UNION ALL SELECT identity,day,'first_release',coalesce(reference,locator),'historical_claim'
      FROM sdhd_release_evidence
    UNION ALL SELECT identity,effective_on,kind,new_source,'timeline' FROM history_events
      WHERE effective_on IS NOT NULL AND kind IN
          ('released','superseded_release_claim','general_open','route_opened','removed','restored','hdd_revision')
    UNION ALL SELECT identity,day,kind,url,status FROM jp_wiki_dates
    UNION ALL SELECT identity,day,json_extract(evidence,'$.kind'),source,status FROM jp_date_review
      WHERE json_extract(evidence,'$.kind') IS NOT NULL;
'''
RELEASE = {'first_release', 'advance_release'}
SONGS = '''SELECT DISTINCT e.identity,json_extract(r.payload,'$.title') AS title,
                  json_extract(r.payload,'$.artist') AS artist,
                  CAST(json_extract(r.payload,'$.exType') AS INTEGER) AS ex_type
           FROM history_entities e JOIN history_revisions r USING(identity)
           WHERE e.kind='song' AND json_extract(r.payload,'$.title') IS NOT NULL'''
TARGETS = '''
    SELECT e.*,p.exact_on,p.basis,h.effective_on AS historic_exact,h.new_source AS first_source,
           b.version AS first_version
    FROM history_entities e LEFT JOIN sdhd_first_release p USING(identity)
    LEFT JOIN history_events h ON h.event=(
        SELECT event FROM history_events WHERE identity=e.identity AND
          kind IN ('first_hdd_observation','file_observed','released')
        ORDER BY CASE WHEN kind='first_hdd_observation' THEN 0 WHEN kind='file_observed' THEN 1 ELSE 2 END,event LIMIT 1)
    LEFT JOIN history_batches b ON b.batch=h.batch
'''


def song_index(db):
    songs = defaultdict(list)
    for row in db.execute(SONGS):
        songs[normalize(row['title'])].append(row)
    return songs


def artist_match(wiki, hdd):
    def trim_credit(name):
        credit = re.split(r'[「『]', name, maxsplit=1)[0]
        credit = re.sub(r'(?:曲|歌)\s*[：:]', '', credit)
        return normalize(credit)

    wiki, hdd = trim_credit(wiki), trim_credit(hdd)
    return bool(wiki and wiki == hdd)


def detail_targets(db, index, title, artist, difficulty, raw):
    rows = index.get(normalize(title), ())
    same = {r['identity'] for r in rows if r['ex_type'] == (2 if difficulty == 'WE' else 0)}
    matches = {r['identity'] for r in rows if r['identity'] in same and artist_match(artist, r['artist'] or '')}
    if difficulty == 'WE':
        charts = db.execute('SELECT DISTINCT chart_identity,identity,tag FROM worlds_end_versions WHERE tag IS NOT NULL')
        explicit = {r['chart_identity'] for r in charts if r['identity'] in matches and re.search(
            r'(?:WE|WORLD[’\x27]S\s*END)(?:\s*譜面)?[\s【「〈(（]*' + re.escape(r['tag']) + r'(?=[\s】」〉)）・、]|譜面)', raw, re.I)}
        return explicit
    if difficulty is None:
        return matches
    return {r[0] for parent in matches for r in db.execute(
        'SELECT identity FROM history_entities WHERE parent=? AND difficulty=?', (parent, difficulty))}


def release_state(db, identity):
    indexed = db.execute('SELECT * FROM sdhd_first_release WHERE identity=?', (identity,)).fetchone()
    history = db.execute('''SELECT effective_on,evidence FROM history_events WHERE identity=? AND kind='released'
                         AND effective_on IS NOT NULL ORDER BY effective_on,event''', (identity,)).fetchall()
    dates = {row['effective_on'] for row in history}
    if indexed and indexed['exact_on']:
        dates.add(indexed['exact_on'])
    return indexed, dates


def selected_release(db, identity):
    indexed, dates = release_state(db, identity)
    if len(dates) == 1:
        return next(iter(dates)), indexed['basis'] if indexed else 'legacy_timeline'
    return None, None


def is_official(db, identity, day, basis):
    if basis and ('official' in basis or 'sega' in basis):
        return True
    return db.execute('''
        SELECT 1 FROM sdhd_release_evidence WHERE identity=? AND day=? AND
          (reference LIKE 'https://info-chunithm.sega.jp/%' OR reference LIKE 'https://chunithm.sega.jp/%')
        UNION ALL
        SELECT 1 FROM jp_date_review WHERE identity=? AND day=? AND
          (source LIKE 'https://info-chunithm.sega.jp/%' OR source LIKE 'https://chunithm.sega.jp/news/%')
          AND status IN ('adopted','corroborated','corrected_official_date')
          AND json_extract(evidence,'$.kind') IN ('first_release','advance_release')
        LIMIT 1''', (identity, day, identity, day)).fetchone() is not None


def record_review(db, identity, day, source, status, previous, evidence):
    db.execute('''INSERT INTO jp_date_review VALUES (?,?,?,?,?,?)
                  ON CONFLICT(identity,day,source) DO UPDATE
                  SET evidence=json_patch(jp_date_review.evidence,excluded.evidence)
                  WHERE excluded.evidence<>'{}'
                    AND (jp_date_review.status=excluded.status OR excluded.status='corrected') ''',
               (identity, day, source, status, previous, encoded(evidence)))


def adopt(db, identity, day, source, basis, witness, *, replaces=None, correction='corrected_general_unlock', parent=None):
    indexed, known = release_state(db, identity)
    if known == {day}:
        return 'corroborated'
    if known and known != {replaces}:
        record_review(db, identity, day, source, 'conflict_existing_date', encoded(sorted(known)), {})
        return 'conflict_existing_date'
    edition = db.execute('SELECT * FROM versions WHERE ? BETWEEN start_on AND end_on', (day,)).fetchone()
    if edition is None:
        return 'outside_version_calendar'
    if replaces:
        record_review(db, identity, day, source, correction, encoded(dict(indexed)), {'old_day': replaces})
        db.execute("""UPDATE history_events SET kind='superseded_release_claim'
                      WHERE identity=? AND kind='released' AND effective_on=?""", (identity, replaces))
    if indexed:
        db.execute('UPDATE sdhd_first_release SET earliest_on=?,latest_on=?,exact_on=?,basis=? WHERE identity=?',
                   (day, day, day, basis, identity))
        db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)', (identity, day, basis, source))
    day_batch = batch(db, 'day:' + day, edition['version'], 'public_day', 'JP release-date audit', day=day)
    proof = {'source': source, 'basis': basis, 'hdd_witness': witness, 'parent': parent}
    event(db, identity, day_batch, 'released', 'availability', None, 'released', None, witness,
          encoded(proof), day=day)
    # Keep lifecycle routes, candidate revisions, and dates from other editions.
    # A historical date observed before the available HDD data cannot certify a revision.
    db.execute('DELETE FROM history_windows WHERE version=? AND identity=? AND end_on<?',
               (edition['version'], identity, day))
    db.execute('UPDATE history_windows SET start_on=? WHERE version=? AND identity=? AND start_on<?',
               (day, edition['version'], identity, day))
    return 'corrected' if replaces else 'adopted'


def import_pages(db, pages, failures):
    by_identity = defaultdict(list)
    index = song_index(db)
    stats = Counter()
    for failure in failures:
        db.execute('INSERT OR REPLACE INTO jp_wiki_review VALUES (?,?,?,?)',
                   (failure['url'], failure['title'], failure['status'], failure.get('detail', '')))
    for page in sorted(pages, key=lambda p: (p['url'], p['obtained_at'])):
        url = page['url']
        if not url.startswith(BASE) or urlsplit(url).query:
            raise ValueError('Wiki page must be a public CHUNITHM detail URL')
        digest = sha256(encoded(page['lines']).encode()).hexdigest()
        title, artist, records = parse(page['lines'])
        reading = re.fullmatch(r'(.+?)\s*[(（][ぁ-ゖァ-ヺー・ ]+[)）]', title)
        if reading and normalize(reading[1]) == normalize(page['title']):
            title = reading[1].rstrip()
        db.execute('INSERT OR IGNORE INTO jp_wiki_pages VALUES (?,?,?,?,?,?)',
                   (url, digest, page['title'], page['obtained_at'], page.get('format', 'indexed_source_excerpt'), len(page['lines'])))
        if not title or not artist:
            status = 'missing_song_metadata'
        elif not index.get(normalize(title)):
            status = 'title_not_in_hdd'
        else:
            status = 'parsed'
            for record in records:
                targets = detail_targets(db, index, title, artist, record['difficulty'], record['raw'])
                if not targets:
                    stats['unmatched_dated_clauses'] += 1
                    db.execute('INSERT OR IGNORE INTO jp_wiki_unmatched VALUES (?,?,?,?,?,?)',
                               (url, digest, record['line'], record['date_text'], record['kind'], encoded(record)))
                for identity in sorted(targets):
                    row = record | {'url': url, 'digest': digest, 'title': title, 'artist': artist,
                                    'identity': identity, 'match': 'exact normalized HDD title and artist credit'}
                    row['status'] = 'ambiguous_identity' if len(targets) != 1 else (
                        'missing_full_date' if not row['day'] else 'candidate')
                    by_identity[identity].append(row)
        db.execute('INSERT OR REPLACE INTO jp_wiki_review VALUES (?,?,?,?)',
                   (url, page['title'], status, title or 'No verified song information table'))
        stats[status] += 1
    return by_identity, stats


def choose_release(db, identity, rows, target):
    usable = [r for r in rows if r['status'] == 'candidate']
    explicit = {r['day'] for r in usable if r['kind'] in RELEASE}
    unlock = {r['day'] for r in usable if r['kind'] == 'general_unlock'}
    unlock.update(r[0] for r in db.execute('''SELECT effective_on FROM history_events
                   WHERE identity=? AND kind='general_open' AND effective_on IS NOT NULL
                   UNION SELECT day FROM jp_date_review WHERE identity=?
                   AND json_extract(evidence,'$.kind')='general_unlock' ''', (identity, identity)))
    regular = {r['day'] for r in usable if r['kind'] == 'regular_distribution'}
    if len(explicit) == 1:
        early = next(iter(explicit))
        unlock |= {day for day in regular if day > early}
    else:
        early = None
    old, basis = selected_release(db, identity)
    official = is_official(db, identity, old, basis)
    raw_days = {r[0] for r in db.execute('SELECT day FROM sdhd_release_evidence WHERE identity=? AND reference IS NULL',
                                        (identity,))}
    for row in usable:
        day, kind = row['day'], row['kind']
        if kind == 'regular_distribution' and day in unlock:
            row['kind'] = 'general_unlock'
        elif kind == 'regular_distribution' and not explicit:
            if day == old:
                row['status'] = 'agrees_official' if official else 'agrees_legacy_date'
            elif raw_days == {day}:
                row['status'] = 'corroborated_hdd_candidate'
            else:
                row['status'] = 'regular_distribution_needs_launch_evidence'
        if row['kind'] == 'general_unlock':
            row['status'] = 'separate_event' if day != old else 'matches_selected_release_needs_review'
        if kind not in RELEASE and row['status'] != 'corroborated_hdd_candidate':
            continue
        if len(explicit) > 1:
            row['status'] = 'conflicting_wiki_dates'
        elif day == old:
            row['status'] = 'agrees_official' if official else 'agrees_legacy_date'
        else:
            indexed, _ = release_state(db, identity)
            replaces = old if (indexed and not official and basis == 'dated_release_evidence'
                                and old in unlock and day < old) else None
            # Ordinary distribution alone needs a corroborating HDD date and the
            # absence of an earlier stated route. Explicit additions stand on their own.
            if not target['first_source']:
                row['status'] = 'missing_hdd_witness'
            else:
                row['status'] = adopt(db, identity, day, row['url'], 'wikiwiki_release_with_hdd_identity',
                                      target['first_source'], replaces=replaces)


def apply_reviewed_dates(db, reviewed, targets):
    """Apply audited announcement rows when their HDD title and chart agree."""
    groups = defaultdict(list)
    index = song_index(db)
    for claim in reviewed:
        day, url = claim['day'], claim['url']
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError('Expected an ISO date in the reviewed evidence')
        official = bool(re.fullmatch(
            r'https://(?:info-chunithm\.sega\.jp/\d+|chunithm\.sega\.jp/news/\d{4}-\d{2}-\d{2})/', url))
        if not official and not url.startswith(BASE):
            raise ValueError('Unexpected reviewed date source')
        title, difficulty = claim['title'], claim.get('difficulty')
        if difficulty == 'WE':
            rows = db.execute('SELECT DISTINCT chart_identity,title,tag,stars FROM worlds_end_versions')
            matches = {r['chart_identity'] for r in rows if normalize(r['title']) == normalize(title)
                       and r['tag'] == claim['tag'] and r['stars'] == claim['stars']}
        else:
            matches = detail_targets(db, index, title, claim['artist'], difficulty, claim['raw'])
        if claim.get('music_id') is not None:
            matches = {identity for identity in matches if targets[identity]['music_id'] == claim['music_id']}
        if claim.get('witness_version'):
            matches = {identity for identity in matches if targets[identity]['first_version'] == claim['witness_version']}
        if len(matches) != 1:
            raise ValueError(f'Reviewed evidence has no unique HDD witness: {title}/{difficulty}: {sorted(matches)}')
        groups[next(iter(matches))].append((claim, official))
    result = Counter()
    for identity, claims in groups.items():
        releases = {claim['day'] for claim, _ in claims if claim['kind'] in RELEASE}
        direct = {claim['day'] for claim, official in claims if official and claim['kind'] in RELEASE}
        old, old_basis = selected_release(db, identity)
        protected = is_official(db, identity, old, old_basis)
        for claim, official in sorted(claims, key=lambda item: not item[1]):
            day, url, kind = (claim[k] for k in ('day', 'url', 'kind'))
            if kind not in RELEASE:
                status = 'separate_event'
            elif len(direct or releases) > 1 or direct and day not in direct:
                status = 'conflicting_reviewed_dates'
            else:
                basis = 'official_release_date' if official else 'wikiwiki_reviewed_release_date'
                replaces = old if official and not protected and old_basis == 'dated_release_evidence' and day != old else None
                status = adopt(db, identity, day, url, basis, targets[identity]['first_source'],
                               replaces=replaces, correction='corrected_official_date')
            digest = sha256(encoded(claim).encode()).hexdigest()
            record_review(db, identity, day, url, status, None,
                          claim | {'evidence_sha256': digest, 'hdd_witness': targets[identity]['first_source']})
            result[('official_' if official else 'wiki_') + status] += 1
    return result


def propagate(db, targets):
    result = Counter()
    for identity, target in targets.items():
        if target['kind'] != 'song':
            continue
        day, basis = selected_release(db, identity)
        if not day:
            continue
        witnesses = normal_launch_witnesses(db, identity, day)
        counts = Counter(r['difficulty'] for r in witnesses)
        for chart in witnesses:
            if counts[chart['difficulty']] != 1:
                record_review(db, chart['identity'], day, identity, 'ambiguous_launch_slot', None, {})
                continue
            current, child_basis = selected_release(db, chart['identity'])
            parent_corrected = db.execute('''SELECT status FROM jp_date_review WHERE identity=? AND day=?
                AND status IN ('corrected_general_unlock','corrected_official_date')''', (identity, day)).fetchone()
            replaces = current if (parent_corrected and child_basis == 'dated_release_evidence'
                                   and current != day and current == target['exact_on']) else None
            status = adopt(db, chart['identity'], day, identity, 'confirmed_normal_song_release', chart['source'],
                           parent=identity, replaces=replaces,
                           correction=parent_corrected[0] if parent_corrected else 'corrected_general_unlock')
            result[status] += 1
    return result


def category(row):
    if row['difficulty'] == 'ULT':
        return 'ULTIMA'
    if row['difficulty'] == 'WE':
        return 'WORLD_END'
    if row['difficulty']:
        return 'normal_chart'
    return 'normal_song'


def audit(db, cache, destination):
    db.executescript(SCHEMA)
    targets = {r['identity']: r for r in db.execute(TARGETS)}
    ordinary = {r['identity'] for r in db.execute(SONGS) if r['ex_type'] == 0}
    subjects = {key: row for key, row in targets.items() if row['kind'] != 'song' or key in ordinary}
    before = {key: selected_release(db, key)[0] for key in subjects}
    pages, stats = import_pages(db, cache['pages'], cache.get('failures', []))
    with db:
        reviewed = apply_reviewed_dates(db, cache.get('reviewed', []), targets)
        for identity, rows in sorted(pages.items()):
            choose_release(db, identity, rows, targets[identity])
            for row in rows:
                db.execute('INSERT OR IGNORE INTO jp_wiki_dates VALUES (?,?,?,?,?,?,?,?,?)',
                           (row['url'], row['digest'], identity, row['line'], row['date_text'], row['kind'],
                            row['day'], row['status'], encoded(row)))
        propagation = propagate(db, targets)
    after = {key: selected_release(db, key)[0] for key in subjects}
    write_gaps(db, subjects, after, pages, destination / 'remaining.csv')
    write_conflicts(db, destination / 'date-review.csv')
    return {'missing_before': dict(Counter(category(subjects[k]) for k, day in before.items() if not day)),
            'missing_after': dict(Counter(category(subjects[k]) for k, day in after.items() if not day)),
            'changed': dict(Counter(category(subjects[k]) for k in before if before[k] != after[k])),
            'propagation': dict(propagation), 'wiki_pages': dict(stats),
            'reviewed': dict(reviewed),
            'wiki_events': dict(Counter(r['kind'] for rows in pages.values() for r in rows)),
            'wiki_status': dict(Counter(r['status'] for rows in pages.values() for r in rows)),
            'calendar_audit': calendar_audit(db),
            'corrected_general_unlock': db.execute(
                "SELECT count(*) FROM jp_date_review WHERE status='corrected_general_unlock'").fetchone()[0]}


def calendar_audit(db):
    queries = {
        'editions': 'SELECT count(*) FROM versions',
        'multiple_exact_launch_days': '''SELECT count(*) FROM (
            SELECT identity FROM history_events WHERE kind='released' AND effective_on IS NOT NULL
            GROUP BY identity HAVING count(DISTINCT effective_on)>1)''',
        'general_unlock_coincides_with_selected': '''SELECT count(*) FROM jp_date_evidence e
            JOIN sdhd_first_release p USING(identity) WHERE e.kind IN ('general_open','general_unlock') AND e.day=p.exact_on''',
        'hdd_dates_with_conflicting_or_out_of_window_hints': '''SELECT count(*) FROM sdhd_first_release
            WHERE basis='conflicting_or_out_of_window_release_evidence' ''',
        'ultima_same_day_as_parent_from_hdd_hints': '''SELECT count(*) FROM sdhd_first_release a
            JOIN history_entities e ON e.identity=a.identity JOIN sdhd_first_release b ON b.identity=e.parent
            WHERE e.difficulty='ULT' AND a.exact_on=b.exact_on AND a.basis='dated_release_evidence' ''',
        'we_same_day_as_independent_we_song_from_hdd_hints': '''SELECT count(*) FROM sdhd_first_release a
            JOIN history_entities e ON e.identity=a.identity JOIN sdhd_first_release b ON b.identity=e.parent
            WHERE e.kind='we' AND a.exact_on=b.exact_on AND a.basis='dated_release_evidence' ''',
        'multi_day_option_packages': '''SELECT count(*) FROM (
            SELECT package FROM sdhd_notice_claim WHERE kind='first'
            GROUP BY package HAVING count(DISTINCT effective_on)>1)''',
        'known_hdd_preloads': '''SELECT count(*) FROM history_events h JOIN sdhd_first_release p USING(identity)
            WHERE h.kind='first_hdd_observation' AND h.earliest_on<p.exact_on''',
    }
    return {key: db.execute(sql).fetchone()[0] for key, sql in queries.items()}


def write_conflicts(db, path):
    with path.open('w', newline='', encoding='utf-8-sig') as target:
        writer = csv.writer(target)
        writer.writerow(('identity', 'day', 'source', 'status', 'previous', 'evidence'))
        writer.writerows(db.execute('''SELECT identity,day,source,status,previous,evidence FROM jp_date_review
            WHERE status NOT IN ('adopted','corroborated','separate_event')
            UNION ALL SELECT identity,day,url,status,NULL,evidence FROM jp_wiki_dates WHERE status IN
               ('ambiguous_identity','conflicting_wiki_dates','missing_full_date','conflict_existing_date',
                'matches_selected_release_needs_review')
            ORDER BY identity,day,source'''))


def write_gaps(db, subjects, dates, pages, path):
    with path.open('w', newline='', encoding='utf-8-sig') as target:
        writer = csv.writer(target)
        writer.writerow(('title', 'music_id', 'difficulty', 'version', 'first_hdd_witness', 'wiki_url',
                         'official_evidence', 'release_candidate', 'unlock_candidate', 'reason', 'next_source', 'identity'))
        for key, row in sorted(subjects.items()):
            if dates[key]:
                continue
            song = row['parent'] or key
            meta = db.execute('''SELECT title,version,source FROM music_versions JOIN versions USING(version)
                                 WHERE identity=? ORDER BY ordinal LIMIT 1''', (song,)).fetchone()
            title = meta['title'] if meta else ''
            candidates = pages.get(key, []) or pages.get(song, [])
            refs = db.execute('''SELECT day,reference,locator FROM sdhd_release_evidence
                                 WHERE identity=? ORDER BY day,locator''', (key,)).fetchall()
            reasons = {r['status'] for r in candidates if r['status'] not in ('candidate','separate_event')}
            if not reasons:
                failure = db.execute('SELECT status FROM jp_wiki_review WHERE title=?', (title,)).fetchone()
                if failure and failure[0] != 'parsed':
                    reasons.add(failure[0])
                if row['difficulty'] in ('BAS','ADV','EXP','MAS'):
                    parent_day, _ = selected_release(db, song)
                    reasons.add('no_unique_hdd_launch_slot' if parent_day else 'parent_release_unconfirmed')
                elif row['difficulty'] in ('WE', 'ULT'):
                    reasons.add('no_dated_witness_for_independent_chart')
                else:
                    reasons.add('no_unambiguous_dated_wiki_launch')
            reason = '; '.join(sorted(reasons))
            writer.writerow((title, row['music_id'], row['difficulty'], row['first_version'] or (meta and meta['version']),
                             row['first_source'] or (meta and meta['source']),
                             ' '.join(sorted({r['url'] for r in candidates})) or page_url(title),
                             encoded([tuple(r) for r in refs if r['reference']]),
                             encoded(sorted({r['day'] for r in candidates if r['day'] and r['kind'] in RELEASE | {'regular_distribution'}} |
                                            {r['day'] for r in refs if not r['reference']})),
                             encoded(sorted({r['day'] for r in candidates if r['day'] and r['kind'] == 'general_unlock'})),
                             reason, 'https://info-chunithm.sega.jp/; ' + page_url(title), key))


def build(catalog, destination, cache):
    output = destination / 'catalog.sqlite'
    if output.exists():
        raise FileExistsError(output)
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='jp-wiki-build-', dir=destination) as stage:
        work = Path(stage)
        staged = work / output.name
        with closing(open_catalog(catalog)) as src, closing(sqlite3.connect(staged)) as db:
            if (src.execute('PRAGMA application_id').fetchone()[0] != 1431193672
                    or src.execute('PRAGMA user_version').fetchone()[0] != 3
                    or not src.execute('SELECT 1 FROM complete WHERE ready=1').fetchone()):
                raise ValueError('Expected a complete JP HDD v3 catalog')
            src.backup(db)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            result = audit(db, cache, work)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('JP catalog integrity check failed')
        output.hardlink_to(staged)
        (work / 'remaining.csv').replace(destination / 'remaining.csv')
        (work / 'date-review.csv').replace(destination / 'date-review.csv')
    return result


def write_json(path, data):
    stage = path.with_suffix(path.suffix + '.tmp')
    stage.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    stage.replace(path)


async def collect(catalog, path, cache, *, refresh=False):
    import httpx

    with closing(open_catalog(catalog)) as db:
        db.row_factory = sqlite3.Row
        targets = [r for r in db.execute(TARGETS)
                   if r['basis'] == 'dated_release_evidence' or selected_release(db, r['identity'])[0] is None]
        pending = {r['parent'] or r['identity'] for r in targets if r['difficulty'] in (None, 'ULT', 'WE')}
        titles = sorted({r['title'] for r in db.execute(SONGS) if r['identity'] in pending})
    if not refresh:
        cached = {r['url'] for r in cache['pages']}
        titles = [title for title in titles if page_url(title) not in cached]
    if not titles:
        return cache
    cache['failures'] = [r for r in cache.get('failures', []) if r['title'] not in titles]
    agent = 'UniBot-JP-history/1.0'
    async with httpx.AsyncClient(headers={'User-Agent': agent}, timeout=20) as client:
        try:
            response = await client.get('https://wikiwiki.jp/robots.txt')
            response.raise_for_status()
        except httpx.HTTPError as exc:
            cache['failures'].append({'url': BASE, 'title': 'robots.txt', 'status': type(exc).__name__})
            return cache
        robots = RobotFileParser()
        robots.parse(response.text.splitlines())
        delay = max(1.5, robots.crawl_delay(agent) or 0)
        for index, title in enumerate(titles):
            url = page_url(title)
            if not robots.can_fetch(agent, url):
                cache['failures'].append({'url': url, 'title': title, 'status': 'robots_disallowed'})
                continue
            await asyncio.sleep(delay)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                cache['failures'].append({'url': url, 'title': title, 'status': f'http_{code}'})
                if code in (401, 403, 429, 503):
                    cache['failures'].extend({'url': page_url(rest), 'title': rest, 'status': 'collection_paused'}
                                             for rest in titles[index + 1:])
                    break
            except httpx.RequestError as exc:
                cache['failures'].append({'url': url, 'title': title, 'status': type(exc).__name__})
                break
            else:
                page = DetailHTML()
                page.feed(response.text)
                lines = {str(i): line for i, line in enumerate(page.body)}
                if not parse(lines)[0]:
                    cache['failures'].append({'url': url, 'title': title, 'status': 'missing_song_metadata'})
                elif not any(r['url'] == url and r['lines'] == lines for r in cache['pages']):
                    cache['pages'].append({'url': url, 'title': title, 'lines': lines,
                                           'obtained_at': datetime.now(timezone.utc).isoformat(), 'format': 'html_body'})
                write_json(path, cache)
    return cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--reviewed', type=Path, default=Path(__file__).with_name('jp_reviewed_dates.json'))
    args = parser.parse_args()
    cache = json.loads(args.cache.read_text(encoding='utf-8')) if args.cache.exists() else {'pages': [], 'failures': []}
    if args.collect:
        cache = asyncio.run(collect(args.catalog, args.cache, cache, refresh=args.refresh))
        write_json(args.cache, cache)
    cache['reviewed'] = json.loads(args.reviewed.read_text(encoding='utf-8'))
    result = build(args.catalog, args.output, cache)
    write_json(args.output / 'audit.json', result)
    print(encoded(result))


if __name__ == '__main__':
    main()
