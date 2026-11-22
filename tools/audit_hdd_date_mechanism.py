"""Audit calendar hypotheses against official claims without modifying the HDD catalog."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sqlite3
from xml.etree import ElementTree

import import_paradise_lost  # Loads the offline service namespace.
from _unibot_history.old_hdd_history import OldHddHistory

OPEN = re.compile(r'v([12])(?:_(\d\d))? (\d{1,2})_(\d+)')
CALENDAR = re.compile(r'20\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])')
TIMING = re.compile(r'open|release|date|time|lock|limit|sched|expire|valid|period', re.I)


def parsed_date(value):
    if value and CALENDAR.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def members(node, field):
    entry = node.find(field)
    return tuple(entry.findtext(key) for key in ('id', 'str', 'data')) if entry is not None else None


def music_fields(node):
    return {'net_open': members(node, 'netOpenName'), 'release_tag': members(node, 'releaseTagName'),
            **{name: node.findtext(name) for name in ('releaseDate', 'firstLock', 'disableFlag',
                                                     'enableUltima', 'isGiftMusic', 'exType')}}


def inventory(db, roots):
    types, fields, days, labels = Counter(), Counter(), Counter(), defaultdict(Counter)
    scan_errors = []
    checked = []
    sampled = set()
    date_examples = {}
    reference_fields = Counter()
    reference_examples = {}
    for row in db.execute('''SELECT path,hdd,package,sha256,xml FROM raw_xml JOIN sources USING(path)
                             ORDER BY hdd,path'''):
        generation = row['hdd'].split('_')[0]
        try:
            node = ElementTree.fromstring(row['xml'])
        except ElementTree.ParseError:
            scan_errors.append(row['path'])
            continue
        types[generation, node.tag] += 1
        for item in node.iter():
            if TIMING.search(item.tag):
                fields[generation, node.tag, item.tag] += 1
            if parsed_date((item.text or '').strip()):
                key = generation, node.tag, item.tag
                days[key] += 1
                date_examples.setdefault(key, [row['path'], (item.text or '').strip()])
        if node.tag in ('EventData', 'CourseData', 'MapData', 'MapAreaData'):
            for item in node.iter():
                if item.tag not in ('musicName', 'mapName', 'mapAreaName', 'stopReleaseEventName',
                                    'stopUnlockEventName', 'timeTableName', 'ddsBannerName'):
                    continue
                identifier = item.findtext('id')
                if identifier is not None and identifier.isdecimal():
                    key = generation, node.tag, item.tag
                    reference_fields[key] += 1
                    reference_examples.setdefault(key, [row['path'], identifier, item.findtext('str')])
        key = members(node, 'netOpenName')
        if key:
            labels[generation, row['hdd'], key][node.tag] += 1
        sample = (row['hdd'], node.tag)
        if (generation in roots and node.tag in ('MusicData', 'EventData', 'CourseData', 'MapData', 'MapAreaData')
                and row['package'] == 'A000' and sample not in sampled):
            try:
                OldHddHistory.resource_bytes(roots[generation], row['path'], row['sha256'])
            except (OSError, ValueError) as error:
                checked.append({'source': row['path'], 'error': type(error).__name__})
            else:
                checked.append({'source': row['path'], 'sha256': row['sha256']})
            sampled.add(sample)
    id_labels = defaultdict(set)
    label_ids = defaultdict(set)
    for generation, hdd, key in labels:
        id_labels[generation, hdd, key[0]].add(key[1:])
        label_ids[generation, hdd, key[1:]].add(key[0])
    sdhd_codes = []
    for (_, hdd, (identity, text, _data)), counts in labels.items():
        match = OPEN.fullmatch(text or '')
        if hdd.startswith('SDHD_') and match and match[1] == '2' and match[2] and identity.isdecimal():
            prefixes = (0, 5, 10, 15, 20, 25, 30, 40, 45, 50)
            if int(match[2]) not in prefixes:
                continue
            expected_base = 2000 + prefixes.index(int(match[2])) * 100
            offset = int(identity) - expected_base
            sdhd_codes.append({'hdd': hdd, 'id': int(identity), 'prefix': match[2], 'label_group': int(match[3]),
                               'label_slot': int(match[4]), 'offset': offset, 'types': dict(counts)})
    return {
        'sdhd_numbering': sdhd_codes,
        'types': [[*key, count] for key, count in sorted(types.items())],
        'timing_fields': [[*key, count] for key, count in sorted(fields.items())],
        'date_shaped_fields': [[*key, count, *date_examples[key]] for key, count in sorted(days.items())],
        'open_labels': [{'generation': g, 'hdd': hdd, 'members': key, 'types': dict(counts)}
                        for (g, hdd, key), counts in labels.items()],
        'id_conflicts': [[*key, sorted(values)] for key, values in id_labels.items() if len(values) > 1],
        'text_conflicts': [[key[0], key[1], key[2], sorted(values)]
                           for key, values in label_ids.items() if len(values) > 1],
        'reference_fields': [[*key, count, *reference_examples[key]]
                             for key, count in sorted(reference_fields.items())],
        'source_samples': checked, 'xml_errors': scan_errors,
    }


def features(row):
    tag = row['net_open']
    package = row['package']
    return {
        'hdd_label': (row['hdd'], *tag) if tag else None,
        'hdd_nondefault_label': (row['hdd'], *tag) if tag and not (tag[1] or '').endswith('00_0') else None,
        'package': row['package'],
        'package_label': (package, *tag) if tag and package else None,
        'package_label_type_mode': (row['package'], *tag, row['category'], row['mode']) if tag and package else None,
        'package_label_type': (row['package'], *tag, row['category']) if tag and package else None,
    }


def score(rows, proposals):
    result = defaultdict(Counter)
    errors = []
    for row, predicted in zip(rows, proposals, strict=True):
        for group in ('all', row['category'], row['category'] + ':' + row['mode'], 'edition:' + row['version'], 'mode:' + row['mode']):
            result[group]['samples'] += 1
            if predicted:
                result[group]['predicted'] += 1
                result[group]['correct'] += predicted == row['day']
        if predicted and predicted != row['day']:
            errors.append({'identity': row['identity'], 'title': row['title'], 'edition': row['version'],
                           'expected': row['day'], 'predicted': predicted, 'package': row['package'],
                           'net_open': row['net_open'], 'mode': row['mode'], 'official': row['url']})
    return {'groups': result, 'errors': errors}


def official_samples(db):
    samples = {}
    conflicts = set()
    for row in db.execute('''SELECT c.identity,c.effective_on AS day,c.mode,c.announcement AS url,
        c.package,c.source,b.version,s.hdd,l.build_on,e.kind,e.difficulty,coalesce(e.parent,e.identity) AS subject,x.xml
        FROM sdhd_notice_claim c JOIN history_entities e USING(identity)
        JOIN history_batches b ON b.batch=c.package JOIN sources s ON s.path=c.source
        JOIN raw_xml x ON x.path=c.source
        LEFT JOIN sdhd_layers l ON l.version=b.version AND l.package=s.package AND l.status='indexed'
        WHERE c.kind='first' AND (e.kind IN ('song','we') OR e.difficulty='ULT')
        ORDER BY c.identity,c.effective_on,c.announcement'''):
        node = ElementTree.fromstring(row['xml'])
        record = {key: row[key] for key in row.keys() if key != 'xml'} | music_fields(node)
        if row['kind'] == 'song' and record['exType'] != '0':
            continue  # An independent WE parent duplicates its chart's announcement.
        record['category'] = row['difficulty'] if row['difficulty'] == 'ULT' else row['kind']
        record['title'] = node.findtext('name/str')
        record['release_day'] = parsed_date(record['releaseDate'])
        identity = row['identity']
        if identity in samples and samples[identity]['day'] != row['day']:
            conflicts.add(identity)
        samples.setdefault(identity, record)
    return [record for key, record in samples.items() if key not in conflicts]


def legacy_samples(db):
    samples = []
    for row in db.execute("""SELECT x.identity,coalesce(e.parent,e.identity) AS subject,
                x.effective_on AS day,x.evidence AS url,CASE WHEN s.package IS NOT NULL THEN 'pkg:'||s.hdd||':'||s.package END AS package,s.path AS source,s.hdd,
                b.version,e.kind AS category,'legacy_archived' AS mode,NULL AS build_on,
                json_extract(r.payload,'$.title') AS title,z.xml
                FROM history_events x JOIN history_entities e USING(identity)
                JOIN history_batches b USING(batch) JOIN sources s ON s.path=x.new_source
                JOIN raw_xml z ON z.path=s.path
                LEFT JOIN history_revisions r ON r.revision=(SELECT revision FROM history_observations o
                    WHERE o.identity=coalesce(e.parent,e.identity) AND o.source=s.path LIMIT 1)
                WHERE x.kind='released' AND x.effective_on IS NOT NULL AND s.hdd GLOB 'SDBT_*'
                    AND (e.kind='we' OR (e.kind='song' AND e.identity LIKE '0:%'))
                ORDER BY x.identity,x.effective_on,s.path"""):
        node = ElementTree.fromstring(row['xml'])
        record = {key: row[key] for key in row.keys() if key != 'xml'} | music_fields(node)
        record['release_day'] = parsed_date(record['releaseDate'])
        record['title'] = node.findtext('name/str')
        samples.append(record)
    return samples


def next_thursday(value):
    if value:
        built = date.fromisoformat(value)
        return (built + timedelta(days=(3 - built.weekday()) % 7 or 7)).isoformat()
    return None


def evaluate(samples):
    # Every object from a public update date stays in one partition. Sorting
    # dates within each era prevents a dense version from filling the holdout.
    years = defaultdict(set)
    for row in samples:
        years[row['day'][:4]].add(row['day'])
    held_days = {day for dates in years.values() for i, day in enumerate(sorted(dates)) if i % 3 == 2}
    train = [row for row in samples if row['day'] not in held_days]
    test = [row for row in samples if row['day'] in held_days]
    known = defaultdict(lambda: defaultdict(set))
    all_dates = defaultdict(lambda: defaultdict(set))
    for row in samples:
        for rule, key in features(row).items():
            if key is not None:
                all_dates[rule][key].add(row['day'])
                if row['day'] not in held_days:
                    known[rule][key].add(row['day'])
    result = {'discovery_dates': sorted({row['day'] for row in train}), 'holdout_dates': sorted(held_days),
              'discovery_samples': len(train), 'holdout_samples': len(test), 'rules': {}}
    for rule, mapping in all_dates.items():
        estimates = [known[rule].get(features(row)[rule], set()) for row in test]
        result['rules'][rule] = score(test, [min(values) if len(values) == 1 else None for values in estimates])
        result['rules'][rule]['multiple_days'] = [{'key': key, 'days': sorted(days)} for key, days in mapping.items()
                                                  if len(days) > 1]
    # A second experiment supplies one independently dated identity per
    # update. All companion identities remain withheld, including the anchor's
    # parent song and other charts of that song.
    anchored = defaultdict(lambda: defaultdict(set))
    anchor_groups = set()
    ordered = sorted(test, key=lambda row: (row['day'], row['category'] != 'song', row['identity']))
    for day in sorted(held_days):
        row = next(item for item in ordered if item['day'] == day)
        anchor_groups.add(row['subject'])
        for rule, key in features(row).items():
            if key is not None:
                anchored[rule][key].add(day)
    remaining_test = [row for row in test if row['subject'] not in anchor_groups]
    result['one_anchor_per_held_day'] = {}
    for rule in all_dates:
        estimates = [anchored[rule].get(features(row)[rule], set()) | known[rule].get(features(row)[rule], set())
                     for row in remaining_test]
        result['one_anchor_per_held_day'][rule] = score(
            remaining_test, [min(values) if len(values) == 1 else None for values in estimates])
    result['known_raw_date_counterexamples'] = [row for row in samples if row['release_day']
                                               and row['release_day'] != row['day']]
    result['rules']['raw_release_date'] = score(test, [row['release_day'] for row in test])
    result['rules']['ordinary_raw_release_date'] = score(test, [row['release_day'] if row['category'] == 'song' else None
                                                               for row in test])
    result['release_date_by_mode'] = score(samples, [row['release_day'] for row in samples])
    result['rules']['first_thursday_after_build'] = score(test, [next_thursday(row['build_on']) for row in test])
    result['rules']['package_build_day'] = score(test, [row['build_on'] for row in test])
    return result


def remaining(db, anchors):
    dates = defaultdict(set)
    for record in anchors:
        key = features(record)['package_label_type']
        if key:
            dates[key].add(record['day'])
    sql = '''SELECT f.identity,e.kind,e.difficulty,e.music_id,f.basis,o.batch AS package,o.source,x.xml
        FROM sdhd_first_release f JOIN history_entities e USING(identity)
        JOIN history_observations o USING(identity)
        JOIN history_batches b USING(batch) JOIN versions v USING(version) JOIN raw_xml x ON x.path=o.source
        WHERE f.exact_on IS NULL AND b.kind='package' AND v.ordinal>12 AND o.state='hdd_enabled'
        ORDER BY v.ordinal,b.earliest_on,b.sequence,o.source,f.identity'''
    first = {}
    variants = defaultdict(set)
    for row in db.execute(sql):
        node = ElementTree.fromstring(row['xml'])
        if row['identity'] not in first:
            record = {key: row[key] for key in row.keys() if key != 'xml'}
            record['category'] = row['difficulty'] if row['difficulty'] == 'ULT' else (
                'song' if row['kind'] == 'song' and node.findtext('exType') == '0' else row['kind'])
            record['title'] = node.findtext('name/str')
            record['net_open'] = members(node, 'netOpenName')
            key = (record['package'], *record['net_open'], record['category']) if record['net_open'] else None
            record['anchor_dates'] = sorted(dates.get(key, ())) if key else []
            first[row['identity']] = record
        raw = parsed_date(node.findtext('releaseDate'))
        if raw:
            variants[row['identity']].add(raw)
    for identity, record in first.items():
        record['song_level_raw_dates'] = sorted(variants[identity])
        record['status'] = 'candidate_only' if record['song_level_raw_dates'] or record['anchor_dates'] else 'unresolved'
    return list(first.values())


def run(db, roots):
    result = inventory(db, roots)
    samples = official_samples(db)
    result['validation'] = evaluate(samples)
    result['official_samples'] = samples
    label_days = {(item['day'], tuple(item['net_open'] or ())) for item in samples}
    result['distinct_open_labels_by_day'] = dict(sorted(Counter(day for day, _ in label_days).items()))
    legacy = legacy_samples(db)
    result['legacy_validation'] = evaluate(legacy)
    result['legacy_samples'] = legacy
    result['gaps'] = remaining(db, samples)
    result['package_order'] = [dict(row) for row in db.execute('''SELECT p.hdd,p.package,p.data_version,
        p.classification,p.path,b.sequence,b.earliest_on,b.latest_on FROM packages p
        LEFT JOIN history_batches b ON b.batch='pkg:'||p.hdd||':'||p.package ORDER BY p.hdd,b.sequence,p.package''')]
    result['resources'] = dict(db.execute('SELECT kind,count(*) FROM history_assets GROUP BY kind'))
    result['image_examples'] = [dict(row) for row in db.execute('''SELECT l.location,l.asset,l.source,l.title,l.batch,
        s.hdd FROM history_asset_links l JOIN sources s ON s.path=l.location
        WHERE l.location LIKE '%info_music_new0212.dds' AND s.hdd='SDHD_2.00'
           OR l.location LIKE '%info_music_new0005.dds' AND s.hdd='SDBT_1.06'
        ORDER BY l.location''')]
    result['official_images'] = db.execute('SELECT count(DISTINCT image_sha256) FROM sdhd_notice_claim').fetchone()[0]
    result['image_hash_matches'] = db.execute('''SELECT count(DISTINCT c.image_sha256) FROM sdhd_notice_claim c
        JOIN sources s ON s.sha256=c.image_sha256 AND s.kind='image' ''').fetchone()[0]
    result['revision_batches'] = [dict(row) for row in db.execute('''SELECT x.field,b.version,b.batch,
        b.sequence,count(*) AS observations,count(x.effective_on) AS dated FROM history_events x
        JOIN history_batches b USING(batch) WHERE x.kind='hdd_revision'
        AND x.field IN ('hdd_hundredths','display_level','notation_sha256')
        GROUP BY x.field,b.version,b.batch ORDER BY x.field,b.version,b.sequence,b.batch''')]
    result['in_game_notice_relationships'] = dict(db.execute('''SELECT relation,count(*) FROM history_asset_links
        WHERE batch GLOB 'pkg:*' GROUP BY relation'''))
    result['physical_timetables'] = []
    for generation, pattern in (('SDBT', '*/data/A000/timeTable/*/*.xml'),
                                ('SDHD', '*/HDD/*/data/A000/timeTable/*/*.xml')):
        if generation not in roots:
            continue
        for path in sorted(roots[generation].glob(pattern)):
            tree = ElementTree.parse(path).getroot()
            result['physical_timetables'].append({'generation': generation,
                'path': path.relative_to(roots[generation]).as_posix(),
                'name': tree.findtext('name/str'),
                'parameters': {leaf.tag: leaf.text for leaf in tree.iter() if not len(leaf)}})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--old-root', type=Path)
    parser.add_argument('--new-root', type=Path)
    args = parser.parse_args()
    roots = {key: path for key, path in (('SDBT', args.old_root), ('SDHD', args.new_root)) if path}
    with closing(sqlite3.connect(args.database.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        result = run(db, roots)
    output = args.output.resolve()
    if any(output.is_relative_to(root.resolve()) for root in roots.values()):
        parser.error('Output must be outside the HDD roots')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'mechanism.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'gap-candidates.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        keys = ['identity', 'kind', 'difficulty', 'music_id', 'basis', 'package', 'source', 'category', 'title', 'net_open',
                'song_level_raw_dates', 'anchor_dates', 'status']
        writer = csv.DictWriter(stream, keys)
        writer.writeheader()
        writer.writerows(result['gaps'])


if __name__ == '__main__':
    main()
