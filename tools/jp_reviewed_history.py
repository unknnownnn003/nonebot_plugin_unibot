"""Carry reviewed operational evidence forward independently of HDD overlays."""
from collections import defaultdict

from jp_option_dates import ASSUMPTION
from jp_package_date_fill import PACKAGE_DATE, RELEASE_BASIS, TUTORIAL_BASIS
from jp_release_reconcile import REPAIR_BASES
from jp_version_removals import INFERRED


def loose_path(path):
    return path.replace('.zip!/', '/') if path else path


def copy_reviewed(db, catalog):
    db.execute('ATTACH DATABASE ? AS reviewed', (catalog.resolve(strict=True).as_uri() + '?mode=ro',))
    # Preserve reviewed source relationships through the change of locator.
    db.create_function('loose_path', 1, loose_path, deterministic=True)
    missing = db.execute("""
        SELECT old.version,old.package,old.container FROM reviewed.sdhd_layers old
        WHERE old.status='indexed' AND NOT EXISTS (
          SELECT 1 FROM main.sdhd_layers new WHERE new.version=old.version
          AND new.package=old.package AND new.status='indexed' AND new.music_count>=old.music_count
        )
    """).fetchall()
    if missing:
        raise ValueError(f'Missing previously indexed packages: {[tuple(row) for row in missing]}')

    db.execute('INSERT OR IGNORE INTO main.references_used SELECT * FROM reviewed.references_used')
    db.execute('INSERT OR IGNORE INTO main.history_assets SELECT * FROM reviewed.history_assets')
    db.execute("""INSERT OR IGNORE INTO main.history_batches SELECT * FROM reviewed.history_batches
                  WHERE kind='public_day'""")
    db.execute("""INSERT OR IGNORE INTO main.history_asset_links
                  SELECT asset,loose_path(location),relation,loose_path(source),batch,published_on,title
                  FROM reviewed.history_asset_links WHERE batch IS NULL OR
                  batch IN (SELECT batch FROM main.history_batches)""")
    for kind, name, ddl in db.execute("""SELECT type,name,sql FROM reviewed.sqlite_master
                                        WHERE name GLOB 'jp_*' OR name IN
                                        ('hdd_notice_review','sdhd_notice','sdhd_notice_claim',
                                         'sdhd_notice_identity','sdhd_notice_day')
                                        ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid""").fetchall():
        db.execute(ddl)
        if kind == 'table':
            if name == 'hdd_notice_review':
                db.execute("""INSERT INTO main.hdd_notice_review
                              SELECT asset,loose_path(location),announcement,identity,day,mode,status,evidence
                              FROM reviewed.hdd_notice_review""")
            elif name == 'sdhd_notice_claim':
                db.execute("CREATE TEMP TABLE reviewed_claim_paths AS "
                           "SELECT DISTINCT source,loose_path(source) AS path FROM reviewed.sdhd_notice_claim")
                db.execute("CREATE UNIQUE INDEX reviewed_claim_by_source ON reviewed_claim_paths(source)")
                rows = db.execute("""
                    SELECT c.*,p.path FROM reviewed.sdhd_notice_claim c JOIN reviewed_claim_paths p USING(source)
                """).fetchall()
                for row in rows:
                    source = db.execute('SELECT path FROM main.sources WHERE path IN (?,?)',
                                        (row['path'], row['source'])).fetchone()
                    revision = db.execute("""
                        SELECT revision FROM main.history_observations
                        WHERE identity=? AND batch=? AND source=?
                    """, (row['identity'], row['package'], source[0] if source else row['path'])).fetchone()
                    if not source or not revision:
                        raise ValueError(f"Reviewed claim lost its witness: {row['identity']} / {row['package']} / {row['path']}")
                    db.execute('INSERT INTO main.sdhd_notice_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (*tuple(row)[:10], source[0], revision[0], row['field']))
                db.execute('DROP TABLE reviewed_claim_paths')
            else:
                db.execute(f'INSERT INTO main.{name} SELECT * FROM reviewed.{name}')

    db.execute("""INSERT INTO main.sdhd_release_evidence
                  SELECT DISTINCT identity,day,loose_path(locator),reference FROM reviewed.sdhd_release_evidence""")
    db.execute("""INSERT INTO main.sdhd_first_release
                  SELECT * FROM reviewed.sdhd_first_release
                  WHERE exact_on IS NOT NULL AND basis NOT IN (?,?,?,?,?)""",
               (*REPAIR_BASES, RELEASE_BASIS, TUTORIAL_BASIS))
    db.execute("""DELETE FROM main.history_windows WHERE version IN
                  (SELECT version FROM main.versions WHERE ordinal<=12)""")
    db.execute("""INSERT INTO main.history_windows SELECT * FROM reviewed.history_windows
                  WHERE version IN (SELECT version FROM main.versions WHERE ordinal<=12)""")

    rows = db.execute("""
        SELECT e.*,b.version,b.effective_on AS batch_day
        FROM reviewed.history_events e JOIN reviewed.history_batches b USING(batch)
        WHERE (b.kind='public_day' OR (e.kind='hdd_revision' AND e.effective_on IS NOT NULL
              AND e.evidence NOT LIKE ?)) AND e.evidence NOT LIKE ? AND e.evidence NOT LIKE ?
          AND NOT (e.kind='released' AND
              CASE WHEN json_valid(e.evidence) THEN coalesce(json_extract(e.evidence,'$.basis'),'')
                   ELSE '' END IN (?,?,?))
        ORDER BY event
    """, (ASSUMPTION + '%', INFERRED + '%', PACKAGE_DATE + '%', *REPAIR_BASES)).fetchall()
    known = set(map(tuple, db.execute('SELECT identity,batch,kind,field,before,after,effective_on,evidence '
                                      'FROM main.history_events')))
    sdhd_versions = {version for version, in db.execute('SELECT version FROM main.versions WHERE ordinal>12')}
    public, first = defaultdict(dict), {}
    changes = {}
    for row in rows:
        identity, key, kind, field = (row[k] for k in ('identity', 'batch', 'kind', 'field'))
        day = row['effective_on']
        if kind == 'hdd_revision':
            transition = (identity, key, field, row['before'], row['after'])
            changes[transition] = (day, row['evidence'])
            continue
        if kind == 'released' and day:
            first.setdefault(identity, (day, None, None))
        if kind in ('removed', 'restored', 'removal_boundary_candidate') and row['version'] in sdhd_versions:
            when = day or row['batch_day']
            if when and (kind != 'removal_boundary_candidate' or when not in public[identity]):
                public[identity][when] = kind
        values = (identity, key, kind, field, row['before'], row['after'], day, row['evidence'])
        if values in known:
            continue
        db.execute("""INSERT INTO main.history_events
                      (identity,batch,kind,field,before,after,old_source,new_source,
                       earliest_on,latest_on,effective_on,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (*values[:6], loose_path(row['old_source']), loose_path(row['new_source']),
                    row['earliest_on'], row['latest_on'], day, row['evidence']))
        known.add(values)
    for identity, parent, day in db.execute("""
        SELECT e.identity, CASE WHEN h.kind='we' THEN h.parent END, e.effective_on
        FROM main.history_events e JOIN main.history_entities h USING(identity)
        WHERE e.kind='limited_end' AND e.effective_on IS NOT NULL
    """):
        public[identity].setdefault(day, 'removal_boundary_candidate')
        if parent:
            public[parent].setdefault(day, 'removal_boundary_candidate')
    return public, first, changes
