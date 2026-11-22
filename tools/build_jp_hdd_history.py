"""Extend a verified SDBT catalog with real SDHD package observations."""
from collections import defaultdict
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import mkdtemp
from time import perf_counter
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile

from old_hdd_evidence import normalize_name
from old_hdd_scan import encoded, file_hash
from old_hdd_timeline import batch, event, index_event_changes, shift
from sdhd_public import add_ultima_evidence, read_availability
from sdhd_scan import SDHD, compact_date, issue, parse_record, read_layers, scan_auxiliary


@dataclass(frozen=True)
class Observation:
    identity: str
    music_id: int
    kind: str
    slot: int | None
    difficulty: str | None
    parent: str | None
    source: str
    payload: dict
    enabled: bool
    low: str
    batch: str
    revision: str
    resource_path: str | None = None
    resource_ref: str | None = None


def normalize(value):
    return normalize_name(value or '')


def label(payload, key):
    return dict(payload.get(key) or ()).get('str')


def fingerprint(payload):
    return payload.get('notation_sha256') or payload.get('c2s_sha256')


class Histories:
    def __init__(self, db):
        self.db = db
        self.songs = defaultdict(dict)
        self.by_id = defaultdict(set)
        self.by_title = defaultdict(set)
        self.old = {}
        self.legacy_present = set()
        self.seen_sdhd_charts = set()
        self.first = {}
        self.release_hints = defaultdict(set)
        self.traces = {}
        rows = db.execute("""
            SELECT e.*,o.source,o.state,b.version,r.revision,r.payload FROM history_observations o
            JOIN history_entities e USING(identity) JOIN history_revisions r USING(revision)
            JOIN history_batches b USING(batch) JOIN versions v USING(version)
            WHERE b.kind='checkpoint' ORDER BY v.ordinal,e.kind DESC,e.identity
        """)
        for row in rows:
            self.old[row['identity']] = dict(row) | {'payload': json.loads(row['payload'])}
            if row['version'] == 'PARADISE LOST' and row['state'] == 'present':
                self.legacy_present.add(row['identity'])
        for row in self.old.values():
            if row['kind'] == 'song':
                self.remember(row['identity'], row['music_id'], row['payload'], row['source'])
            elif row['parent']:
                self.songs[row['parent']][row['slot'], row['difficulty']] = (
                    row['identity'], row['music_id'], row['payload'], row['source'])
        self.known_release = {row[0]: row[1] for row in db.execute("""
            SELECT identity,min(effective_on) FROM history_events
            WHERE kind='released' AND effective_on IS NOT NULL GROUP BY identity
        """)}

    def remember(self, identity, music_id, payload, source):
        self.by_id[music_id].add(identity)
        self.by_title[normalize(payload['title'])].add(identity)
        self.songs[identity][None] = (identity, music_id, payload, source)

    def link(self, new, old, kind, evidence):
        self.db.execute('INSERT OR IGNORE INTO history_identity_links VALUES (?,?,?,?,?,?)',
                        (new.identity, old[0], kind, new.source, old[3], evidence))

    def observe(self, batch_id, low, source, music_id, kind, slot, difficulty, parent, identity, payload, enabled,
                *, resource_path=None, resource_ref=None):
        content = encoded(payload)
        revision = sha256((identity + '\0' + content).encode('utf-8')).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO history_entities VALUES (?,?,?,?,?,?)',
                        (identity, kind, parent, music_id, slot, difficulty))
        self.db.execute('INSERT OR IGNORE INTO history_revisions VALUES (?,?,?)', (revision, identity, content))
        self.db.execute('INSERT OR IGNORE INTO history_observations VALUES (?,?,?,?,?,?)',
                        (batch_id, identity, revision, source, 'hdd_enabled' if enabled else 'disabled',
                         'sdhd_package_observation'))
        self.db.execute('INSERT OR IGNORE INTO sdhd_observed_ids VALUES (?,?,?,?)',
                        (batch_id, music_id, identity, source))
        row = Observation(identity, music_id, kind, slot, difficulty, parent, source, payload, enabled,
                          low, batch_id, revision, resource_path, resource_ref)
        if enabled and identity not in self.old and (identity not in self.first or low < self.first[identity].low):
            self.first[identity] = row
        return row

    def record(self, version, parsed, batch_id, low, owners, *, identities=None):
        song, payload, charts = parsed
        music_id = song.music_id
        chart_fingerprints = {fingerprint(data) for _, data in charts} - {None}
        candidates = self.by_id[music_id] | self.by_title[normalize(song.title)]
        matches = []
        for candidate in sorted(candidates):
            archived = self.songs[candidate][None]
            _, previous_id, previous, _ = archived
            if previous.get('exType') != payload.get('exType'):
                continue
            same_title = normalize(previous['title']) == normalize(song.title)
            same_artist = bool(song.artist and normalize(previous['artist']) == normalize(song.artist))
            cue = bool(song.cue_ref and song.cue_ref == label(previous, 'cueFileName'))
            shared = chart_fingerprints & {fingerprint(r[2]) for k, r in self.songs[candidate].items() if k}
            jacket = bool(song.jacket_hash and song.jacket_hash == previous.get('jacket_sha256'))
            if ((previous_id == music_id and same_title and same_artist and (cue or jacket or shared))
                    or (shared and same_artist and (same_title or (cue and jacket)))):
                matches.append(candidate)
        exact_id = [key for key in matches if self.songs[key][None][1] == music_id]
        matches = [key for key in (exact_id or matches) if owners.get(key, music_id) == music_id]
        prior_id = matches[0] if len(matches) == 1 else None
        proposed = f'sdhd:{batch_id.rsplit(":", 1)[-1]}:{version}:{song.ex_type}:{music_id}'
        identity = identities[0].identity if identities else prior_id or proposed
        previous_slots = self.songs[identity].copy()
        playable_type = song.ex_type in (0, 2)
        song_row = self.observe(batch_id, low, song.source, music_id, 'song', None, None, None, identity,
                                payload, playable_type and song.disabled is False,
                                resource_path=song.jacket_path, resource_ref=song.jacket_ref)
        if not prior_id and not identities:
            for candidate in sorted(candidates - {identity}):
                self.link(song_row, self.songs[candidate][None], 'candidate_song_continuity',
                          'Same archive ID or normalized title, without a unique audio, artist and content match')
        self.remember(identity, music_id, payload, song.source)
        seed = {(row.slot, row.difficulty): row for row in identities or () if row.slot is not None}
        seen = set()
        result = [song_row]
        release = compact_date(payload.get('releaseDate'))
        if release:
            self.release_hints[identity].add((release, song.source, None))
        for chart, data in charts:
            key = chart.slot, chart.difficulty
            if key in seen:
                issue(self.db, version, song.source, 'duplicate_chart_slot', encoded(key))
                continue
            seen.add(key)
            previous = previous_slots.get(key)
            if not previous and chart.difficulty == 'WE':
                previous = next((row for slot, row in previous_slots.items() if slot and slot[1] == 'WE'), None)
            same = previous and fingerprint(data) is not None and fingerprint(data) == fingerprint(previous[2])
            # Across the cabinet boundary or a numeric ID replacement, require
            # matching notation. Otherwise retain both charts and the candidate.
            established = previous and (same or
                (previous[0] in self.seen_sdhd_charts and previous[1] == music_id))
            chart_id = (seed[key].identity if key in seed else previous[0] if established
                        else f'{identity}:sdhd:{version}:{music_id}:{chart.slot}:{chart.difficulty}')
            kind = 'we' if chart.difficulty == 'WE' else 'chart'
            observed = self.observe(batch_id, low, song.source, music_id, kind, chart.slot, chart.difficulty,
                                    identity, chart_id, data, song_row.enabled and data['enabled'] and bool(chart.digest),
                                    resource_path=chart.path, resource_ref=chart.ref)
            if previous and chart_id != previous[0]:
                self.link(observed, previous, 'candidate_chart_continuity',
                          'Matched song and difficulty but different or unavailable notation at cabinet/ID boundary')
            self.songs[identity][key] = (chart_id, music_id, data, song.source)
            self.seen_sdhd_charts.add(chart_id)
            result.append(observed)
        return result


