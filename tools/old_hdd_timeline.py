"""Build release evidence, content revisions and per-date uncertainty windows."""
from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256
from itertools import combinations
from pathlib import PurePosixPath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from old_hdd_evidence import ROOT, normalize_name
from old_hdd_scan import STARS, display_level, encoded, integer, parse_music, safe_ref

ONE_DAY = timedelta(days=1)


def shift(day, days):
    return (date.fromisoformat(day) + days * ONE_DAY).isoformat()


def batch(db, key, version, kind, evidence, *, day=None, bounds=(None, None), sequence=None):
    first, last = (day, day) if day else bounds
    timing = 'exact' if day else 'range' if first and last else 'batch_only' if sequence is not None else 'unknown'
    db.execute('INSERT OR IGNORE INTO history_batches VALUES (?,?,?,?,?,?,?,?,?)',
               (key, version, kind, day, first, last, timing, sequence, evidence))
    return key


def event(db, identity, batch_id, kind, field, before, after, old, new, evidence, *, day=None, bounds=(None, None)):
    first, last = (day, day) if day else bounds
    db.execute('''INSERT INTO history_events
                  (identity,batch,kind,field,before,after,old_source,new_source,earliest_on,latest_on,effective_on,evidence)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
               (identity, batch_id, kind, field, encoded(before), encoded(after), old, new, first, last, day, evidence))


def payloads(song, jacket, sections):
    # Physical paths and XML exporter schema versions are retained in sources.
    fields = {key: value for key, value in song.fields.items()
              if key not in ('dataName', 'formatVersion', 'resourceVersion')}
    yield None, fields | {'title': song.title, 'artist': song.artist, 'jacket_sha256': jacket}
    for chart in song.charts:
        if chart.difficulty == 'unknown' or (chart.difficulty == 'WE') != (song.ex_type == 2):
            continue
        if not chart.enabled and not chart.ref and chart.level in (None, '0'):
            continue
        notation, creator = sections.get(chart.digest, (None, None))
        data = {'enabled': chart.enabled, 'c2s_sha256': chart.digest, 'notes_designer': chart.designer or creator,
                'designer_source': 'music_xml' if chart.designer else 'c2s_header' if creator else None,
                'notation_sha256': notation}
        if song.ex_type == 2:
            data |= {'tag': song.tag, 'tag_id': song.tag_id, 'stars': STARS.get(song.star), 'star_raw': song.star}
        else:
            data |= {'hdd_hundredths': chart.constant, 'display_level': display_level(chart.constant),
                     'level_raw': chart.level, 'decimal_raw': chart.decimal}
        yield chart, data


def identity_matches(song, previous):
    same_title = normalize_name(song.title) == normalize_name(previous.title)
    artist = bool(song.artist and previous.artist and normalize_name(song.artist) == normalize_name(previous.artist))
    cue = bool(song.cue_ref and song.cue_ref == previous.cue_ref)
    shared = bool({chart.digest for chart in song.charts if chart.enabled and chart.digest} &
                  {chart.digest for chart in previous.charts if chart.enabled and chart.digest})
    jacket = bool(song.jacket_hash and song.jacket_hash == previous.jacket_hash)
    return (same_title and (artist or cue or shared)) or (shared and (artist or (cue and jacket)))


def source_entries(db):
    from build_old_hdd_history import chart_presence, base_state, represented

    sources = {row['path'].casefold(): (row['path'], row['sha256']) for row in db.execute(
        "SELECT path,sha256 FROM sources WHERE kind IN ('image','c2s') AND sha256 IS NOT NULL")}
    endpoints = defaultdict(list)
    jackets = dict(db.execute('SELECT source,sha256 FROM jacket_manifest ORDER BY version'))
    versions = [dict(row) for row in db.execute('SELECT * FROM versions ORDER BY ordinal')]
    sections = {digest: (notation, creator) for digest, notation, creator in db.execute(
        'SELECT sha256,notation_sha256,creator FROM c2s_sections')}
    by_hdd = defaultdict(list)
    for version in versions:
        by_hdd[version['hdd']].append(version['version'])
    packages = {(r['hdd'], r['package']): r for r in db.execute('SELECT * FROM packages')}
    for table in ('music_versions', 'chart_versions', 'worlds_end_versions'):
        for row in db.execute(f'SELECT * FROM {table} ORDER BY version,source'):
            endpoints[row['source']].append(dict(row))
    songs = {}
    for row in db.execute('''SELECT s.path,s.hdd,s.package,s.sha256,x.xml FROM raw_song_index i
                             JOIN sources s ON s.path=i.source JOIN raw_xml x ON x.path=s.path ORDER BY s.path'''):
        path, hdd, package, digest, xml = row
        song = parse_music(xml, path, package, digest)
        parent = PurePosixPath(path).parent
        for obj, ref in [(song, song.jacket_ref), *[(chart, chart.ref) for chart in song.charts]]:
            relative = safe_ref(parent, ref) if ref else None
            actual, value = sources.get((relative or '').casefold(), (None, None))
            if obj is song:
                song.jacket_path, song.jacket_hash = actual, value
            else:
                obj.path, obj.digest = actual, value
        songs[path] = (hdd, song)

    known = defaultdict(list)
    for path, rows in endpoints.items():
        for row in rows:
            if 'chart_identity' not in row:
                song = songs[path][1]
                known[song.music_id, song.ex_type].append((row['identity'], song))

    for path, (hdd, song) in songs.items():
        package = packages[hdd, song.package]
        official_candidate = package['classification'] in ('base', 'historical_candidate')
        if song.ex_type not in (0, 2) or not official_candidate:
            continue
        identity = next((row['identity'] for row in endpoints[path] if 'chart_identity' not in row), None)
        group = song.music_id, song.ex_type
        if identity is None:
            matches = {key for key, previous in known[group] if identity_matches(song, previous)}
            if len(matches) == 1:
                identity = matches.pop()
            else:
                identity = f"{song.ex_type}:{song.music_id}:raw:{sha256(path.encode()).hexdigest()[:16]}"
            known[group].append((identity, song))
        jacket = jackets.get(path, song.jacket_hash)
        for chart, payload in payloads(song, jacket, sections):
            if chart is not None and sum(c.slot == chart.slot and represented(c) for c in song.charts) != 1:
                continue
            kind = 'song' if chart is None else 'we' if song.ex_type == 2 else 'chart'
            cid = identity if chart is None else f'{identity}:{chart.slot}:{chart.difficulty}'
            db.execute('INSERT OR IGNORE INTO history_entities VALUES (?,?,?,?,?,?)',
                       (cid, kind, identity if chart else None, song.music_id,
                        chart.slot if chart else None, chart.difficulty if chart else None))
            text = encoded(payload)
            revision = sha256(encoded([cid, payload]).encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO history_revisions VALUES (?,?,?)', (revision, cid, text))
            selected = [r for r in endpoints[path] if r.get('chart_identity', r['identity']) == cid]
            state = base_state(song, early=hdd in ('SDBT_1.01','SDBT_1.06') and 'disableFlag' not in song.fields)
            raw_state = chart_presence(state, chart) if chart else state
            record = {'identity': cid, 'revision': revision, 'source': path, 'raw_state': raw_state,
                      'payload': payload, 'song': song, 'kind': kind, 'parent': identity if chart else None,
                      'versions': by_hdd[hdd], 'selected': selected, 'package_batch': f'pkg:{hdd}:{song.package}'}
            yield record


def package_order(db, versions):
    for disk, names in versions.items():
        rows = list(db.execute('SELECT * FROM packages WHERE hdd=? ORDER BY package', (disk,)))
        accepted = [row for row in rows if row['classification'] in ('base','historical_candidate')]
        # When data.conf has a tie, compare full music payloads for strict
        # cumulative inclusion. Only then give the tied packages an order.
        signatures = defaultdict(dict)
        for name, *values in db.execute('''SELECT s.package,i.music_id,i.source,s.sha256
                                          FROM raw_song_index i JOIN sources s ON s.path=i.source
                                          WHERE s.hdd=? ORDER BY i.source''', (disk,)):
            mid, path, digest = values
            signatures[name].setdefault(mid, []).append(digest)
        groups = defaultdict(list)
        for row in accepted:
            if row['package'] != 'A000' and row['data_version']:
                groups[tuple(map(int, row['data_version'].split('.')))].append(row['package'])
        sequence = {'A000': 0}
        index = 0
        for rev in sorted(groups):
            members = sorted(groups[rev], key=lambda key: (len(signatures[key]), key))
            music = [m for m in members if signatures[m]]
            cumulative = all(signatures[a].items() < signatures[b].items()
                             for a, b in zip(music, music[1:]))
            for name in (members if len(members) == 1 else music if cumulative else []):
                index += 1
                sequence[name] = index
        for row in rows:
            name = row['package']
            edition = names[0] if len(names) == 1 or name == 'A000' else None
            batch(db, f'pkg:{disk}:{name}', edition, 'package',
                  encoded({'path': row['path'], 'data_version': row['data_version'],
                           'classification': row['classification'], 'basis': row['evidence']}),
                  sequence=sequence.get(name))


def public_dates(records, public, title_owners, versions):
    row = records[0]
    keys = set()
    for record in records:
        song = record['song']
        keys.add((normalize_name(song.title), normalize_name(song.tag or '') if song.ex_type == 2 else '',
                  STARS.get(song.star) if song.ex_type == 2 else None))
    releases, removals, restores, boundaries = {}, {}, {}, {}
    for key in sorted(keys, key=str):
        owners = title_owners[key]
        for edition, loc, day, artist in public.dated_rows.get(key, ()):
            if edition not in versions or not versions[edition]['start_on'] <= day <= versions[edition]['end_on']:
                continue
            artist_token = normalize_name(artist).replace(' ', '')
            artist_owners = {key for key, names in owners.items() if artist_token and artist_token in names}
            identity = row['parent'] or row['identity']
            if set(owners) == {identity} or artist_owners == {identity}:
                releases[day] = loc
        if len(owners) == 1:
            removals.update(public.removed.get(key, ()))
            restores.update(public.restored.get(key, ()))
            boundaries.update(public.boundary_removed.get(key, ()))
    return releases, removals, restores, boundaries


def difference(db, previous, current, batch_id, basis, bounds):
    if previous is None or current is None:
        return
    old, new = previous['payload'], current['payload']
    for field in sorted(old.keys() | new.keys()):
        before, after = old.get(field), new.get(field)
        if before == after:
            continue
        event(db, current['identity'], batch_id, 'revision', field, before, after,
              previous['source'], current['source'], basis, bounds=bounds)


def build_windows(db, records, public, title_owners, epochs, sequences):
    identity, kind = records[0]['identity'], records[0]['kind']
    versions = {v['version']: v for v in epochs}
    checkpoints = {row['version']: (record, row) for record in records for row in record['selected']}
    physically = defaultdict(list)
    for record in records:
        for edition in record['versions']:
            physically[edition].append(record)
    dated, removed, restored, boundaries = public_dates(records, public, title_owners, versions)
    observed_present = {edition for edition, (_, row) in checkpoints.items() if row['state'] == 'present'}
    # A date list proves the row's difficulty was listed. Chart identity must
    # also have usable C2S in its first playable edition (or in a raw-only case).
    eligible_editions = observed_present or {
        version for version, values in physically.items() if any(item['raw_state'] == 'present' for item in values)}
    first_observed = next((v['version'] for v in epochs if v['version'] in eligible_editions), None)
    signals = defaultdict(dict)
    if first_observed and len(dated) == 1:
        day, loc = next(iter(dated.items()))
        current = versions[first_observed]
        if current['start_on'] <= day <= current['end_on']:
            signals[day]['released'] = loc
    first_possible = versions[first_observed]['start_on'] if first_observed else None

    def applies(day):
        if not first_possible or day < first_possible:
            return False
        if kind != 'chart':
            return True
        return any(span['start_on'] < day and span['version'] in eligible_editions for span in epochs)

    for day, loc in sorted(removed.items()):
        if applies(day):
            signals[day]['removed'] = loc
    for day, loc in sorted(restored.items()):
        if applies(day):
            signals[day]['restored'] = loc
    for day, actions in signals.items():
        if 'released' in actions and any(other < day and 'restored' in values for other, values in signals.items()):
            actions['release_after_reported_restoration'] = actions['released']
    exact = {day: next(iter(actions.items())) if len(actions) == 1 else
             ('availability_conflict', encoded(actions)) for day, actions in signals.items()}
    first_release = next((d for d, (k, _) in exact.items() if k == 'released'), None)
    last_record = None
    prior_state = 'not_observed'
    last_playable = False
    public_state = None
    for span in epochs:
        edition, start, end = span['version'], span['start_on'], span['end_on']
        selected, anchor = checkpoints.get(edition, (None, {}))
        state = anchor.get('state', 'not_observed')
        potential = physically[edition]
        if not selected and not last_record and not potential:
            continue
        checkpoint = f'end:{edition}'
        before = last_record
        day_events = {day: data for day, data in exact.items() if start <= day <= end}
        for day, (change, source) in sorted(day_events.items()):
            key = batch(db, f'day:{day}', edition, 'public_day', 'Dated public release/removal/restoration evidence', day=day)
            event(db, identity, key, change, 'availability',
                  {'removed':'present','released':'unavailable','restored':'unavailable'}.get(change),
                  {'removed':'unavailable','released':'present','restored':'present'}.get(change),
                  before['source'] if before else None, selected['source'] if selected else None, source, day=day)
        if start in boundaries and applies(start) and start not in day_events:
            event(db, identity, checkpoint, 'removal_boundary_candidate', 'availability', 'present', 'unavailable',
                  before['source'] if before else None, selected['source'] if selected else None,
                  boundaries[start], bounds=(start,end))
        if state == 'present' and prior_state != 'present' and not any(
                change in ('released','restored') for change, _ in day_events.values()):
            action = 'restored_candidate' if prior_state in ('removed','disabled') else (
                'reobserved' if last_playable else 'first_observed')
            event(db, identity, checkpoint, action, 'availability', prior_state, state,
                  before['source'] if before else None, selected['source'], anchor['basis'], bounds=(start,end))
        if prior_state == 'present' and state != 'present' and not any(
                change == 'removed' for change, _ in day_events.values()):
            event(db, identity, checkpoint, 'disabled_candidate' if state == 'disabled' else 'observation_gap',
                  'availability', prior_state, state, before['source'], selected['source'] if selected else None,
                  anchor.get('basis', 'absence from retained endpoint'), bounds=(start,end))

        # Ordered OPTION chains contribute their own deltas. Equal-rank or
        # unclassified alternatives remain in history_observations.
        revisions = {}
        for record in potential:
            key = record['package_batch']
            if sequences[key] is not None and edition == record['versions'][0]:
                revisions.setdefault(key, []).append(record)
        prior = before
        for key in sorted(revisions, key=sequences.__getitem__):
            candidates = revisions[key]
            if len({candidate['revision'] for candidate in candidates}) != 1:
                continue
            current = candidates[0]
            if prior is None:
                event(db, identity, key, 'file_observed', 'revision', None, current['revision'], None,
                      current['source'], 'first retained file in ordered package chain')
            difference(db, prior, current, key, 'HDD payload difference; public activation unverified', (start,end))
            prior = current
        if selected:
            difference(db, prior, selected, checkpoint, 'HDD endpoint difference; effective day unverified', (start,end))

        cuts = sorted({start, end, shift(end, 1), *day_events, *(shift(day, 1) for day in day_events)})
        dated_presence = False
        for begin, after in zip(cuts, cuts[1:]):
            if begin in day_events:
                public_state = {'removed':'unavailable','released':'present','restored':'present'}.get(day_events[begin][0])
                dated_presence = public_state == 'present'
            day_conflict = begin in day_events and day_events[begin][0] == 'availability_conflict'
            if begin == end:
                availability = {'present':'present','removed':'unavailable','disabled':'unavailable',
                                'not_released':'unavailable'}.get(state, 'possible')
                if not selected and public_state == 'unavailable':
                    availability = 'unavailable'
                basis = anchor.get('basis','retained endpoint has no record')
                candidates = [selected['revision']] if selected else []
                if day_conflict or (public_state == 'unavailable' and state == 'present'):
                    availability, basis = 'possible', 'conflicting dated evidence and endpoint'
            else:
                if first_release and begin < first_release:
                    availability = 'unavailable'
                elif day_conflict:
                    availability = 'possible'
                elif public_state == 'unavailable':
                    availability = 'possible' if state == 'present' and begin not in day_events else 'unavailable'
                elif begin in day_events:
                    availability = 'present'
                elif (dated_presence and state == 'present') or prior_state == state == 'present':
                    availability = 'inferred_present'
                else:
                    availability = 'possible'
                basis = 'dated evidence and HDD coverage' if public_state or first_release else 'HDD checkpoint continuity'
                candidates = sorted({item['revision'] for item in potential
                                     if item['raw_state'] == 'present' or item is selected}
                                    | ({before['revision']} if before and prior_state == 'present' else set()))
                if selected and selected['revision'] not in candidates and state == 'present':
                    candidates.append(selected['revision'])
            if availability != 'unavailable':
                db.execute('INSERT INTO history_windows VALUES (?,?,?,?,?,?,?,?)',
                           (edition, identity, begin, shift(after,-1), availability,
                            candidates[0] if len(candidates) == 1 else None, encoded(candidates), basis))
        if selected:
            last_record = selected
        prior_state = state
        if state == 'present':
            last_playable = True
            if public_state == 'unavailable':
                public_state = None


def build_timeline(db, evidence, evidence_path):
    db.executescript((ROOT / 'tools/old_hdd_timeline.sql').read_text(encoding='utf-8'))
    epochs = [dict(row) for row in db.execute('SELECT * FROM versions ORDER BY ordinal')]
    versions = defaultdict(list)
    for span in epochs:
        name = span['version']
        versions[span['hdd']].append(name)
        batch(db, f'end:{name}', name, 'checkpoint', span['basis'], bounds=(span['start_on'], span['end_on']))
        batch(db, f"day:{span['start_on']}", name, 'public_day', span['start_url'], day=span['start_on'])
    package_order(db, versions)
    sequences = dict(db.execute('SELECT batch,sequence FROM history_batches'))
    entities = defaultdict(list)
    title_owners = defaultdict(lambda: defaultdict(set))
    for record in source_entries(db):
        identity, path = record['identity'], record['source']
        entities[identity].append(record)
        song = record['song']
        tag = normalize_name(song.tag or '') if song.ex_type == 2 else ''
        key = normalize_name(song.title), tag, STARS.get(song.star) if song.ex_type == 2 else None
        title_owners[key][record['parent'] or identity].add(normalize_name(song.artist or '').replace(' ', ''))
        db.execute('INSERT INTO history_observations VALUES (?,?,?,?,?,?)',
                   (record['package_batch'], identity, record['revision'], path, record['raw_state'], 'HDD file observation'))
        for row in record['selected']:
            db.execute('INSERT INTO history_observations VALUES (?,?,?,?,?,?)',
                       (f"end:{row['version']}", identity, record['revision'], path, row['state'], row['basis']))
    for records in entities.values():
        build_windows(db, records, evidence, title_owners, epochs, sequences)
    index_identity_links(db, entities)
    index_late_option_changes(db)
    index_assets(db, evidence_path)
    index_event_changes(db)


def index_identity_links(db, entities):
    groups = defaultdict(dict)
    by_raw_id = defaultdict(dict)
    for identity, rows in entities.items():
        first = rows[0]
        if first['kind'] == 'song':
            song = first['song']
            by_raw_id[song.ex_type, song.music_id][identity] = first['source']
            continue
        for row in rows:
            digest = row['payload']['c2s_sha256']
            if digest:
                groups[first['kind'], digest].setdefault(identity, row)

    def link(left, right, kind, a, b, basis):
        db.execute('INSERT OR IGNORE INTO history_identity_links VALUES (?,?,?,?,?,?)', (left, right, kind,a,b,basis))

    for members in by_raw_id.values():
        for (left, a), (right, b) in combinations(sorted(members.items()), 2):
            link(left, right, 'same_raw_id_distinct', a,b, 'Matching signals did not establish continuity')
    for members in groups.values():
        for (left, a), (right, b) in combinations(sorted(members.items()), 2):
            if a['song'].music_id == b['song'].music_id or not identity_matches(a['song'], b['song']):
                continue
            link(left, right, 'possible_cross_id_chart', a['source'], b['source'],
                 'Shared C2S and corroborating song evidence; distinct archive identities retained')
    for row in db.execute("""SELECT identity,origin_identity,source,origin_basis FROM music_versions
                             WHERE origin_identity IS NOT NULL ORDER BY version,identity"""):
        identity, origin, source, basis = row
        if identity in entities and origin in entities:
            link(identity, origin, 'worlds_end_origin', source,entities[origin][0]['source'],basis)


def index_event_changes(db):
    rows = db.execute('''
        SELECT e.*,s.hdd,s.package,p.data_version FROM history_game_events e
        JOIN sources s ON s.path=e.source JOIN packages p USING(hdd,package)
        WHERE e.event_id IS NOT NULL AND p.classification IN ('base','historical_candidate')
        ORDER BY s.hdd,s.package,e.source
    ''')
    layers = defaultdict(lambda: defaultdict(list))
    targets = defaultdict(list)
    for source, role, target, _ in db.execute('SELECT * FROM history_game_event_links ORDER BY source,role,target_id'):
        targets[source].append((role,target))
    for row in rows:
        rev = row['data_version']
        rank = (0,) if row['package'] == 'A000' else (1,*map(int, rev.split('.'))) if rev else None
        if rank:
            layers[row['hdd'],row['event_id']][rank].append(row)
    fields = ('title', 'type', 'always_open', 'music_type', 'banner_id', 'targets')
    for (hdd,event_id), revisions in layers.items():
        before = old_source = None
        for _, candidates in sorted(revisions.items()):
            variants = {tuple(row[field] for field in fields[:-1]) + (tuple(targets[row['source']]),)
                        for row in candidates}
            if len(variants) != 1:
                before = old_source = None
                continue
            after = variants.pop()
            if before is None:
                before = (None,) * len(fields)
            for row in candidates:
                for field, old, new in zip(fields,before,after,strict=True):
                    if old != new:
                        db.execute('INSERT INTO history_game_event_changes VALUES (?,?,?,?,?,?,?)',
                                   (f"pkg:{hdd}:{row['package']}", event_id, field, encoded(old), encoded(new),
                                    old_source, row['source']))
            before, old_source = after, candidates[0]['source']


def index_late_option_changes(db):
    # A032/A033 have no data.conf. Persist the actual differences with
    # locators, independently of uncertain precedence and activation.
    for row in db.execute("""SELECT * FROM option_changes
                             WHERE hdd='SDBT_1.50' AND before_source IS NOT NULL ORDER BY rowid"""):
        scope, _, field = row['field'].partition('.')
        key = f"pkg:{row['hdd']}:{row['after_package']}"
        cid = db.execute('''SELECT o.identity FROM history_observations o JOIN history_entities e USING(identity)
                             WHERE o.batch=? AND o.source=? AND (?='song' AND e.kind='song'
                               OR e.difficulty=? OR CAST(e.slot AS TEXT)=?) ORDER BY o.identity''',
                         (key, row['after_source'], scope, scope, scope)).fetchall()
        if len(cid) != 1:
            continue
        db.execute('''INSERT INTO history_events
                      (identity,batch,kind,field,before,after,old_source,new_source,earliest_on,latest_on,effective_on,evidence)
                      VALUES (?,?, 'option_variant',?,?,?,?,?,?,?,?,?)''',
                   (cid[0][0], key, field, row['before'], row['after'], row['before_source'], row['after_source'],
                    row['effective_on'], row['effective_on'], row['effective_on'], row['evidence']))

def index_assets(db, evidence_path):
    banner_paths = defaultdict(list)
    lookup = {row[0].casefold(): row for row in db.execute(
        "SELECT path,sha256,hdd,package FROM sources WHERE kind='image' AND sha256 IS NOT NULL")}

    def image(xml_path, reference):
        candidate = safe_ref(PurePosixPath(xml_path).parent, reference) if reference else None
        return lookup.get((candidate or '').casefold())

    def link(asset, kind, location, relation, source, batch_id=None, day=None, title=None):
        db.execute('INSERT OR IGNORE INTO history_assets VALUES (?,?)', (asset, kind))
        db.execute('INSERT OR IGNORE INTO history_asset_links VALUES (?,?,?,?,?,?,?)',
                   (asset, location, relation, source, batch_id, day, title))

    for path, raw, hdd, package in db.execute("""SELECT x.path,x.xml,s.hdd,s.package FROM raw_xml x
                          JOIN sources s USING(path) WHERE path LIKE '%/DDSBanner.xml' ORDER BY path"""):
        node = ElementTree.fromstring(raw)
        file = image(path, node.findtext('ddsFile/path'))
        if file:
            location, digest, _, _ = file
            idx = integer(node.findtext('name/id'))
            label = node.findtext('name/str')
            banner_paths[hdd, idx].append((package, location, digest))
            link(digest, 'hdd_game_banner', location, 'banner_definition', path, f'pkg:{hdd}:{package}', title=label)
    for path, raw, hdd, package in db.execute("""SELECT x.path,x.xml,s.hdd,s.package FROM raw_xml x
                          JOIN sources s USING(path) WHERE path LIKE '%/Event.xml' ORDER BY path"""):
        node = ElementTree.fromstring(raw)
        event_id = integer(node.findtext('name/id'))
        title = node.findtext('name/str')
        type_id = node.findtext('substances/type')
        banner_id = integer(node.findtext('ddsBannerName/id'))
        db.execute('INSERT INTO history_game_events VALUES (?,?,?,?,?,?,?)',
                   (path, event_id, title, type_id, node.findtext('alwaysOpen') == 'true',
                    node.findtext('substances/music/musicType'), banner_id))
        for role, xpath in (('music', 'music/musicNames/list/*'), ('map_music', 'map/musicNames/list/*'),
                            ('recommend_music', 'recommendMusic/musicNames/list/*'),
                            ('map', 'map/mapName'), ('course', 'information/courseNames/list/*'),
                            ('movie', 'information/movieName')):
            for ref in node.findall('substances/' + xpath):
                number = integer(ref.findtext('id'))
                if number is not None and number >= 0:
                    db.execute('INSERT OR IGNORE INTO history_game_event_links VALUES (?,?,?,?)',
                               (path, role, number, ref.findtext('str')))
        if type_id == '1':
            file = image(path, node.findtext('substances/information/image/path'))
            if file:
                location, digest, _, _ = file
                link(digest, 'hdd_game_notice', location, 'event_information_image', path,
                     f'pkg:{hdd}:{package}', title=title)
        if banner_id is not None and banner_id >= 0:
            matches = banner_paths[hdd, banner_id]
            local = [item for item in matches if item[0] == package]
            # Prefer an explicit same-package definition. Across packages only
            # resolve when all retained definitions have the same bytes.
            matches = local or matches
            if matches and len({item[2] for item in matches}) == 1:
                _, location, digest = matches[0]
                link(digest, 'hdd_game_banner', location, 'event_banner', path, f'pkg:{hdd}:{package}', title=title)

    archive = evidence_path / 'sega-pop-index.html' if evidence_path else None
    if not archive or not archive.is_file():
        return
    from bs4 import BeautifulSoup

    raw = archive.read_bytes()
    listing = 'https://am.sega.jp/dlpop/dlpop_cat/chunithm'
    db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
               ('sega-pop-index', listing, sha256(raw).hexdigest(), 'SEGA operator publicity archive'))
    for anchor in BeautifulSoup(raw, 'html.parser').find_all('a', href=True):
        label = anchor.get_text(' ', strip=True)
        stamp = re.match(r'(202[01])\.(\d{2})\.(\d{2})', label)
        if not stamp or not re.search(r'CHUNITHM (?:CRYSTAL PLUS|PARADISE)', label):
            continue
        published = date(*map(int, stamp.groups())).isoformat()
        page = urljoin(listing, anchor['href'])
        parts = urlsplit(page)
        if parts.netloc != 'am.sega.jp' or not re.fullmatch(r'/dlpop/chunithm/\d+/?', parts.path):
            continue
        page_id = parts.path.rstrip('/').rsplit('/',1)[1]
        path = evidence_path / 'sega-pop' / (page_id + '.html')
        if not path.is_file():
            continue
        data = path.read_bytes()
        db.execute('INSERT OR IGNORE INTO references_used VALUES (?,?,?,?)',
                   ('sega-pop-' + page_id, page, sha256(data).hexdigest(), label))
        for pop in BeautifulSoup(data, 'html.parser').find_all('a', href=True):
            url = urlsplit(urljoin(page, pop['href']))
            if url.netloc != 'am.sega.jp' or not url.path.lower().endswith('.pdf') or '/uploads/' not in url.path:
                continue
            parts = re.sub('/+', '/', url.path)
            location = urlunsplit(('https', url.netloc, parts, '', ''))
            asset = 'url:' + sha256(location.encode()).hexdigest()
            # A POP date printed in the source filename and corroborated by a
            # dated update is a link candidate. Publication dates stay separate.
            dd = re.search(r'chunithm_(20|21)(\d{2})(\d{2})', parts, re.I)
            key = None
            if dd:
                try:
                    day = date(2000 + int(dd[1]), int(dd[2]), int(dd[3])).isoformat()
                except ValueError:
                    day = None
                if day and db.execute('SELECT 1 FROM history_batches WHERE batch=?', (f'day:{day}',)).fetchone():
                    key = f'day:{day}'
            link(asset, 'official_publicity', location, 'filename_date_matches_update' if key else 'official_listing',
                 page, key, published, label)
