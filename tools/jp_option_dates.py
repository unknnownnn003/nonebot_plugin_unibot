"""Use a uniquely evidenced OPTION day for otherwise undated chart changes."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


ASSUMPTION = 'SDHD option-day assumption: '


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
        rows = apply_option_days(db)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('OPTION date update failed database verification')
    for field in ('hdd_hundredths', 'c2s_sha256'):
        print(f'{field}: {sum(row[0] == field for row in rows)}')


if __name__ == '__main__':
    main()
