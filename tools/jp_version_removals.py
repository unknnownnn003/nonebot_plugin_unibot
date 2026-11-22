"""Infer jukebox removals from adjacent HDD version checkpoints."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from old_hdd_scan import encoded
from old_hdd_timeline import batch, event


INFERRED = 'JP version boundary removal inference: '


def apply_version_removals(db):
    candidates = db.execute("""
        SELECT m.identity,m.source,m.music_id,old.version,new.version,new.start_on
        FROM versions old JOIN versions new ON new.ordinal=old.ordinal+1
        JOIN music_versions m ON m.version=old.version AND m.state='present'
        WHERE NOT EXISTS (
            SELECT 1 FROM music_versions x WHERE x.version=new.version
              AND (x.identity=m.identity OR x.music_id=m.music_id)
        ) AND NOT EXISTS (
            SELECT 1 FROM history_identity_links l
            JOIN music_versions x ON x.version=new.version
              AND x.identity=CASE WHEN l.identity=m.identity THEN l.related ELSE l.identity END
            WHERE l.identity=m.identity OR l.related=m.identity
        ) AND NOT EXISTS (
            SELECT 1 FROM history_events known
            WHERE known.identity=m.identity AND known.kind='removed'
              AND known.effective_on BETWEEN old.start_on AND new.end_on
              AND (known.effective_on>=new.start_on OR NOT EXISTS (
                  SELECT 1 FROM history_events r WHERE r.identity=m.identity AND r.kind='restored'
                    AND r.effective_on>known.effective_on AND r.effective_on<=new.start_on
              ))
        ) AND NOT EXISTS (
            SELECT 1 FROM history_events r WHERE r.identity=m.identity
              AND r.kind='restored' AND r.effective_on=new.start_on
        )
        ORDER BY new.ordinal,m.identity
    """).fetchall()
    for identity, source, music_id, old, version, day in candidates:
        observed = db.execute("""
            SELECT v.start_on,v.end_on FROM versions v JOIN music_versions m USING(version)
            WHERE v.start_on>? AND (m.identity=? OR m.music_id=? OR EXISTS (
                SELECT 1 FROM history_identity_links l
                WHERE (l.identity=? AND l.related=m.identity) OR (l.related=? AND l.identity=m.identity)
            )) ORDER BY v.ordinal LIMIT 1
        """, (day, identity, music_id, identity, identity)).fetchone()
        restore = db.execute("""
            SELECT min(effective_on) FROM history_events
            WHERE identity=? AND kind='restored' AND effective_on>?
        """, (identity, day)).fetchone()[0]
        resume = restore if not observed or (restore and restore <= observed[1]) else observed[0]
        key = batch(db, f'day:{day}', version, 'public_day', 'JP edition boundary', day=day)
        evidence = INFERRED + encoded({'previous_version': old, 'absent_version': version, 'resume_on': resume})
        event(db, identity, key, 'removed', 'availability', 'present', 'removed',
              source, None, evidence, day=day)
        db.execute("""
            DELETE FROM history_windows WHERE start_on>=? AND (? IS NULL OR end_on<?)
              AND identity IN (SELECT identity FROM history_entities WHERE identity=? OR parent=?)
        """, (day, resume, resume, identity, identity))
        if resume:
            db.execute("""
                UPDATE history_windows SET start_on=?
                WHERE start_on>=? AND start_on<? AND end_on>=?
                  AND identity IN (SELECT identity FROM history_entities WHERE identity=? OR parent=?)
            """, (resume, day, resume, resume, identity, identity))
    return len(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    uri = args.database.resolve(strict=True).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        changed = apply_version_removals(db)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Version removal update failed database verification')
    print(f'version-boundary removals: {changed}')


if __name__ == '__main__':
    main()
