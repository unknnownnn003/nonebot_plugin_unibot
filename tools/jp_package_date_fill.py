"""Assign requested package-day estimates to otherwise undated JP records."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from old_hdd_scan import encoded


PACKAGE_DATE = 'JP package date assumption: '
RELEASE_BASIS = 'package_date_assumption'
TUTORIAL_BASIS = 'tutorial_package_observation_day'
JST = timezone(timedelta(hours=9))


def read_package_dates(db, roots, needed, changed_files):
    dated = defaultdict(set)
    for version, hdd, package, day in db.execute("""
        SELECT b.version,s.hdd,s.package,e.effective_on
        FROM history_events e JOIN history_batches b USING(batch) JOIN sources s ON s.path=e.new_source
        WHERE e.kind IN ('released','restored') AND e.effective_on IS NOT NULL
    """):
        key = version, hdd, package
        if key in needed:
            dated[key].add(day)
    locations = {}
    for version, hdd, package, start, end, path, applied, built in db.execute("""
        SELECT v.version,v.hdd,p.package,v.start_on,v.end_on,p.path,b.effective_on,l.build_on
        FROM versions v JOIN packages p USING(hdd)
        LEFT JOIN history_batches b ON b.batch='pkg:'||p.hdd||':'||p.package AND b.version=v.version
        LEFT JOIN sdhd_layers l ON l.version=v.version AND l.package=p.package AND l.container=p.path
    """):
        key = version, hdd, package
        if key not in needed:
            continue
        if package == 'A000':
            locations[key] = start, 'base_version_start', path
        elif applied:
            locations[key] = applied, 'adopted_batch_date', path
        elif built:
            locations[key] = built, 'package_build_timestamp', path
        else:
            in_edition = {day for day in dated[key] if start <= day <= end}
            if len(in_edition) == 1:
                locations[key] = in_edition.pop(), 'unique_dated_release_or_restore_in_package', path
                continue
            root = roots['SDBT' if hdd.startswith('SDBT') else 'SDHD']
            if root is None:
                continue
            paths = sorted(changed_files[hdd, package]) or [row[0] for row in db.execute("""
                SELECT path FROM sources WHERE hdd=? AND package=? AND kind='music_xml'
            """, (hdd, package))]
            days = Counter()
            for locator in paths:
                file = root / locator
                if file.is_file():
                    day = datetime.fromtimestamp(file.stat().st_mtime, JST).date().isoformat()
                    days[day] += 1
            majority = [(day, count) for day, count in days.items()
                        if start <= day <= end and count >= 2 and count * 2 > len(paths)]
            if majority:
                label = 'majority_changed_music_xml_mtime' if changed_files[hdd, package] else 'majority_music_xml_mtime'
                locations[key] = majority[0][0], label, path
                continue
            config = root / path / 'data.conf'
            if config.is_file():
                day = datetime.fromtimestamp(config.stat().st_mtime, JST).date().isoformat()
                locations[key] = day, 'package_data_conf_mtime', path
            elif days:
                day = max(days, key=lambda stamp: (days[stamp], stamp))
                locations[key] = day, 'package_music_xml_mtime', path
    return locations


def fill_package_dates(db, sdbt_root=None, sdhd_root=None):
    revisions = db.execute("""
        SELECT e.event,e.field,b.version,coalesce(p.hdd,s.hdd),coalesce(p.package,s.package),
               e.earliest_on,e.latest_on,e.evidence,
               CASE WHEN p.package IS NULL OR (p.package=s.package AND p.hdd=s.hdd) THEN s.path END,
               e.effective_on
        FROM history_events e JOIN history_batches b USING(batch)
        JOIN sources s ON s.path=e.new_source
        LEFT JOIN packages p ON b.kind='package' AND b.batch='pkg:'||p.hdd||':'||p.package
        WHERE (e.effective_on IS NULL OR e.evidence LIKE ?)
          AND e.kind IN ('revision','hdd_revision','hdd_enabled','hdd_disabled','option_variant')
    """, (PACKAGE_DATE + '%',)).fetchall()
    releases = db.execute("""
        WITH dated AS (
            SELECT identity FROM history_events WHERE kind='released' AND effective_on IS NOT NULL
            UNION SELECT identity FROM sdhd_first_release WHERE exact_on IS NOT NULL
        ), witnessed AS (
            SELECT identity FROM history_events WHERE kind='first_hdd_observation'
            UNION SELECT identity FROM history_observations WHERE state='present'
        ), songs AS (SELECT DISTINCT identity,ex_type FROM music_versions)
        SELECT h.identity,h.kind,b.version,s.hdd,s.package,o.source,
            EXISTS(SELECT 1 FROM songs WHERE songs.identity=h.parent AND ex_type='1') AS tutorial
        FROM witnessed w JOIN history_entities h USING(identity)
        LEFT JOIN dated USING(identity)
        JOIN history_observations o ON o.rowid=(
            SELECT earlier.rowid FROM history_observations earlier
            JOIN history_batches eb USING(batch) JOIN versions ev USING(version)
            WHERE earlier.identity=h.identity AND eb.kind='package'
              AND (earlier.state IN ('present','hdd_enabled','tutorial') OR (
                    earlier.state='disabled' AND EXISTS(
                      SELECT 1 FROM songs WHERE songs.identity=h.parent AND ex_type='1')))
            ORDER BY ev.ordinal,eb.sequence NULLS LAST,earlier.source LIMIT 1
        )
        JOIN history_batches b USING(batch) JOIN sources s ON s.path=o.source
        WHERE dated.identity IS NULL AND (h.kind='chart' OR (
            h.kind='song' AND EXISTS(SELECT 1 FROM songs WHERE songs.identity=h.identity AND ex_type='0')))
    """).fetchall()
    needed = {tuple(row[2:5]) for row in revisions if row[-1] is None}
    needed.update(tuple(row[2:5]) for row in releases)
    unassigned = {(hdd, package) for version, hdd, package in needed if version is None}
    needed.update((version, hdd, package) for version, hdd in db.execute('SELECT version,hdd FROM versions')
                  for disk, package in unassigned if disk == hdd)
    changed_files = defaultdict(set)
    for _, _, _, hdd, package, _, _, _, source, _ in revisions:
        if source:
            changed_files[hdd, package].add(source)
    dates = read_package_dates(db, {'SDBT': sdbt_root, 'SDHD': sdhd_root}, needed, changed_files)
    for hdd, package in unassigned:
        choices = {value for (_, disk, code), value in dates.items() if (disk, code) == (hdd, package)}
        if len(choices) == 1:
            dates[None, hdd, package] = choices.pop()
    result = Counter()
    remaining = Counter()
    for event, field, version, hdd, package, earliest, latest, evidence, _, effective in revisions:
        if effective:
            continue
        key = version, hdd, package
        choice = dates.get(key)
        if not choice:
            remaining['/'.join(x or '?' for x in key)] += 1
            continue
        day, basis, locator = choice
        proof = encoded({'basis': basis, 'package': locator, 'prior_bounds': [earliest, latest],
                         'prior_evidence': evidence})
        db.execute('UPDATE history_events SET effective_on=?,earliest_on=?,latest_on=?,evidence=? WHERE event=?',
                   (day, day, day, PACKAGE_DATE + proof, event))
        result['revision:' + field] += 1
    for identity, kind, version, hdd, package, source, tutorial in releases:
        choice = dates.get((version, hdd, package))
        if not choice:
            continue
        day, basis, locator = choice
        basis_label = TUTORIAL_BASIS if tutorial else RELEASE_BASIS
        db.execute("""
            INSERT INTO sdhd_first_release VALUES (?,?,?,?,?)
            ON CONFLICT(identity) DO UPDATE SET earliest_on=excluded.earliest_on,
                latest_on=excluded.latest_on,exact_on=excluded.exact_on,basis=excluded.basis
            WHERE sdhd_first_release.exact_on IS NULL
        """, (identity, day, day, day, basis_label))
        db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)',
                   (identity, day, f'{basis}: {locator}', source))
        result['tutorial_observation' if tutorial else f'release:{kind}'] += 1
    return {'filled': dict(result), 'unresolved_revision_packages': dict(remaining)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--sdbt-root', required=True, type=Path)
    parser.add_argument('--sdhd-root', required=True, type=Path)
    args = parser.parse_args()
    uri = args.database.resolve(strict=True).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        report = fill_package_dates(db, args.sdbt_root.resolve(strict=True), args.sdhd_root.resolve(strict=True))
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Package date fill failed database verification')
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
