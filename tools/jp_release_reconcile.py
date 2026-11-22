"""Reconcile JP release dates using HDD launch events and verified continuity."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree

from apply_hdd_notice_reviews import normal_launch_witnesses
from build_jp_wiki_dates import adopt, release_state, selected_release
from old_hdd_evidence import normalize_name
from old_hdd_scan import encoded
from old_hdd_timeline import batch, event


NORMAL = ('BAS', 'ADV', 'EXP', 'MAS')
LAUNCH_BASIS = 'hdd_launch_event_inferred'
CONTINUITY_BASIS = 'matched_hdd_release_continuity'
ARCHIVE_BASIS = 'reviewed_local_archive_release'
REPAIR_BASES = (LAUNCH_BASIS, CONTINUITY_BASIS, ARCHIVE_BASIS)


def launch_events(db, root):
    rows = db.execute("""
        SELECT h.identity,b.batch,v.start_on,o.source,s.path,s.sha256,
               json_extract(r.payload,'$.netOpenName') AS music_gate,
               json_extract(r.payload,'$.title'),l.label
        FROM sdhd_first_release p JOIN history_entities h USING(identity)
        JOIN history_observations o USING(identity) JOIN history_revisions r USING(revision)
        JOIN history_batches b USING(batch) JOIN versions v USING(version)
        JOIN history_game_event_links l ON l.target_id=h.music_id AND l.role='music'
        JOIN history_game_events g ON g.source=l.source
        JOIN sources s ON s.path=g.source AND s.hdd=v.hdd AND s.package='A000'
        WHERE p.exact_on IS NULL AND h.kind='song' AND b.sequence=0 AND b.kind='package'
          AND o.state='hdd_enabled' AND g.type='3' AND g.music_type='0'
          AND g.title LIKE '%曲開放%ロンチ%'
          AND json_extract(r.payload,'$.exType')='0'
          AND NOT EXISTS (
              SELECT 1 FROM history_observations earlier
              JOIN history_batches eb USING(batch) JOIN versions ev USING(version)
              WHERE earlier.identity=h.identity AND eb.kind='package'
                AND earlier.state='hdd_enabled' AND ev.ordinal<v.ordinal
          )
          AND NOT EXISTS (SELECT 1 FROM sdhd_release_evidence e
                          WHERE e.identity=h.identity AND e.day<>v.start_on)
        ORDER BY h.identity,b.batch,s.path,o.source
    """).fetchall()
    gates, proposals = {}, defaultdict(set)
    for identity, launch_batch, day, witness, source, digest, music_gate, title, label in rows:
        if not title or not label or normalize_name(title) != normalize_name(label):
            continue
        if source not in gates:
            path = (root / source).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError(f'Launch event is outside the HDD root: {source}')
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != digest:
                raise ValueError(f'Launch event changed on disk: {source}')
            gates[source] = ElementTree.fromstring(raw).findtext('netOpenName/id')
        gate = gates[source]
        if gate and music_gate and gate == dict(json.loads(music_gate)).get('id'):
            proposals[identity].add((launch_batch, day, witness, source))
    result = Counter()
    for identity, matches in proposals.items():
        if len(matches) != 1:
            continue
        launch_batch, day, witness, source = next(iter(matches))
        if not adopt_missing(db, identity, day, source, LAUNCH_BASIS, witness):
            continue
        result['launch_songs'] += 1
        charts = db.execute("""
            SELECT DISTINCT h.identity,h.difficulty,o.source
            FROM history_entities h JOIN history_observations o USING(identity)
            JOIN history_revisions r USING(revision)
            WHERE h.parent=? AND o.batch=? AND o.state='hdd_enabled'
              AND h.difficulty IN ('BAS','ADV','EXP','MAS')
              AND json_extract(r.payload,'$.c2s_sha256') IS NOT NULL
            ORDER BY h.difficulty,h.identity,o.source
        """, (identity, launch_batch)).fetchall()
        counts = Counter(difficulty for _, difficulty, _ in charts)
        for child, difficulty, chart_source in charts:
            if counts[difficulty] == 1:
                result['launch_charts'] += adopt_missing(db, child, day, source, LAUNCH_BASIS,
                                                        chart_source, parent=identity)
    return result


def adopt_normal_charts(db, identity, day, source, basis):
    charts = normal_launch_witnesses(db, identity, day)
    counts = Counter(row['difficulty'] for row in charts)
    added = 0
    for row in charts:
        if counts[row['difficulty']] != 1:
            continue
        observed_by_release = db.execute("""
            SELECT 1 FROM history_observations o JOIN history_batches b USING(batch)
            JOIN versions v USING(version)
            WHERE o.identity=? AND o.source=? AND b.kind='package'
              AND b.earliest_on<=? AND ? BETWEEN v.start_on AND v.end_on
        """, (row['identity'], row['source'], day, day)).fetchone()
        if observed_by_release:
            added += adopt_missing(db, row['identity'], day, source, basis, row['source'], parent=identity)
    return added


def adopt_missing(db, identity, day, source, basis, witness, *, parent=None):
    _, known = release_state(db, identity)
    return not known and adopt(db, identity, day, source, basis, witness, parent=parent) == 'adopted'


def observed_song(db, identity):
    row = db.execute("""
        SELECT r.payload,o.source FROM history_observations o
        JOIN history_revisions r USING(revision) JOIN history_batches b USING(batch)
        JOIN versions v USING(version)
        WHERE o.identity=? AND b.kind='package'
        ORDER BY v.ordinal,b.sequence,o.source LIMIT 1
    """, (identity,)).fetchone()
    return (json.loads(row[0]), row[1]) if row else ({}, None)


def chart_signatures(db, identity):
    result = defaultdict(lambda: defaultdict(set))
    for chart, difficulty, fingerprint in db.execute("""
        SELECT DISTINCT h.identity,h.difficulty,json_extract(r.payload,'$.notation_sha256')
        FROM history_entities h JOIN history_revisions r USING(identity)
        WHERE h.parent=? AND h.difficulty IN ('BAS','ADV','EXP','MAS')
    """, (identity,)):
        if fingerprint:
            result[difficulty][chart].add(fingerprint)
    return result


def matched_charts(db, identity, related):
    left, right = chart_signatures(db, identity), chart_signatures(db, related)
    matches = {}
    for difficulty in NORMAL:
        pairs = [(a, b) for a, hashes in left[difficulty].items() for b, other in right[difficulty].items()
                 if hashes & other]
        if len(pairs) != 1:
            return None
        matches[difficulty] = pairs[0]
    return matches


def save_inherited_date(db, identity, donor, day):
    changed = db.execute("""
        UPDATE sdhd_first_release SET earliest_on=?,latest_on=?,exact_on=?,basis=?
        WHERE identity=? AND exact_on IS NULL AND NOT EXISTS (
          SELECT 1 FROM history_events WHERE identity=? AND kind='released' AND effective_on IS NOT NULL
        )
    """, (day, day, day, CONTINUITY_BASIS, identity, identity)).rowcount
    if changed:
        db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)',
                   (identity, day, 'Same Music ID, title, cue and all four ordinary notation fingerprints', donor))
    return changed


def inherit_release_dates(db):
    targets = db.execute("""
        SELECT h.identity,h.music_id FROM sdhd_first_release p JOIN history_entities h USING(identity)
        WHERE h.kind='song' AND (p.exact_on IS NULL OR p.basis=?)
        ORDER BY h.music_id,h.identity
    """, (CONTINUITY_BASIS,)).fetchall()
    result = Counter()
    for identity, music_id in targets:
        target, _ = observed_song(db, identity)
        if target.get('exType') != '0':
            continue
        linked = db.execute("""
            SELECT h.identity FROM history_identity_links l JOIN history_entities h
              ON h.identity=CASE WHEN l.identity=? THEN l.related ELSE l.identity END
            WHERE (l.identity=? OR l.related=?) AND h.music_id=? AND l.kind='candidate_song_continuity'
        """, (identity, identity, identity, music_id)).fetchall()
        donors = {}
        for (donor,) in linked:
            day, basis = selected_release(db, donor)
            if not day or basis == CONTINUITY_BASIS:
                continue
            prior, _ = observed_song(db, donor)
            cue = dict(target.get('cueFileName') or ()).get('str')
            if (prior.get('exType') != '0' or not cue or cue != dict(prior.get('cueFileName') or ()).get('str')
                    or normalize_name(target['title']) != normalize_name(prior['title'])):
                continue
            matches = matched_charts(db, identity, donor)
            if matches:
                donors[donor] = (day, matches)
        if len(donors) != 1:
            continue
        donor, (day, matches) = next(iter(donors.items()))
        _, known = release_state(db, identity)
        if known and known != {day}:
            continue
        result['continuity_songs'] += save_inherited_date(db, identity, donor, day)
        for child, prior in matches.values():
            child_day, _ = selected_release(db, prior)
            if child_day == day:
                result['continuity_charts'] += save_inherited_date(db, child, prior, day)
    return result


def apply_reviewed_access(db):
    """Apply the explicit local archive distinctions reviewed for this repair."""
    cases = json.loads(Path(__file__).with_name('jp_release_reconcile.json').read_text(encoding='utf-8'))
    result = Counter()
    for case in cases:
        identity, day, source = (case[key] for key in ('identity', 'day', 'source'))
        witness = db.execute("""
            SELECT o.source FROM history_observations o JOIN history_batches b USING(batch)
            LEFT JOIN versions v USING(version)
            WHERE o.identity=? AND b.kind='package' AND o.state IN ('present','hdd_enabled')
            ORDER BY v.ordinal NULLS LAST,b.sequence NULLS LAST,o.source LIMIT 1
        """, (identity,)).fetchone()
        if not witness:
            continue
        if case['kind'] == 'released':
            if adopt_missing(db, identity, day, source, ARCHIVE_BASIS, witness[0]):
                db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)',
                           (identity, day, case['basis'], source))
                result['archive_songs'] += 1
                result['archive_charts'] += adopt_normal_charts(db, identity, day, source,
                                                              ARCHIVE_BASIS)
            continue
        if db.execute("""SELECT 1 FROM history_events WHERE identity=? AND kind='general_open'
                         AND effective_on=?""", (identity, day)).fetchone():
            continue
        version = db.execute('SELECT version FROM versions WHERE ? BETWEEN start_on AND end_on', (day,)).fetchone()
        if version:
            key = batch(db, 'day:' + day, version[0], 'public_day', 'Reviewed Wiki archive', day=day)
            event(db, identity, key, 'general_open', 'access', None, 'standard', None, witness[0],
                  encoded(case), day=day)
            result['general_open'] += 1
    return result


def apply_release_repairs(db, root):
    result = apply_reviewed_access(db)
    result.update(launch_events(db, root.resolve(strict=True)))
    result.update(inherit_release_dates(db))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    uri = args.database.resolve(strict=True).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        totals = apply_release_repairs(db, args.root)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Release repair failed database verification')
    print(json.dumps(totals, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
