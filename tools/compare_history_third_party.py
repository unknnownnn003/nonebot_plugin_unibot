"""Compare public Aqua metadata against a pinned B2 release without writing to it."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from build_history_catalog import atomic_write, format_json, private_root, validate_package
from export_history_constants import candidate_index, classification, checkpoint_rows, csv_rows, jp_snapshots
from _unibot_history.history import HistoryCatalog, catalog_state, normalize_name
from history_music_xml import DIFFICULTIES, raw_constant


@dataclass(frozen=True)
class Reading:
    music_id: str
    title: str
    artist: str
    release_tag: str
    difficulty: str
    constant: Decimal
    file: str
    locator: str
    difficulty_basis: str


# Read only the catalog INSERT grammar. No third-party SQL is executed.
HEADER = re.compile(r'(?im)^\s*INSERT INTO `?(chuni(?:_music|_music_level)|chusan_music(?:_level)?)`?\s*\(([^)]+)\)\s*VALUES\s*')
TOKEN = r"(?:[bB]'[01]'|'(?:\\.|''|[^'\\])*'|-?[0-9]+|true|false|null)"
ROW = re.compile(r'\(\s*(' + TOKEN + r'(?:\s*,\s*' + TOKEN + r')*)\s*\)\s*([,;])', re.I)
TOKENIZER = re.compile(TOKEN, re.I)


def literals(data):
    text = data.decode('utf-8-sig')
    for match in HEADER.finditer(text):
        columns = [name.strip(' `\t\r\n') for name in match[2].split(',')]
        cursor = match.end()
        while True:
            row = ROW.match(text, cursor)
            if row is None:
                raise ValueError(f'Unsupported catalog INSERT row at offset {cursor}')
            fields = TOKENIZER.findall(row[1])
            item = dict(zip(columns, fields, strict=True))
            yield match[1], item, f'char:{cursor}'
            if row[2] == ';':
                break
            cursor = row.end()
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1


def sql_string(token):
    if token.lower() == 'null':
        return ''
    if not token.startswith("'"):
        raise ValueError('Expected quoted SQL text')
    return re.sub(r"\\(['\\\"])", r'\1', token[1:-1].replace("''", "'"))


def sql_readings(raw, metadata_files, value_files, legacy):
    """Extract ordered INSERT observations without replaying migration state."""
    table = 'chuni_music' if legacy else 'chusan_music'
    songs = {}
    for file in metadata_files:
        for name, row, _ in literals(raw[file]):
            if name == table:
                songs[row['music_id']] = tuple(sql_string(row[k]) for k in ('name','artist_name','release_version'))
    readings, all_slots = {}, set()
    for file in value_files:
        for name, row, locator in literals(raw[file]):
            if name != table + '_level':
                continue
            key, diff = row['music_id'], int(row['diff'])
            if not 0 <= diff < (4 if legacy else 5):
                continue
            all_slots.add((key,diff))
            level = int(row['level'])
            if row['enable'].lower() not in ('true', '1', "b'1'") or not level:
                readings.pop((key,diff), None)
                continue
            title, artist, tag = songs.get(key, ('','',''))
            value = raw_constant(level, int(row['level_decimal']))
            readings[key,diff] = Reading(key,title,artist,tag,DIFFICULTIES[diff],value,file,locator,'explicit_sql_diff')
    return readings.values(), len(all_slots), len({key for key, _ in all_slots})


def aqua_readings(data, file):
    obj = json.loads(data, parse_float=Decimal)
    readings = []
    for key, song in obj.items():
        # The generator removes disabled slots before writing. Only the common
        # contiguous 4/5-note layout can be proposed for ordinary difficulties.
        notes = song['notes']
        if song['worldsEndTag'] != 'Invalid' or len(notes) not in (4,5):
            continue
        for idx, note in enumerate(notes):
            value = note['lv']
            if isinstance(value, bool) or not isinstance(value, (Decimal,int)) or not 0 < value < 20:
                raise ValueError(f'Invalid AquaNet level at {key}:{idx}')
            readings.append(Reading(key,str(song['name']),str(song['composer'] or ''),str(song['ver'] or ''),DIFFICULTIES[idx],
                                    Decimal(value),file,f'$[{key!r}].notes[{idx}]', 'inferred_array_position'))
    return readings, sum(len(song['notes']) for song in obj.values() if song['worldsEndTag']=='Invalid')


def official_keys(conn):
    rows = conn.execute("""
        SELECT json_extract(o.value_json,'$.official_id'), o.song_id,
               coalesce(json_extract(o.raw_json,'$.title'), json_extract(o.raw_json,'$.name')),
               json_extract(o.value_json,'$.artist')
        FROM observation o WHERE o.region='JP' AND o.field='song_metadata'
          AND o.artifact_id IN ('official-jp','oto-jp','oto-deleted')
    """)
    index = defaultdict(set)
    for key, song, title, artist in rows:
        if key is not None and title and artist:
            index[str(key),normalize_name(title),normalize_name(artist)].add(song)
    return index


def compare(catalog, root, input_manifest, output):
    source_meta = input_manifest['artifacts']
    raw = {}
    for file, evidence in source_meta.items():
        path = root / file
        if path.resolve().parent != root.resolve():
            raise ValueError('Evidence file escapes the source directory')
        data = path.read_bytes()
        if sha256(data).hexdigest() != evidence['sha256']:
            raise ValueError(f'Evidence hash mismatch: {file}')
        raw[file] = data
    all_out, gaps, differences = [], [], []
    summary = []
    with catalog._connect() as conn:
        index = official_keys(conn)
        candidates = candidate_index(conn)
        snapshots = {snap['version']:snap for snap in jp_snapshots(conn)}
        targets = {}
        for case in input_manifest['comparisons']:
            version = case['comparison_version']
            snap = snapshots[version]
            if version not in targets:
                targets[version] = {(r['song_id'], r['difficulty']):r for r in checkpoint_rows(conn,snap)}
            if case['adapter'] == 'aqua-json':
                file = case['files'][0]
                values, raw_slots = aqua_readings(raw[file],file)
                dataset_song_count = len(json.loads(raw[file]))
            else:
                values, raw_slots, dataset_song_count = sql_readings(
                    raw, case['metadata'], case['files'], case['adapter'] == 'legacy-sql'
                )
            count = Counter()
            for value in sorted(values,key=lambda r:(int(r.music_id),DIFFICULTIES.index(r.difficulty))):
                ids = index.get((value.music_id,normalize_name(value.title),normalize_name(value.artist)), ())
                song = next(iter(ids)) if len(ids)==1 else None
                b2 = targets[version].get((song,value.difficulty))
                count['exact_numeric'] += 1
                count['unique_song_identity'] += song is not None
                status = 'unmatched_identity' if song is None else 'outside_b2_target'
                base = base_observation = chart = revision = state = None
                if b2 is not None:
                    category = classification(b2)
                    base, base_observation = b2['constant_tenths'], b2['constant_observation']
                    chart, revision, state = b2['chart_id'], b2['revision_id'], catalog_state(b2)
                    candidate = candidates.get((revision,snap['on_date']))
                    if category in ('unknown','display_only') and candidate is not None:
                        category = 'evidence_insufficient'
                    if category == 'verified':
                        status = 'agrees_b2' if value.constant*10 == base else 'differs_b2'
                    elif category == 'conflict':
                        status = 'b2_open_conflict'
                    else:
                        status = 'gap_' + category
                    count['unique_b2_slot'] += 1
                    if status.startswith('gap_') or status == 'b2_open_conflict':
                        count['gap_playable'] += state == 'playable'
                    if status == 'gap_estimated' and base is not None and value.constant * 10 != base:
                        count['estimates_differ'] += 1
                    count['gap_playability_unconfirmed'] += category!='verified' and state=='unknown'
                count[status] += 1
                evidence = source_meta[value.file]
                record = (case['id'], case['version_evidence'], case.get('observed_on'), version, snap['on_date'],
                          case['grade'], value.music_id,value.title,value.artist,value.release_tag,value.difficulty,
                          str(value.constant),song,chart,revision,state,status,base,base_observation,
                          candidate[0] if b2 is not None and candidate else None, evidence['url'], evidence['sha256'],
                          value.locator, value.difficulty_basis, 'unverified')
                if case.get('calibration_only'):
                    continue
                all_out.append(record)
                if status.startswith('gap_') or status=='b2_open_conflict':
                    gaps.append(record)
                if status=='differs_b2' or status=='b2_open_conflict':
                    differences.append(record)
            summary.append({'comparison':case['id'],'target':version,'b2_date':snap['on_date'],
                            'calibration_only':case.get('calibration_only', False),
                            'dataset_songs':dataset_song_count,'raw_ordinary_slots':raw_slots,
                            'enabled_songs_with_numbers':len({r.music_id for r in values}),
                            'difficulty_basis': 'inferred_array_position' if case['adapter'] == 'aqua-json' else 'explicit_sql_diff',
                            **count})
    output.mkdir(parents=True,exist_ok=True)
    fields = ('source','version_evidence','observed_or_published_on','b2_version','b2_date','evidence_grade',
              'music_id','title','artist','release_tag','difficulty','constant','b2_song_id','b2_chart_id','b2_revision_id',
              'b2_availability','result','b2_tenths','b2_observation_id','b2_candidate_id','source_url','source_sha256','locator',
              'difficulty_basis','historical_revision_identity')
    csv_rows(output/'candidate_constants.csv',fields,all_out)
    csv_rows(output/'b2_gap_matches.csv',fields,gaps)
    csv_rows(output/'third_party_conflicts.csv',fields,differences)
    result = {'b2_release':catalog.release_id,'candidate_rows':len(all_out),'gap_rows':len(gaps),
              'difference_rows':len(differences),
              'distinct_gap_slots':len({(row[3],row[13]) for row in gaps}),
              'distinct_difference_slots':len({(row[3],row[13]) for row in differences}),
              'distinct_explicit_difficulty_gap_slots':len({(row[3],row[13]) for row in gaps
                                                             if row[-2] == 'explicit_sql_diff'}),
              'comparisons':summary}
    atomic_write(output/'comparison.json',format_json(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    catalog = HistoryCatalog(args.catalog)
    validate_package(catalog.path.parent)
    input_manifest = json.loads(args.manifest.read_bytes())
    print(json.dumps(compare(catalog,args.sources,input_manifest,private_root(args.output)),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