def external_dates(db, histories, path):
    if not path:
        return
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as other:
        rows = other.execute("""
            SELECT n.normalized,o.effective_on,o.locator,a.id,a.url,a.sha256
            FROM observation o JOIN song_name n ON n.song_id=o.song_id AND n.artifact_id=o.artifact_id
            JOIN source_artifact a ON a.id=o.artifact_id
            WHERE o.region='JP' AND o.field='song_metadata' AND o.grain='day'
              AND o.effective_on=json_extract(o.value_json,'$.release')
            ORDER BY 1,2,3
        """)
        for title, day, locator, reference, url, digest in rows:
            candidates = histories.by_title[title]
            if len(candidates) != 1:
                continue
            identity = next(iter(candidates))
            if (identity not in histories.first or
                    histories.first[identity].payload.get('exType') != '0'):
                continue
            reference = f'sdhd-date:{reference}'
            db.execute('INSERT OR IGNORE INTO references_used VALUES (?,?,?,?)',
                       (reference, url, digest, 'JP day-grain first release from existing research; no chart metrics imported'))
            histories.release_hints[identity].add((day, locator, reference))


def release_index(db, histories, calendar):
    for identity, row in histories.first.items():
        version, start, end = next((v, a, b) for v, a, b in calendar if a <= row.low <= b)
        hints = histories.release_hints[identity]
        if row.difficulty in ('BAS', 'ADV', 'EXP', 'MAS', 'WE') and row.parent in histories.first:
            parent = histories.first[row.parent]
            if parent.batch == row.batch:
                hints = histories.release_hints[row.parent]
        if (row.difficulty == 'ULT' and version == 'NEW' and row.batch.endswith(':A000')
                and row.music_id in {47, 89, 107, 148, 203}):
            hints = {(start, 'SEGA NEW launch announcement, five enabled ULTIMA records',
                      'https://info-chunithm.sega.jp/1151/')}
        for day, locator, ref in sorted(hints, key=lambda x: (x[0], x[1], x[2] or '')):
            db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)',
                       (identity, day, locator, ref))
        all_days = {day for day, _, _ in hints}
        accepted = {day for day in all_days if row.low <= day <= end}
        day = next(iter(accepted)) if len(accepted) == len(all_days) == 1 else None
        future = next(iter(all_days)) if len(all_days) == 1 and next(iter(all_days)) > end else None
        basis = 'dated_release_evidence' if day else 'disk_presence_future_date_hint' if future else (
            'conflicting_or_out_of_window_release_evidence' if all_days else 'disk_presence_only')
        if basis == 'conflicting_or_out_of_window_release_evidence':
            issue(db, version, row.source, 'release_date_not_adopted', encoded(sorted(all_days)))
        db.execute('INSERT INTO sdhd_first_release VALUES (?,?,?,?,?)',
                   (identity, day or future or row.low, day, day, basis))
        if day:
            histories.known_release[identity] = day
            day_batch = batch(db, f'day:{day}', version, 'public_day', 'Archived JP date evidence', day=day)
            event(db, identity, day_batch, 'released', 'availability', None, 'released', None, row.source,
                  encoded({'evidence': 'sdhd_release_evidence', 'access': 'conditions may apply'}), day=day)


