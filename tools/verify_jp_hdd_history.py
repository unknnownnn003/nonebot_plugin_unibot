"""Verify old checkpoint compatibility and SDHD content against archived Music.xml."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from statistics import median
from time import perf_counter
from xml.etree import ElementTree

import import_paradise_lost  # Offline service bootstrap
from _unibot_history.old_hdd_history import OldHddHistory
from old_hdd_scan import file_hash


def verify(database, baseline, root):
    catalog, old = OldHddHistory(database), OldHddHistory(baseline)
    result = {'sdbt': {}, 'sdhd': {}}
    for version in old.versions():
        name, day = version['version'], version['end_on']
        if catalog.checkpoint(name, active_only=False) != old.checkpoint(name, active_only=False):
            raise ValueError(f'Old checkpoint changed: {name}')
        if catalog.catalog_on(day, include_possible=True) != old.catalog_on(day, include_possible=True):
            raise ValueError(f'Old day query changed: {name}')
        result['sdbt'][name] = len(catalog.checkpoint(name))
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',) or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite integrity check failed')
        for edition in catalog.versions()[12:]:
            name, day, hdd = edition['version'], edition['end_on'], edition['hdd']
            sources = db.execute('''
                SELECT s.path,x.xml FROM history_batches b JOIN sources s
                ON b.batch='pkg:'||s.hdd||':'||s.package JOIN raw_xml x USING(path)
                WHERE s.hdd=? AND s.kind='music_xml' ORDER BY b.sequence,s.path
            ''', (hdd,))
            expected = {}
            for path, xml in sources:
                song = ElementTree.fromstring(xml)
                expected[int(song.findtext('name/id'))] = (song, path)
            rows = catalog.checkpoint(name, active_only=False)
            actual = {row['music_id'] for row in rows if row['kind'] == 'song'}
            if actual != set(expected):
                raise ValueError(f'SDHD song overlay differs: {name}')
            present = Counter()
            for row in rows:
                song, path = expected[row['music_id']]
                payload = row['payload']
                if row['source'] != path:
                    raise ValueError(f'Wrong overlay provenance: {name} {row["identity"]}')
                if row['kind'] == 'song':
                    if payload['title'] != song.findtext('name/str'):
                        raise ValueError(f'SDHD song title differs: {name} {row["identity"]}')
                    group = {'0': 'normal_songs', '2': 'we_songs'}.get(song.findtext('exType'), 'special_songs')
                else:
                    slot = payload['difficulty_label']
                    node = next(n for n in song.findall('fumens/*') if n.findtext('type/data') == slot)
                    if row['slot'] != int(node.findtext('type/id')):
                        raise ValueError(f'SDHD chart slot differs: {name} {row["identity"]}')
                    if row['kind'] == 'we':
                        group = 'we_charts'
                        if payload['star_raw'] != song.findtext('starDifType'):
                            raise ValueError(f'SDHD WE star differs: {name} {row["identity"]}')
                    else:
                        group = 'ultima' if row['difficulty'] == 'ULT' else 'normal_charts'
                        constant = int(node.findtext('level')) * 100 + int(node.findtext('levelDecimal'))
                        if payload['hdd_hundredths'] != constant:
                            raise ValueError(f'SDHD HDD constant differs: {name} {row["identity"]}')
                present[group, 'inventory'] += 1
                present[group, row['state']] += 1
            durations = []
            for _ in range(5):
                start = perf_counter()
                snapshot = catalog.catalog_on(day)
                durations.append((perf_counter() - start) * 1000)
            charts = catalog.charts(name, active_only=False)
            sample = [r for r in charts if r['c2s_path'] and r['package'] != 'A000']
            sample = (sample or charts)[0]
            catalog.resource_bytes(root, sample['c2s_path'], sample['c2s_sha256'])
            result['sdhd'][name] = {'counts': {'/'.join(key): count for key, count in sorted(present.items())},
                                    'date_median_ms': round(median(durations), 1),
                                    'dated_rows': len(snapshot['rows']),
                                    'date_uncertainty': snapshot['uncertain_count']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('--repeat', type=Path)
    args = parser.parse_args()
    result = verify(args.database, args.baseline, args.root)
    if args.repeat:
        result['reproducible'] = file_hash(args.database) == file_hash(args.repeat)
        if not result['reproducible']:
            raise ValueError('Repeated builds differ')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
