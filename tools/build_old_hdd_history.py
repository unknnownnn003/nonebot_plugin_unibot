"""Build an offline JP old-cabinet history audit from extracted HDD directories."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, timedelta
from itertools import combinations
import json
from pathlib import Path
import sqlite3
import tempfile

from old_hdd_evidence import PublicEvidence, ROOT, normalize_name
from history_calendar import JP_RELEASES
from old_hdd_scan import HDD_NAMES, STARS, discover, display_level, encoded, log_conflict


def version_rows():
    for i, (version, start, url) in enumerate(JP_RELEASES[:12]):
        _, following, boundary_url = JP_RELEASES[i + 1]
        end = (date.fromisoformat(following) - timedelta(days=1)).isoformat()
        basis = 'partial_historical_reconstruction' if i >= 10 else 'observed_hdd_endpoint'
        yield version, i + 1, HDD_NAMES[min(i, 10)], start, end, url, boundary_url, basis


def layer_index(db, hdd, packs):
    indexed = {}
    for name, rows in sorted(packs.items()):
        index = defaultdict(list)
        for song in rows:
            index[song.music_id].append(song)
        for duplicates in index.values():
            if len(duplicates) > 1:
                for song in duplicates:
                    log_conflict(db, hdd, song, None, 'duplicate_id_in_package', name)
        indexed[name] = index
    for (left, first), (right, second) in combinations(indexed.items(), 2):
        shared = first.keys() & second.keys()
        if not shared:
            continue
        # Compute equality using all revisions, including duplicates.
        equal = sum(sorted(x.digest for x in first[key]) == sorted(x.digest for x in second[key]) for key in shared)
        relation = 'identical_shared_xml' if equal == len(shared) else 'different_shared_xml'
        db.execute('INSERT INTO option_pairs VALUES (?,?,?,?,?,?,?,?)',
                   (hdd, left, right, len(shared), equal, len(first.keys() - second.keys()),
                    len(second.keys() - first.keys()), relation))
    return indexed


def group_candidates(indexed, accepted):
    groups = defaultdict(list)
    for name in accepted:
        for key, variants in indexed.get(name, {}).items():
            groups[key].extend(variants)
    return groups


def diff_fields(left, right, keys):
    for key in keys:
        before = getattr(left, key)
        after = getattr(right, key)
        if before != after:
            yield key, before, after


def represented(chart):
    return chart.enabled is not False or bool(chart.ref) or chart.level not in (None, '0')


def source_diff(left, right):
    for key in sorted(left.fields.keys() | right.fields.keys()):
        before, after = left.fields.get(key), right.fields.get(key)
        if before != after:
            yield 'song', key, before, after
    for field, before, after in diff_fields(left, right, ('jacket_hash',)):
        yield 'song', field, before, after
    old = {c.slot: c for c in left.charts if represented(c)}
    new = {c.slot: c for c in right.charts if represented(c)}
    for slot in sorted(old.keys() | new.keys()):
        a, b = old.get(slot), new.get(slot)
        if a is None or b is None:
            yield str(slot), 'slot_presence', a is not None, b is not None
            continue
        for field, before, after in diff_fields(a, b, ('enabled', 'constant', 'digest', 'designer', 'ref')):
            yield b.difficulty, field, before, after


def option_event(db, hdd, before, after, kind, field, a, b, day, evidence):
    db.execute('INSERT INTO option_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (hdd, before.package if before else None, after.package, after.music_id, after.title, kind,
                field, encoded(a), encoded(b), before.source if before else None, after.source, day, evidence))


def game_revision(info, name):
    version = info[name][0]
    return tuple(map(int, version.split('.'))) if version else ()


def options_earlier(db, hdd, indexed, info):
    groups = group_candidates(indexed, [name for name in info if info[name][1] in ('base', 'historical_candidate')])
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda song: (game_revision(info, song.package), song.source))
        strongest = game_revision(info, rows[-1].package)
        terminal = [x for x in rows if game_revision(info, x.package) == strongest]
        payloads = {(x.digest, x.jacket_hash, tuple(c.digest for c in x.charts)) for x in terminal}
        if len(payloads) > 1:
            for row in terminal:
                log_conflict(db, hdd, row, None, 'package_precedence_unknown', encoded([x.source for x in terminal]))
            yield rows[-1], 'conflict', 'equal_data_revision_conflict', rows
        else:
            choice = terminal[-1]
            early = hdd in HDD_NAMES[:2] and 'disableFlag' not in choice.fields
            basis = 'early_schema_without_disableFlag' if early else 'observed_versioned_option_union'
            yield choice, base_state(choice, early=early), basis, rows
    active = {}
    release_groups = defaultdict(list)
    for name in indexed:
        rev = game_revision(info, name)
        if rev:
            release_groups[rev].extend(row for variants in indexed[name].values() for row in variants)
    for revision, rows in sorted(release_groups.items()):
        same_version = defaultdict(list)
        for row in rows:
            same_version[row.music_id].append(row)
        for key, candidates in sorted(same_version.items()):
            if len({(x.digest, x.jacket_hash, tuple(c.digest for c in x.charts)) for x in candidates}) != 1:
                active.pop(key, None)
                continue
            row = sorted(candidates, key=lambda x: x.source)[-1]
            previous = active.get(key)
            if previous is None and revision[-1]:
                option_event(db, hdd, None, row, 'first_observed', None, None, None, None,
                             'first available directory revision ' + '.'.join(map(str, revision)))
            elif previous is not None:
                for difficulty, field, a, b in source_diff(previous, row):
                    option_event(db, hdd, previous, row, 'observed_diff', f'{difficulty}.{field}', a, b, None,
                                 'ordered data.conf revisions, public effective date unknown')
            active[key] = row


def base_state(song, *, early=False):
    if song.ex_type == 1:
        return 'tutorial'
    if song.ex_type not in (0, 2) or (song.disabled is None and not early):
        return 'unknown'
    return 'disabled' if song.disabled else 'present'


def paradise_options(db, indexed, evidence):
    hdd = HDD_NAMES[-1]
    groups = group_candidates(indexed, ['A000', 'A032', 'A033'])
    result = {'PARADISE': [], 'PARADISE LOST': []}
    for key, rows in sorted(groups.items()):
        candidates = {row.package: row for row in rows}
        first = candidates.get('A000')
        aug = candidates.get('A032')
        later = candidates.get('A033')
        reference = aug or first or later
        title = normalize_name(reference.title)
        tag = normalize_name(reference.tag or '') if reference.ex_type == 2 else ''
        removal, removal_ref = evidence.removal(title, tag, STARS.get(reference.star) if tag else None)
        known_release, release_ref, release_date = evidence.first_release(title, tag, STARS.get(reference.star) if tag else None)
        reviewed = evidence.reviewed_release(key, [row.source for row in rows])
        if reviewed:
            release_date = reviewed['on']
            release_ref = evidence.reviewed_sources[reviewed['reference']]
            known_release = 'PARADISE' if release_date <= '2021-05-12' else 'PARADISE LOST'
        release_proof = f'{release_ref} {release_date}' if release_ref and release_date else release_ref
        reference_basis = [x for x in (removal_ref, release_proof, evidence.max_titles.get(title) if not tag else None) if x]
        conflicting_identity = any(normalize_name(row.title) != title or row.ex_type != reference.ex_type
                                   for row in rows) or len(candidates) != len(rows)
        if conflicting_identity:
            log_conflict(db, hdd, reference, None, 'option_identity_conflict', encoded([x.source for x in rows]))
        if first and aug and not conflicting_identity:
            for diff, field, before, after in source_diff(first, aug):
                day = removal if field == 'disableFlag' and removal == '2021-08-05' else None
                option_event(db, hdd, first, aug, 'summer_observed_diff', f'{diff}.{field}', before, after, day,
                             removal_ref if day else 'A000/A032 retained variants; exact activation day unknown')
        if aug and first is None and release_date and not conflicting_identity:
            option_event(db, hdd, None, aug, 'public_release_observed_in_A032', None, None, None,
                         release_date, release_ref)
        if later and not conflicting_identity:
            older = aug or first
            if older:
                for diff, field, before, after in source_diff(older, later):
                    day = removal if field == 'disableFlag' and removal else None
                    option_event(db, hdd, older, later, 'later_observed_diff', f'{diff}.{field}', before, after, day,
                                 removal_ref if day else 'A033 retained alternate; exact activation day unknown')
        for version in result:
            paradise = version == 'PARADISE'
            day = '2021-05-12' if paradise else '2021-11-03'
            choice = reference
            basis = 'observed_A000' if first else 'observed_A032'
            state = base_state(choice)
            if conflicting_identity:
                state, basis = 'conflict', 'option_identity_conflict'
            elif state == 'tutorial':
                basis = 'exType_1_tutorial'
            elif removal and day >= removal:
                state, basis = 'removed', 'dated_wiki_removal'
            elif paradise:
                if known_release == 'PARADISE LOST' or (
                        reference.ex_type == 0 and reference.release == 'v1 1.55.00'):
                    state, basis = 'not_released', 'lost_release_evidence'
                elif removal and day < removal and (first or known_release == 'PARADISE'
                                                     or removal == '2021-05-13'):
                    choice, state, basis = first or aug or later, 'present', 'before_documented_removal'
                elif first:
                    choice, state = first, base_state(first)
                elif known_release == 'PARADISE':
                    choice, state, basis = aug or later, 'present', 'public_paradise_membership_later_xml'
                elif reference.ex_type == 0 and reference.release == 'v1 1.50.00':
                    state, basis = 'unknown', 'paradise_release_tag_only'
                else:
                    state, basis = 'unknown', 'paradise_membership_unverified'
            elif removal and day < removal:
                choice, state, basis = first or aug or later, 'present', 'before_documented_removal'
            elif release_date and day < release_date:
                state, basis = 'not_released', 'dated_wiki_release'
            elif tag and not first and not release_ref:
                state, basis = 'unknown', 'we_release_date_unverified'
            elif aug and aug.disabled:
                state, basis = 'unknown', 'unverified_A032_disable'
            elif later and later.disabled and not aug and not removal and not first:
                state, basis = 'unknown', 'unverified_A033_disable'
            elif aug or first:
                choice = aug or first
                state, basis = base_state(choice), 'observed_A032' if aug else 'observed_A000'
            else:
                choice, state, basis = later, 'unknown', 'A033_only_cutoff_unverified'
            if not conflicting_identity and not paradise and removal == '2021-11-04' and later and later.disabled:
                choice = aug or first or later
                if choice.disabled and choice is not later:
                    log_conflict(db, version, choice, None, 'date_vs_disable_flag', removal_ref, later.source)
                state, basis = 'present', 'last_playable_day_wiki'
            if choice.release:
                reference_basis.append(f'{choice.source}:releaseTagName={choice.release}')
            reason = encoded(sorted(set(reference_basis + [x.source for x in rows])))
            db.execute('INSERT INTO paradise_split_evidence VALUES (?,?,?,?,?,?,?,?,?)',
                       (version, key, choice.title, 'WE' if choice.ex_type == 2 else None,
                        state, basis, choice.source, reason, state in ('unknown', 'conflict')))
            result[version].append((choice, state, basis, rows))
    for name in ('A114', 'A910', 'A999'):
        for key, songs in sorted(indexed.get(name, {}).items()):
            for song in songs:
                db.execute('INSERT INTO paradise_split_evidence VALUES (?,?,?,?,?,?,?,?,?)',
                           ('1.50 raw', key, song.title, 'WE' if song.ex_type == 2 else None, 'excluded', name,
                            song.source, 'Non-production / unverified restoration layer; raw XML preserved', 0))
    return result


class IdentityIndex:
    def __init__(self):
        self.revisions = defaultdict(list)

    def song(self, db, version, song):
        group = (song.music_id, song.ex_type)
        matches = []
        for identity, previous in self.revisions[group]:
            title = normalize_name(song.title) == normalize_name(previous.title)
            artist = bool(song.artist and previous.artist and
                          normalize_name(song.artist) == normalize_name(previous.artist))
            cue = bool(song.cue_ref and song.cue_ref == previous.cue_ref)
            shared_chart = bool({c.digest for c in song.charts if c.enabled and c.digest} &
                                {c.digest for c in previous.charts if c.enabled and c.digest})
            same_jacket = bool(song.jacket_hash and song.jacket_hash == previous.jacket_hash)
            if (title and (artist or cue or shared_chart)) or (shared_chart and (artist or (cue and same_jacket))):
                matches.append(identity)
        if len(matches) == 1:
            return matches[0]
        identity = f'{song.ex_type}:{song.music_id}:{len(self.revisions[group]) + 1}'
        if self.revisions[group]:
            log_conflict(db, version, song, None, 'cross_edition_identity_conflict',
                         encoded([i for i, _ in self.revisions[group]]))
            db.execute('INSERT INTO unmatched_records VALUES (?,?,?,?,?,?,?)',
                       (version, song.music_id, song.title, None, song.source, 'possible_id_reuse',
                        encoded([i for i, _ in self.revisions[group]])))
        self.revisions[group].append((identity, song))
        return identity


def match_origin(song, songs):
    matches = [identity for identity, other in songs if song.cue_ref and other.cue_ref == song.cue_ref
               and (normalize_name(song.title) == normalize_name(other.title) or
                    (song.jacket_hash and song.jacket_hash == other.jacket_hash))]
    return matches[0] if len(matches) == 1 else None


def write_edition(db, version, rows, public, identities, pinned_scores):
    identity_rows = [(identities.song(db, version, song), song, state, basis) for song, state, basis, _ in rows]
    ordinary = [(identity, song) for identity, song, state, _ in identity_rows
                if song.ex_type == 0 and state in ('present', 'removed', 'disabled')]
    names = Counter(normalize_name(song.title) for _, song in ordinary)
    for identity, song, state, basis in identity_rows:
        origin = match_origin(song, ordinary) if song.ex_type == 2 else None
        db.execute('INSERT INTO music_versions VALUES (' + ','.join('?' for _ in range(24)) + ')',
                   (version, identity, song.music_id, song.directory, song.title, song.artist,
                    song.fields.get('sortName'), encoded(song.fields.get('genreNames')),
                    encoded(song.fields.get('worksName')), encoded(song.fields.get('rightsInfoName')),
                    song.release, song.open_tag, song.disabled, str(song.fields.get('firstLock', '')).lower() in ('true','1'),
                    song.ex_type, encoded(song.fields.get('cueFileName')), song.jacket_ref, song.jacket_hash,
                    song.source, song.package, state, basis, origin, 'same_cue_and_title_or_jacket' if origin else None))
        db.execute('INSERT INTO jacket_manifest VALUES (?,?,?,?,?,?,?,?)',
                   (version, identity, song.music_id, song.jacket_ref, song.source,
                    song.jacket_path, song.jacket_hash, song.jacket_status))
        if song.jacket_status != 'matched':
            log_conflict(db, version, song, None, 'jacket_reference', song.jacket_status)
        if song.ex_type not in (0, 2):
            continue
        charts = [c for c in song.charts if represented(c)]
        slots = Counter(c.slot for c in charts)
        for chart in charts:
            if chart.enabled is False and chart.difficulty == 'WE' and song.ex_type != 2:
                continue
            if slots[chart.slot] != 1 or chart.difficulty == 'unknown' or (chart.difficulty == 'WE') != (song.ex_type == 2):
                log_conflict(db, version, song, chart.difficulty, 'chart_slot_anomaly', str(chart.slot))
                continue
            chart_state = chart_presence(state, chart)
            cid = f'{identity}:{chart.slot}:{chart.difficulty}'
            if song.ex_type == 2:
                write_we(db, version, identity, cid, song, chart, chart_state, basis, public)
            else:
                write_ordinary(db, version, identity, cid, song, chart, chart_state, basis,
                               public, names, pinned_scores)


def chart_presence(state, chart):
    if state != 'present':
        return state
    if chart.enabled is False:
        return 'disabled'
    if chart.enabled is None or not chart.digest:
        return 'unknown'
    return 'present'


def write_we(db, version, identity, cid, song, chart, state, basis, public):
    stars = STARS.get(song.star)
    rows = public.we.get((normalize_name(song.title), normalize_name(song.tag or '')), ())
    distinct = {value for value, _ in rows}
    for wiki, reference in rows:
        result = 'same' if wiki == stars else 'ambiguous_variant' if len(distinct) > 1 else 'different'
        db.execute('INSERT INTO wiki_we_checks VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (version, song.music_id, song.title, song.tag, song.star, stars, reference.split(':')[0],
                    reference, wiki, result))
    if stars is None or not song.tag or song.tag in ('Invalid', 'なし'):
        log_conflict(db, version, song, 'WE', 'we_encoding_unknown', encoded([song.tag, song.star]))
    if distinct and stars not in distinct:
        log_conflict(db, version, song, 'WE', 'wiki_we_different', encoded(rows))
    db.execute('INSERT INTO worlds_end_versions VALUES (' + ','.join('?' for _ in range(20)) + ')',
               (version, identity, cid, song.music_id, song.title, chart.slot, chart.enabled, song.tag, song.tag_id,
                song.star, stars, chart.ref, chart.path, chart.digest, chart.designer, song.jacket_hash,
                song.source, song.package, state, basis))


def write_ordinary(db, version, identity, cid, song, chart, state, basis, public, names, pinned):
    raw = chart.constant
    selected = raw if state == 'present' else None
    status = 'hdd_observed' if selected is not None else 'unknown' if state in ('present', 'unknown') else state
    title = normalize_name(song.title)
    if version == 'PARADISE' and state == 'present' and song.package != 'A000':
        selected = raw if public.transition else None
        status = 'inferred_at_transition' if selected is not None else 'transition_unverified'
    display = display_level(raw)
    for level, locator in public.levels.get((version, title, chart.difficulty), ()):
        db.execute('INSERT INTO wiki_level_checks VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (version, song.music_id, song.title, chart.difficulty, raw, display, level,
                    locator.split(':')[0], locator, 'same' if display == level else 'different'))
        if display != level:
            log_conflict(db, version, song, chart.difficulty, 'wiki_display_disagreement', f'{display} vs {level}', locator)
    if raw is None and state == 'present':
        log_conflict(db, version, song, chart.difficulty, 'invalid_or_unsupported_constant', encoded([chart.level, chart.decimal]))
    db.execute('INSERT INTO chart_versions VALUES (' + ','.join('?' for _ in range(23)) + ')',
               (version, identity, cid, song.music_id, song.title, chart.slot, chart.difficulty, chart.enabled,
                chart.level, chart.decimal, raw, selected, display, chart.ref, chart.path, chart.digest,
                chart.designer, song.source, song.package, state, status, basis, None))
    entry = pinned.get(title)
    if version == 'PARADISE LOST' and names[title] == 1 and entry and chart.slot in range(4):
        digest = entry['identities'][chart.slot]
        if digest:
            db.execute('INSERT INTO score_mapping VALUES (?,?,?,?,?,?,?)',
                       (version, cid, entry['lxns_id'], chart.slot, digest, 'pinned_candidate',
                        'Match via existing legacy_rating title and slot; revalidate live CN chart fingerprint before use'))
    if version == 'PARADISE' and (song.package != 'A000' or state in ('unknown', 'removed', 'conflict')):
        db.execute('INSERT INTO paradise_split_evidence VALUES (?,?,?,?,?,?,?,?,?)',
                   (version, song.music_id, song.title, chart.difficulty, state, status, song.source,
                    encoded({'membership': basis, 'HDD_hundredths': raw, 'selected_hundredths': selected,
                             'transition': 'wiki-lost' if public.transition else None}),
                    status in ('unknown', 'conflict', 'inferred_at_transition', 'transition_unverified')))


def build(args):
    root = args.root.resolve(strict=True)
    missing = [name for name in HDD_NAMES if not (root / name / 'data/A000/music').is_dir()]
    if missing:
        raise FileNotFoundError('Missing base music directories: ' + ', '.join(missing))
    output = args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError('Output must be outside the source HDD tree')
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}-', dir=output.parent))
    with closing(sqlite3.connect(stage / 'catalog.sqlite')) as db:
        db.row_factory = sqlite3.Row
        db.executescript((ROOT / 'tools/old_hdd_schema.sql').read_text(encoding='utf-8-sig'))
        db.executemany('INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)', version_rows())
        public = PublicEvidence(args.evidence, db)
        scores = (ROOT / 'nonebot_plugin_unibot/assets/historical/paradise_lost.json').read_bytes()
        from hashlib import sha256
        db.execute('INSERT INTO references_used VALUES (?,?,?,?)',
                   ('legacy-score-map', 'local:nonebot_plugin_unibot/assets/historical/paradise_lost.json',
                    sha256(scores).hexdigest(), 'Current pinned CN mapping, candidate only'))
        pinned = {normalize_name(row['title']): row for row in json.loads(scores)['songs'] if 'lxns_id' in row}
        identities = IdentityIndex()
        for version, _, hdd, *_ in version_rows():
            if version == 'PARADISE LOST':
                continue
            print(f'{version} / {hdd}', flush=True)
            packs, info = discover(db, root, hdd, hash_all=args.hash_all)
            index = layer_index(db, hdd, packs)
            if version == 'PARADISE':
                versions = paradise_options(db, index, public)
            else:
                versions = {version: list(options_earlier(db, hdd, index, info))}
            for edition, rows in versions.items():
                write_edition(db, edition, rows, public, identities, pinned)
            db.commit()
        from old_hdd_export import changes, export
        from old_hdd_timeline import build_timeline

        build_timeline(db, public, args.evidence)
        changes(db)
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Old HDD catalog failed database integrity checks')
        if db.execute('SELECT count(DISTINCT version) FROM music_versions').fetchone()[0] != 12:
            raise ValueError('An edition has no parsed music records')
        db.execute('INSERT INTO complete VALUES (1)')
        db.commit()
        export(db, stage, audit_csv=getattr(args, 'audit_csv', False))
    if output.exists():
        raise FileExistsError(output)
    stage.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New output directory outside the source tree')
    parser.add_argument('--evidence', type=Path, help='Private directory with saved public Wiki and MAX HTML')
    parser.add_argument('--audit-csv', action='store_true', help='Export legacy audit tables as separate CSV files')
    parser.add_argument('--hash-all', action='store_true', help='Hash all package files including unreferenced binary assets')
    args = parser.parse_args()
    build(args)


if __name__ == '__main__':
    main()