def stage_version(db, histories, root, version, minor, start, end, password):
    hdd = f'SDHD_2.{minor:02}'
    overlay = {}
    active = {}
    active_owners = {}
    traces = defaultdict(list)
    parsed_paths = {}
    with ExitStack() as stack:
        layers = read_layers(db, root, version, minor, start, end, stack, password)
        if not layers or layers[0][0].package != 'A000':
            raise ValueError(f'No readable base for {version}')
        for seq, (layer, revision, files, low) in enumerate(layers):
            key = f'pkg:{hdd}:{layer.package}'
            if db.execute('SELECT 1 FROM history_batches WHERE batch=?', (key,)).fetchone():
                issue(db, version, str(layer.path.relative_to(root)), 'duplicate_package', layer.package)
                continue
            release_version = '.'.join(map(str, revision))
            batch(db, key, version, 'package', encoded({'data_version': release_version,
                  'layer_order': 'data.conf then option code', 'build_on_is_not_effective_on': True}),
                  bounds=(low, end), sequence=seq)
            db.execute('INSERT INTO packages VALUES (?,?,?,?,?,?,?,?)',
                       (hdd, layer.package, layer.path.relative_to(root).as_posix(), release_version,
                        sum(n.endswith('.xml') for n in files), sum(n.endswith('Music.xml') for n in files),
                        'base' if seq == 0 else 'historical_candidate', 'SDHD data.conf'))
            overlay.update((name.casefold(), (file, hdd, layer.package)) for name, file in files.items())
            scan_auxiliary(db, version, layer, files, hdd, key, overlay)
            folders = {name.rpartition('/')[0].casefold() for name in files
                       if name.lower().startswith('music/')}
            for folder in sorted(folders):
                name = folder + '/music.xml'
                if name not in overlay:
                    issue(db, version, key, 'music_patch_without_metadata', folder)
                    continue
                try:
                    record = parse_record(db, version, overlay, name, 50)
                except (ValueError, ExpatError, ElementTree.ParseError, OSError, RuntimeError, BadZipFile) as exc:
                    issue(db, version, overlay[name][0].source, 'music_parse_failure', str(exc))
                    continue
                music_id = record[0].music_id
                if music_id in parsed_paths and parsed_paths[music_id] != folder:
                    issue(db, version, record[0].source, 'duplicate_music_id', parsed_paths[music_id])
                    continue
                parsed_paths[music_id] = folder
                previous = active.get(music_id, ())
                identities = previous if previous and previous[0].source == record[0].source else None
                allowed = previous[0].identity if previous else None
                current = histories.record(version, record, key, low, active_owners, identities=identities)
                if allowed and current[0].identity != allowed:
                    active_owners.pop(allowed)
                active_owners[current[0].identity] = music_id
                active[music_id] = current
                current_ids = {row.identity for row in current}
                for row in previous:
                    if row.identity not in current_ids:
                        traces[row.identity].append(Observation(
                            row.identity, row.music_id, row.kind, row.slot, row.difficulty, row.parent, row.source,
                            row.payload, False, low, key, row.revision))
                for row in current:
                    traces[row.identity].append(row)
    histories.traces[version] = (traces, active)


