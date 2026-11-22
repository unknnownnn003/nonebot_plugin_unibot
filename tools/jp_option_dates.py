"""Apply selected dates and uniquely evidenced OPTION days to undated chart changes."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


ASSUMPTION = 'SDHD option-day assumption: '
USER_DATE = 'SDHD user-selected revision date: '


def apply_batch_day(db, key, day, basis):
    applied = db.execute('SELECT effective_on FROM history_batches WHERE batch=?', (key,)).fetchone()[0]
    if applied and applied != day:
        raise ValueError(f'Batch already has a different effective day: {key} / {applied}')
    db.execute("""
        UPDATE history_batches
        SET effective_on=?,earliest_on=?,latest_on=?,timing='exact',evidence=?||evidence
        WHERE batch=? AND effective_on IS NULL
    """, (day, day, day, f'SDHD user-selected batch date: {day}; {basis} ', key))
    return db.execute("""
        UPDATE history_events SET effective_on=?,earliest_on=?,latest_on=?,
            evidence=?||' Prior bounds: '||coalesce(earliest_on,'unknown')||' to '||
                coalesce(latest_on,'unknown')||'; '||evidence
        WHERE batch=? AND effective_on IS NULL AND kind IN ('hdd_revision','hdd_enabled','hdd_disabled')
        RETURNING field,batch,effective_on
    """, (day, day, day, f'{USER_DATE}{day}; {basis}', key)).fetchall()


def apply_reviewed_days(db):
    reviews = json.loads(Path(__file__).with_name('jp_revision_dates.json').read_text(encoding='utf-8'))
    changed = []
    for review in reviews:
        key, field, day = (review[name] for name in ('batch', 'field', 'day'))
        if not db.execute('''
            SELECT 1 FROM history_batches b JOIN versions v USING(version)
            WHERE b.batch=? AND b.kind='package' AND ? BETWEEN v.start_on AND v.end_on
        ''', (key, day)).fetchone():
            continue
        rows = db.execute('''
            SELECT event,identity,earliest_on,latest_on,effective_on,evidence FROM history_events
            WHERE batch=? AND field=? AND kind='hdd_revision'
              AND (effective_on IS NULL OR (effective_on=? AND evidence LIKE ?))
        ''', (key, field, day, USER_DATE + '%')).fetchall()
        identities = review.get('identities')
        rows = [row for row in rows if identities is None or row[1] in identities]
        if len(rows) != review['expected']:
            raise ValueError(f'Reviewed revision scope changed: {key} / {field} ({len(rows)} rows)')
        for event, _, earliest, latest, effective, evidence in rows:
            if effective is not None:
                continue
            basis = f"{USER_DATE}{day}; {review['basis']} Prior bounds: {earliest} to {latest}; {evidence}"
            db.execute('''
                UPDATE history_events SET effective_on=?,earliest_on=?,latest_on=?,evidence=? WHERE event=?
            ''', (day, day, day, basis, event))
            changed.append((field, key, day))
        if review.get('whole_batch'):
            changed.extend(apply_batch_day(db, key, day, review['basis']))
    return changed


def apply_option_days(db):
    has_notices = db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='sdhd_notice_claim'"
    ).fetchone()
    notice_days = """
        UNION SELECT package,effective_on FROM sdhd_notice_claim WHERE kind IN ('first','revision')
    """ if has_notices else ''
    return db.execute(f"""
        WITH evidence(batch,day) AS (
            SELECT witness.batch,released.effective_on
            FROM history_events witness JOIN history_events released USING(identity)
            WHERE witness.kind='first_hdd_observation'
              AND released.kind='released' AND released.effective_on IS NOT NULL
            {notice_days}
        ), option_days AS MATERIALIZED (
            SELECT e.batch,min(e.day) AS day FROM evidence e JOIN history_batches b USING(batch)
            WHERE b.kind='package' AND b.sequence>0 AND b.batch LIKE 'pkg:SDHD_%'
              AND e.day BETWEEN b.earliest_on AND b.latest_on
            GROUP BY e.batch HAVING count(DISTINCT e.day)=1
        )
        UPDATE history_events AS h
        SET effective_on=o.day, earliest_on=o.day, latest_on=o.day,
            evidence=?||o.day||'; '||h.evidence
        FROM option_days o
        WHERE h.batch=o.batch AND h.kind='hdd_revision'
          AND h.field IN ('hdd_hundredths','c2s_sha256') AND h.effective_on IS NULL
          AND o.day BETWEEN h.earliest_on AND h.latest_on
        RETURNING field,batch,effective_on
    """, (ASSUMPTION,)).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    uri = args.database.resolve(strict=True).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        rows = apply_reviewed_days(db) + apply_option_days(db)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('OPTION date update failed database verification')
    for field in ('hdd_hundredths', 'c2s_sha256'):
        print(f'{field}: {sum(row[0] == field for row in rows)}')
    print(f'all dated revision fields: {len(rows)}')


if __name__ == '__main__':
    main()
