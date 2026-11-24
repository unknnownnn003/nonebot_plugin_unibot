"""Apply reviewed song continuity and availability decisions to the JP catalog."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from old_hdd_timeline import event


# Each reviewed pair has the same Music ID and matching notation in BAS–MAS.
# Keep the identity from the earlier version so existing first-release references survive.
CONTINUITY = (
    ('NEW PLUS', 'NEW', 'A111', 2108),
    ('NEW PLUS', 'NEW', 'A111', 2109),
    ('NEW PLUS', 'NEW', 'A111', 2110),
    ('SUN', 'NEW PLUS', 'A000', 2172),
    ('SUN', 'NEW PLUS', 'A000', 2173),
)


def record_transition(db, old, kept, later, earlier):
    previous = db.execute('''
        SELECT r.payload,o.source FROM history_observations o JOIN history_revisions r USING(revision)
        WHERE o.identity=? AND o.batch=?
    ''', (kept, f'end:{earlier}')).fetchall()
    current = db.execute('''
        SELECT r.payload,o.source,b.batch,b.earliest_on,v.start_on,v.end_on
        FROM history_observations o JOIN history_revisions r USING(revision)
        JOIN history_batches b USING(batch) JOIN versions v USING(version)
        WHERE o.identity=? AND b.version=? AND b.kind='package' AND b.sequence=0
    ''', (old, later)).fetchall()
    if len(previous) != 1 or len(current) != 1:
        raise ValueError(f'Ambiguous version transition: {old} / {kept}')
    before, old_source = previous[0]
    after, new_source, package, low, start, end = current[0]
    before, after = json.loads(before), json.loads(after)
    for field in sorted(before.keys() | after.keys()):
        if before.get(field) != after.get(field):
            event(db, kept, package, 'hdd_revision', field, before.get(field), after.get(field),
                  old_source, new_source, 'SDHD overlay content change',
                  day=start if low == start else None, bounds=(low, end))


def merge_identity(db, old, kept, later, earlier):
    record_transition(db, old, kept, later, earlier)
    releases = db.execute('''
        SELECT identity,earliest_on,latest_on,exact_on FROM sdhd_first_release
        WHERE identity IN (?,?)
    ''', (old, kept)).fetchall()
    if len(releases) == 2:
        if tuple(releases[0])[1:] != tuple(releases[1])[1:]:
            raise ValueError(f'Conflicting first-release dates: {old} / {kept}')
        db.execute('DELETE FROM sdhd_first_release WHERE identity=?', (old,))

    db.execute('''
        DELETE FROM sdhd_release_evidence AS duplicate WHERE identity=? AND EXISTS (
            SELECT 1 FROM sdhd_release_evidence AS retained
            WHERE retained.identity=? AND retained.day=duplicate.day
              AND retained.locator=duplicate.locator AND retained.reference IS duplicate.reference
        )
    ''', (old, kept))
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='jp_wiki_dates'").fetchone():
        db.execute('''
            DELETE FROM jp_wiki_dates AS duplicate WHERE identity=? AND EXISTS (
                SELECT 1 FROM jp_wiki_dates AS retained
                WHERE retained.identity=? AND retained.url=duplicate.url AND retained.digest=duplicate.digest
                  AND retained.line=duplicate.line AND retained.kind=duplicate.kind
                  AND retained.date_text=duplicate.date_text AND retained.day IS duplicate.day
                  AND retained.status=duplicate.status
                  AND json_remove(retained.evidence,'$.identity')=json_remove(duplicate.evidence,'$.identity')
            )
        ''', (old, kept))
    db.execute('DELETE FROM history_events WHERE identity=? AND kind=?', (old, 'first_hdd_observation'))
    db.execute('''
        DELETE FROM history_events WHERE identity=? AND kind='missing_from_local_overlay'
          AND EXISTS (SELECT 1 FROM history_observations o
                      WHERE o.batch=history_events.batch AND o.identity=?)
    ''', (kept, old))
    db.execute('''
        DELETE FROM history_identity_links WHERE (identity=? AND related=?) OR (identity=? AND related=?)
    ''', (old, kept, kept, old))

    tables = db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall()
    for table, in tables:
        columns = db.execute('SELECT name FROM pragma_table_info(?)', (table,)).fetchall()
        for column, in columns:
            if column in {'identity', 'chart_identity', 'origin_identity', 'parent'} or (
                    table == 'history_identity_links' and column == 'related'):
                if (table, column) != ('history_entities', 'identity'):
                    table_name = '"' + table.replace('"', '""') + '"'
                    db.execute(f'UPDATE {table_name} SET "{column}"=? WHERE "{column}"=?', (kept, old))
    db.execute('DELETE FROM history_entities WHERE identity=?', (old,))


def apply_catalog_decisions(db):
    db.execute('PRAGMA defer_foreign_keys=ON')
    merged = 0
    for later, earlier, package, music_id in CONTINUITY:
        old = f'sdhd:A000:{later}:0:{music_id}'
        kept = f'sdhd:{package}:{earlier}:0:{music_id}'
        if not db.execute('SELECT 1 FROM history_entities WHERE identity=?', (old,)).fetchone():
            continue
        rows = db.execute('''
            SELECT version,slot,chart_identity,c2s_sha256 FROM chart_versions
            WHERE (version=? AND identity=?) OR (version=? AND identity=?)
        ''', (later, old, earlier, kept))
        charts = {later: {}, earlier: {}}
        for version, slot, identity, digest in rows:
            if slot in charts[version]:
                raise ValueError(f'Ambiguous chart slot: {identity}')
            charts[version][slot] = (identity, digest)
        if any(not charts[later].get(slot, (None, None))[1] or
               charts[later][slot][1] != charts[earlier].get(slot, (None, None))[1]
               for slot in range(4)):
            raise ValueError(f'Reviewed chart continuity differs for Music ID {music_id}')
        for slot in range(4):
            merge_identity(db, charts[later][slot][0], charts[earlier][slot][0], later, earlier)
        merge_identity(db, old, kept, later, earlier)
        merged += 1

    promoted = {}
    db.execute('''
        UPDATE chart_versions SET selected_hundredths=hdd_hundredths
        WHERE state='possible' AND selected_hundredths IS NULL AND constant_status='hdd_observed'
    ''')
    for table in ('music_versions', 'chart_versions', 'worlds_end_versions', 'history_observations'):
        promoted[table] = db.execute(f"UPDATE {table} SET state='present' WHERE state='possible'").rowcount
    promoted['history_windows'] = db.execute("""
        UPDATE history_windows SET availability='present' WHERE availability='possible'
    """).rowcount
    return {'merged_songs': merged, 'promoted': promoted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    uri = args.database.resolve(strict=True).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        summary = apply_catalog_decisions(db)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('JP catalog update failed database verification')
    print(summary)


if __name__ == '__main__':
    main()