def write_checkpoint(db, version, row, state):
    data = row.payload
    source = db.execute('SELECT package FROM sources WHERE path=?', (row.source,)).fetchone()[0]
    basis = 'sdhd_overlay_and_release_evidence' if state == 'present' else 'sdhd_overlay'
    db.execute('INSERT INTO history_observations VALUES (?,?,?,?,?,?)',
               (f'end:{version}', row.identity, row.revision, row.source, state, basis))
    db.execute('INSERT INTO sdhd_observed_ids VALUES (?,?,?,?)',
               (f'end:{version}', row.music_id, row.identity, row.source))
    if row.kind == 'song':
        db.execute('''INSERT INTO music_versions
            (version,identity,music_id,title,artist,sort_name,genre,works,release_tag,net_open,disable_flag,
             first_lock,ex_type,cue,jacket_ref,jacket_sha256,source,package,state,basis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (version, row.identity, row.music_id, data['title'], data['artist'], data.get('sortName'),
                    encoded(data.get('genreNames')), label(data, 'worksName'), label(data, 'releaseTagName'),
                    label(data, 'netOpenName'), data.get('disableFlag') == 'true', data.get('firstLock') == 'true',
                    data.get('exType'), label(data, 'cueFileName'), row.resource_ref,
                    data.get('jacket_sha256'), row.source, source, state, basis))
        digest = data.get('jacket_sha256')
        db.execute('INSERT INTO jacket_manifest VALUES (?,?,?,?,?,?,?,?)',
                   (version, row.identity, row.music_id, row.resource_ref, row.source, row.resource_path, digest,
                    'matched' if row.resource_path else 'missing'))
        return
    title, jacket = db.execute('SELECT title,jacket_sha256 FROM music_versions WHERE version=? AND identity=?',
                               (version, row.parent)).fetchone()
    c2s = data.get('c2s_sha256')
    if row.kind == 'we':
        db.execute('''INSERT INTO worlds_end_versions
            (version,identity,chart_identity,music_id,title,slot,enabled,tag,tag_id,star_raw,stars,c2s_ref,c2s_path,c2s_sha256,
             notes_designer,jacket_sha256,source,package,state,basis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (version, row.parent, row.identity, row.music_id, title, row.slot, data['enabled'], data['tag'],
                    data['tag_id'], data['star_raw'], data['stars'], row.resource_ref, row.resource_path, c2s,
                    data['notes_designer'], jacket,
                    row.source, source, state, basis))
    else:
        constant = data.get('hdd_hundredths')
        db.execute('''INSERT INTO chart_versions
            (version,identity,chart_identity,music_id,title,slot,difficulty,enabled,level_raw,decimal_raw,
             hdd_hundredths,selected_hundredths,display_level,c2s_ref,c2s_path,c2s_sha256,notes_designer,source,package,state,
             constant_status,basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (version, row.parent, row.identity, row.music_id, title, row.slot, row.difficulty, data['enabled'],
                    data.get('level_raw'), data.get('decimal_raw'), constant, constant if state == 'present' else None,
                    data.get('display_level'), row.resource_ref, row.resource_path, c2s, data['notes_designer'], row.source, source, state,
                    'hdd_observed' if constant is not None else 'unknown', basis))


def availability_on(changes, day, release):
    public = [kind for on, kind in changes.items() if on <= day]
    if public:
        return {'removed': 'removed', 'restored': 'present',
                'removal_boundary_candidate': 'possible'}[public[-1]]
    return 'present' if release and release <= day else 'possible'


def save_windows(db, version, identity, trace, release, start, end, earliest, changes):
    # Content from later layers is a candidate after its build bound. A missing
    # service date cannot silently change a constant inside the operating period.
    lower = max(start, release or earliest or min(row.low for row in trace))
    if lower > end:
        return
    edges = sorted({lower, end, shift(end, 1)} | {row.low for row in trace if lower < row.low <= end}
                   | {day for day in changes if lower < day <= end})
    for first, stop in zip(edges, edges[1:]):
        candidates = [row for row in trace if row.low <= first]
        if not candidates or not any(row.enabled for row in candidates):
            continue
        availability = availability_on(changes, first, release)
        if availability == 'removed':
            continue
        if any(not row.enabled for row in candidates):
            availability = 'possible'
        options = sorted({row.revision for row in candidates if row.enabled})
        db.execute('INSERT INTO history_windows VALUES (?,?,?,?,?,?,?,?)',
                   (version, identity, first, shift(stop, -1), availability,
                    options[0] if len(options) == 1 else None, encoded(options),
                    'sdhd_activation_dates_partial'))


def finish_timeline(db, histories, calendar, public):
    previous = {
        identity: Observation(identity, row['music_id'], row['kind'], row['slot'], row['difficulty'], row['parent'],
                              row['source'], row['payload'], True, calendar[0][1], 'end:PARADISE LOST', row['revision'])
        for identity, row in histories.old.items() if identity in histories.legacy_present
    }
    present_before = histories.legacy_present.copy()
    earlier = set(histories.old)
    earliest = dict(db.execute('SELECT identity,earliest_on FROM sdhd_first_release'))
    for version, start, end in calendar:
        traces, groups = histories.traces[version]
        batch_id = batch(db, f'end:{version}', version, 'checkpoint',
                         'Last locally held layered content; operational coverage is partial', day=end)
        batch(db, f'day:{start}', version, 'public_day', 'SEGA Japanese rollout date', day=start)
        flat = {row.identity: row for rows in groups.values() for row in rows}
        states = {}
        for identity, row in flat.items():
            release = histories.known_release.get(identity)
            state = 'possible' if row.enabled else 'disabled'
            if row.enabled and earliest.get(identity, '') > end:
                state = 'not_yet_dated'
            if row.enabled and release and release <= end and (identity in present_before or identity not in earlier
                                                                 or start <= release):
                state = 'present'
            changes = public.get(row.parent or identity, {})
            operational = availability_on(changes, end, release)
            if operational == 'removed':
                state = 'removed'
            elif (operational == 'possible' and row.enabled and changes
                  and any(day <= end for day in changes)):
                state = 'possible'
            elif (row.enabled and changes and operational == 'present'
                  and (row.kind == 'song' or release and release <= end)
                  and not (earliest.get(identity, '') > end)):
                state = 'present'
            if row.parent and state == 'present' and states[row.parent] != 'present':
                state = 'possible'
            write_checkpoint(db, version, row, state)
            states[identity] = state
        for identity in sorted(set(previous) - set(flat)):
            row = previous[identity]
            event(db, identity, batch_id, 'missing_from_local_overlay', 'hdd_presence', 'present', 'missing',
                  row.source, None, 'Absence in local overlay alone cannot establish a server removal', bounds=(start, end))
        for identity, trace in traces.items():
            row = trace[-1]
            old = previous.get(identity)
            old_payload = old.payload if old else histories.old.get(identity, {}).get('payload')
            old_source = old.source if old else histories.old.get(identity, {}).get('source')
            if identity not in earlier:
                first = trace[0]
                event(db, identity, first.batch, 'first_hdd_observation', 'hdd_presence', None, 'present',
                      None, first.source, 'Data-layer presence', bounds=(first.low, end))
            elif not old and version != 'NEW':
                event(db, identity, trace[0].batch, 'hdd_reappeared', 'hdd_presence', 'missing', 'present',
                      old_source, trace[0].source, 'Data-layer reappearance', bounds=(trace[0].low, end))
            for item in trace:
                if old is not None and item.enabled != old.enabled:
                    event(db, identity, item.batch, 'hdd_enabled' if item.enabled else 'hdd_disabled',
                          'hdd_enabled', old.enabled, item.enabled, old.source, item.source,
                          'Enable flags and file references in data overlay', bounds=(item.low, end))
                if old_payload is not None:
                    for field in sorted(old_payload.keys() | item.payload.keys()):
                        left, right = old_payload.get(field), item.payload.get(field)
                        if left != right:
                            event(db, identity, item.batch, 'hdd_revision', field, left, right, old_source,
                                  item.source, 'SDHD overlay content change', bounds=(item.low, end))
                old, old_payload, old_source = item, item.payload, item.source
            release = histories.known_release.get(identity)
            if identity in earlier and identity not in present_before and release and release < start:
                release = None
            changes = public.get(row.parent or identity, {})
            # A restored song is evidence for ordinary incumbent charts, but
            # cannot prove contemporaneous release of a separately added ULTIMA.
            if row.kind != 'song' and not release:
                changes = {day: kind for day, kind in changes.items() if kind != 'restored'}
            window_trace = trace
            if identity in present_before and identity in previous and min(row.low for row in trace) > start:
                window_trace = [replace(previous[identity], low=start), *trace]
            save_windows(db, version, identity, window_trace, release, start, end, earliest.get(identity), changes)
        present_before = {key for key, state in states.items() if state == 'present'}
        earlier.update(traces)
        previous = flat


def preserve_paradise_constants(db):
    db.execute("""
        UPDATE chart_versions SET selected_hundredths=hdd_hundredths, constant_status='hdd_inherited'
        WHERE version='PARADISE' AND state='present' AND constant_status IN ('inferred_at_transition','transition_unverified')
        AND hdd_hundredths IS NOT NULL AND EXISTS (
            SELECT 1 FROM chart_versions successor WHERE successor.version='PARADISE LOST'
            AND successor.chart_identity=chart_versions.chart_identity
            AND successor.hdd_hundredths=chart_versions.hdd_hundredths AND successor.state='present'
        )
    """)


def build(args):
    started = perf_counter()
    old = args.old.resolve(strict=True)
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError('Output must be new and outside the HDD')
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(prefix='.build-', dir=output.parent))
    try:
        database = work / 'catalog.sqlite'
        shutil.copyfile(old, database)
        with closing(sqlite3.connect(database)) as db:
            db.row_factory = sqlite3.Row
            if (db.execute('PRAGMA user_version').fetchone()[0] != 2 or
                    db.execute('PRAGMA application_id').fetchone()[0] != 1431193672 or
                    db.execute('SELECT max(ordinal) FROM versions').fetchone()[0] != 12):
                raise ValueError('Expected a v2 SDBT lifecycle database with all twelve editions')
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript(Path(__file__).with_name('sdhd_schema.sql').read_text(encoding='utf-8-sig'))
            preserve_paradise_constants(db)
            histories = Histories(db)
            password = os.getenv('UNIBOT_HDD_ZIP_PASSWORD')
            if args.password_file:
                password = args.password_file.read_text(encoding='utf-8-sig').rstrip('\r\n')
            password = password.encode('utf-8') if password else None
            calendar = []
            for i, ((version, start, url), minor) in enumerate(SDHD):
                if i + 1 < len(SDHD):
                    next_version = SDHD[i + 1][0]
                    end, end_url = shift(next_version[1], -1), next_version[2]
                else:
                    end, end_url = args.through, args.through_url or url
                    if date.fromisoformat(end).isoformat() != end or start > end:
                        raise ValueError('Invalid MATE evidence cutoff')
                db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)',
                           (version, i + 13, f'SDHD_2.{minor:02}', start, end, url, end_url, 'sdhd_partial_operating_history'))
                stage_version(db, histories, root, version, minor, start, end, password)
                calendar.append((version, start, end))
                print(f'{version}: {len(histories.traces[version][1])} local music records', flush=True)
            external_dates(db, histories, args.date_catalog)
            add_ultima_evidence(db, histories, args.ultima_evidence)
            release_index(db, histories, calendar)
            public = read_availability(db, histories, args.availability, calendar)
            finish_timeline(db, histories, calendar, public)
            index_event_changes(db, 'SDHD_*')
            db.commit()
            if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Unified database failed integrity verification')
            summary = report(db)
        summary.update({'bytes': database.stat().st_size, 'sha256': file_hash(database),
                        'seconds': round(perf_counter() - started, 2), 'old_catalog_sha256': file_hash(old)})
        (work / 'report.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
        work.rename(output)
        return summary
    finally:
        if work.exists() and work.resolve().parent == output.parent:
            shutil.rmtree(work)


def report(db):
    def query(sql):
        return [dict(row) for row in db.execute(sql)]

    return {
        'counts': query("""SELECT b.version,e.kind,e.difficulty,o.state,count(*) AS records
            FROM history_observations o JOIN history_entities e USING(identity) JOIN history_batches b USING(batch)
            JOIN versions v USING(version) WHERE b.kind='checkpoint' AND v.ordinal>12
            GROUP BY v.ordinal,e.kind,e.difficulty,o.state ORDER BY v.ordinal,e.kind,e.difficulty,o.state"""),
        'issues': query('SELECT version,kind,count(*) AS records FROM sdhd_issues GROUP BY version,kind'),
        'releases': query("""SELECT kind,difficulty,count(*) AS observed,count(exact_on) AS dated
            FROM sdhd_first_release JOIN history_entities USING(identity) GROUP BY kind,difficulty"""),
        'changes': query("""SELECT e.kind,e.field,count(*) AS records FROM history_events e
            JOIN history_batches b USING(batch) JOIN versions v USING(version)
            WHERE v.ordinal>12 GROUP BY e.kind,e.field ORDER BY e.kind,e.field"""),
        'bridge': query("""SELECT kind,count(DISTINCT identity) AS identities FROM sdhd_observed_ids
            JOIN history_entities USING(identity) WHERE identity NOT LIKE 'sdhd:%' AND identity NOT LIKE '%:sdhd:%'
            GROUP BY kind"""),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--date-catalog', type=Path)
    parser.add_argument('--availability', type=Path)
    parser.add_argument('--ultima-evidence', type=Path)
    parser.add_argument('--through-url')
    parser.add_argument('--password-file', type=Path)
    parser.add_argument('--through', required=True, help='Latest MATE date covered by the local evidence')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
