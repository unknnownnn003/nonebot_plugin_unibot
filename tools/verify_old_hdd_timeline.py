"""Verify a real HDD lifecycle database against an archived v1 checkpoint catalog."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from statistics import median
from time import perf_counter

import import_paradise_lost  # Loads the offline service namespace without starting NoneBot.
from _unibot_history.old_hdd_history import OldHddHistory
from old_hdd_scan import file_hash


FIELDS = {
    'song': {
        'title': 'title', 'artist': 'artist', 'sort_name': 'sortName', 'jacket_sha256': 'jacket_sha256',
        'genre': 'genreNames', 'works': 'worksName', 'rights': 'rightsInfoName', 'cue': 'cueFileName',
    },
    'chart': {
        'hdd_hundredths': 'hdd_hundredths', 'display_level': 'display_level',
        'c2s_sha256': 'c2s_sha256', 'notes_designer': 'notes_designer',
    },
    'we': {
        'tag': 'tag', 'tag_id': 'tag_id', 'stars': 'stars',
        'star_raw': 'star_raw', 'c2s_sha256': 'c2s_sha256', 'notes_designer': 'notes_designer',
    },
}
ENCODED_FIELDS = {'genre', 'works', 'rights', 'cue'}


def checkpoint_diff(catalog, baseline, version):
    differences = []
    enrichments = Counter()
    previous = (('song', baseline.songs(version)), ('chart', baseline.charts(version)),
                ('we', baseline.worlds_end(version)))
    old = {(kind, row.get('chart_identity', row['identity'])): row for kind, rows in previous for row in rows
           if kind != 'song' or row['ex_type'] in (0, 2)}
    new = {(row['kind'], row['identity']): row for row in catalog.checkpoint(version)}
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if before is None or after is None:
            differences.append({'kind': key[0], 'identity': key[1],
                                'change': 'added' if after else 'missing',
                                'source': (after or before)['source'], 'basis': (after or before)['basis']})
            continue
        for field, payload in FIELDS[key[0]].items():
            old_value, new_value = before[field], after['payload'].get(payload)
            if field in ENCODED_FIELDS:
                old_value = json.loads(old_value)
            if field == 'notes_designer' and old_value is None and new_value is not None:
                enrichments[key[0] + ':notes_designer'] += 1
            elif old_value != new_value:
                differences.append({'kind': key[0], 'identity': key[1], 'change': field, 'before': before[field],
                                    'after': new_value, 'old_source': before['source'],
                                    'new_source': after['source']})
    picked = catalog.constants(version, include_inferred=True)
    legacy_picked = baseline.constants(version, include_inferred=True)
    for key in sorted(picked.keys() | legacy_picked.keys()):
        if picked.get(key) != legacy_picked.get(key):
            differences.append({'kind': 'chart', 'identity': key, 'change': 'selected_constant',
                                'before': str(legacy_picked[key]) if key in legacy_picked else None,
                                'after': str(picked[key]) if key in picked else None})
    return Counter(kind for kind, _ in new), differences, enrichments


def metrics(db):
    measurements = {
        'entities': """SELECT CASE WHEN kind='song' AND identity LIKE '2:%' THEN 'we_music' ELSE kind END,count(*)
                       FROM history_entities GROUP BY 1""",
        'dated_releases': """SELECT CASE WHEN x.kind='song' AND x.identity LIKE '2:%' THEN 'we_music' ELSE x.kind END,
                             count(DISTINCT e.identity) FROM history_events e JOIN history_entities x USING(identity)
                             WHERE e.kind='released' GROUP BY 1""",
        'batches': "SELECT kind||':'||timing,count(*) FROM history_batches GROUP BY kind,timing",
        'lifecycle': """SELECT x.kind||':'||e.kind,count(*) FROM history_events e JOIN history_entities x USING(identity)
                         WHERE e.field='availability' GROUP BY x.kind,e.kind""",
        'revisions': """SELECT x.kind||':'||e.field,count(*) FROM history_events e JOIN history_entities x USING(identity)
                         WHERE e.kind='revision' GROUP BY x.kind,e.field""",
        'revision_value_changes': """SELECT x.kind||':'||e.field,count(*) FROM history_events e
                                     JOIN history_entities x USING(identity)
                                     WHERE e.kind='revision' AND e.before!='null' AND e.after!='null'
                                     GROUP BY x.kind,e.field""",
        'assets': "SELECT kind,count(*) FROM history_assets GROUP BY kind",
        'asset_links': "SELECT relation,count(DISTINCT asset) FROM history_asset_links GROUP BY relation",
        'total': """SELECT 'raw_music',count(*) FROM raw_song_index UNION ALL
                     SELECT 'raw_game_events',count(*) FROM history_game_events UNION ALL
                     SELECT 'game_event_changes',count(*) FROM history_game_event_changes WHERE old_source IS NOT NULL UNION ALL
                     SELECT 'revision_rows',count(*) FROM history_revisions UNION ALL
                     SELECT 'observations',count(*) FROM history_observations UNION ALL
                     SELECT 'date_windows',count(*) FROM history_windows UNION ALL
                     SELECT 'we_without_c2s',count(*) FROM worlds_end_versions WHERE state='present' AND c2s_sha256 IS NULL UNION ALL
                     SELECT 'c2s_files',count(*) FROM c2s_sections UNION ALL
                     SELECT 'c2s_notation_unparsed',count(*) FROM c2s_sections WHERE notation_sha256 IS NULL UNION ALL
                     SELECT 'c2s_creator_missing',count(*) FROM c2s_sections WHERE creator IS NULL UNION ALL
                     SELECT 'option_alternate_fields',count(*) FROM history_events WHERE kind='option_variant' UNION ALL
                     SELECT 'publicity_linked_days',count(DISTINCT batch) FROM history_asset_links
                       WHERE relation='filename_date_matches_update' UNION ALL
                     SELECT 'historical_candidate_packages',count(*) FROM packages
                       WHERE classification IN ('base','historical_candidate')""",
    }
    return {name: dict(db.execute(query)) for name, query in measurements.items()}


def verify(database, baseline):
    catalog, legacy = OldHddHistory(database), OldHddHistory(baseline)
    result = {'bytes': database.stat().st_size, 'checkpoints': {}}
    for span in legacy.versions():
        version, day = span['version'], span['end_on']
        counts, differences, enrichments = checkpoint_diff(catalog, legacy, version)
        endpoint = {r['identity'] for r in catalog.checkpoint(version)}
        date_rows = catalog.catalog_on(day)
        daily_active = {r['identity'] for r in date_rows['rows']}
        result['checkpoints'][version] = {'counts': counts, 'differences': differences,
                                         'enrichments': enrichments,
                                         'date_checkpoint_conflicts': sorted(endpoint ^ daily_active)}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Database integrity or foreign keys failed')
        result.update(metrics(db))
    perf = {}
    for day in ('2015-07-16','2018-05-01','2021-08-05','2021-11-03'):
        times = []
        for _ in range(7):
            start = perf_counter()
            rows = catalog.catalog_on(day)
            times.append((perf_counter()-start)*1000)
        perf[day] = {'median_ms': round(median(times),1), 'returned': len(rows['rows']),
                     'uncertain': rows['uncertain_count'], 'coverage': rows['coverage']}
    result['date_query'] = perf
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--repeat', type=Path)
    args = parser.parse_args()
    report = verify(args.database, args.baseline)
    if args.repeat:
        report['reproducible'] = file_hash(args.database) == file_hash(args.repeat)
        if not report['reproducible']:
            raise ValueError('Repeated builds differ')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
