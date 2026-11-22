"""Publish reviewed HDD image claims on a verified v3 catalog copy."""
from contextlib import closing
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import argparse
import json
import re
import shutil
import sqlite3

from hdd_image_inventory import open_catalog
from hdd_notice_images import compare, dates, decode, folded, possible_days
from old_hdd_scan import encoded
from old_hdd_timeline import batch, event

def normal_launch_witnesses(db, identity, day):
    """Find normal slots with a C2S in the first witnessed launch-era edition."""
    return db.execute('''SELECT * FROM (
        SELECT e.*,o.source,o.revision,p.earliest_on,
               row_number() OVER (PARTITION BY e.identity ORDER BY v.ordinal,b.sequence,o.source) AS witness_rank
        FROM history_entities e
        JOIN history_observations o USING(identity) JOIN history_revisions r USING(revision)
        JOIN history_batches b USING(batch) JOIN versions v USING(version)
        LEFT JOIN sdhd_first_release p USING(identity)
        WHERE e.parent=? AND e.difficulty IN ('BAS','ADV','EXP','MAS') AND b.kind='package'
          AND v.end_on>=? AND o.state IN ('hdd_enabled','present')
          AND v.ordinal=(
              SELECT min(pv.ordinal) FROM history_observations po
              JOIN history_batches pb USING(batch) JOIN versions pv USING(version)
              WHERE po.identity=e.parent AND pb.kind='package' AND pv.end_on>=?
                AND po.state IN ('hdd_enabled','present'))
          AND json_extract(r.payload,'$.c2s_sha256') IS NOT NULL
    ) WHERE witness_rank=1 ORDER BY slot,identity''', (identity, day, day)).fetchall()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS hdd_notice_review (
    asset TEXT REFERENCES history_assets, location TEXT, announcement TEXT,
    identity TEXT REFERENCES history_entities, day TEXT, mode TEXT, status TEXT,
    evidence TEXT NOT NULL, PRIMARY KEY(asset,location,announcement,identity,day)
) WITHOUT ROWID;
'''


def matches(db, package, music_id, difficulty):
    return db.execute('''SELECT DISTINCT e.*,o.source,o.revision,r.payload,rp.exact_on,rp.earliest_on
        FROM sdhd_observed_ids i JOIN history_entities e USING(identity)
        JOIN history_observations o ON o.identity=i.identity AND o.batch=i.batch AND o.source=i.source
        JOIN history_revisions r USING(revision) LEFT JOIN sdhd_first_release rp USING(identity)
        WHERE i.batch=? AND i.music_id=? AND e.difficulty IS ? AND o.state='hdd_enabled' ''',
                      (package, music_id, difficulty)).fetchall()


def materialize(db, row, day, edition, claim, *, inherited=False):
    identity = row['identity']
    known = {r[0] for r in db.execute('''SELECT exact_on FROM sdhd_first_release WHERE identity=? AND exact_on IS NOT NULL
        UNION SELECT effective_on FROM history_events WHERE identity=? AND kind='released' AND effective_on IS NOT NULL''',
                                    (identity, identity))}
    if known - {day}:
        return 'conflict_existing_exact'
    if not inherited and (row['earliest_on'] is None or row['earliest_on'] > day):
        return 'outside_first_release_bounds'
    if known:
        return 'corroborated'
    db.execute('UPDATE sdhd_first_release SET earliest_on=?,latest_on=?,exact_on=?,basis=? WHERE identity=?',
               (day, day, day, 'reviewed_hdd_notice_with_sega', identity))
    db.execute('INSERT OR IGNORE INTO sdhd_release_evidence VALUES (?,?,?,?)',
               (identity, day, claim['location'], claim['announcement']))
    day_batch = batch(db, f'day:{day}', edition['version'], 'public_day', 'Reviewed HDD notice and SEGA announcement', day=day)
    event(db, identity, day_batch, 'released', 'availability', None, 'released', None, row['source'],
          encoded(claim), day=day)
    # Restrict changes to the launch edition and keep revision ambiguity and
    # availability conditions on existing windows.
    db.execute('DELETE FROM history_windows WHERE version=? AND identity=? AND end_on<?',
               (edition['version'], identity, day))
    db.execute('UPDATE history_windows SET start_on=? WHERE version=? AND identity=? AND start_on<?',
               (day, edition['version'], identity, day))
    return 'adopted'


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def compact(text):
    return re.sub(r'\s+', '', folded(text))


def source_path(root, locator):
    item = Path(locator)
    if item.anchor or '..' in item.parts:
        raise ValueError('Invalid source locator')
    path = (root / item).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('Source escapes its input root')
    return path


def verified_artifacts(claim, root, official):
    address = urlsplit(claim['announcement'])
    html = source_path(official, address.path.strip('/') + '.html').read_bytes()
    if sha256(html).hexdigest() != claim['announcement_sha256']:
        raise ValueError('Archived announcement digest mismatch')
    article = ArticleText()
    article.feed(html.decode('utf-8'))
    text = compact(' '.join(article.parts))
    effective = date.fromisoformat(claim['day'])
    published = claim['published'].replace('-', '.')
    if published not in text or f'{effective.year}年{effective.month}月{effective.day}日' not in text:
        raise ValueError('Announcement date not present in archive')
    for target in claim['targets']:
        title = compact(target['title'])
        if title not in text or (target.get('difficulty') and not re.search(
                re.escape(title) + r'.{0,28}' + re.escape(compact(target['difficulty_label'])), text)):
            raise ValueError(f'Announcement does not name target and difficulty: {target["title"]}')
    location = claim['location']
    marker = location.lower().find('.zip!/')
    path = source_path(root, location[:marker+4] if marker >= 0 else location)
    if marker >= 0:
        member = location[marker+6:]
        with ZipFile(path) as archive:
            members = [entry for entry in archive.infolist() if entry.filename == member]
            if len(members) != 1 or members[0].file_size > 64 * 1024 * 1024:
                raise ValueError('Ambiguous or oversized HDD witness')
            raw = archive.read(members[0])
    else:
        raw = path.read_bytes()
    if sha256(raw).hexdigest() != claim['sha256']:
        raise ValueError('Original HDD notice digest mismatch')

    if media := claim.get('official_image'):
        url = urlsplit(media['url'])
        if (url.scheme != 'https' or url.netloc != address.netloc
                or not url.path.startswith('/wp-content/uploads/') or media['url'] not in html.decode('utf-8')):
            raise ValueError('Official image is not referenced by the archived article')
        archived = source_path(official, url.path.rsplit('/', 1)[1]).read_bytes()
        if sha256(archived).hexdigest() != media['official_sha256']:
            raise ValueError('Archived official image digest mismatch')
        score = compare(decode(raw)[1], decode(archived)[1])['center_crop_mean_error']
        if score > .025:
            raise ValueError('Reviewed official image no longer matches the HDD frame')


def apply_reviews(db, reviews, root, official):
    if not reviews:
        raise ValueError('At least one reviewed claim is required')
    db.executescript(SCHEMA)
    applied = []
    with db:
        for claim in reviews:
            day = date.fromisoformat(claim['day']).isoformat()
            address = urlsplit(claim['announcement'])
            if (address.scheme != 'https' or address.netloc != 'info-chunithm.sega.jp'
                    or not re.fullmatch(r'/\d+/', address.path)
                    or not re.fullmatch(r'[a-f0-9]{64}', claim['announcement_sha256'])
                    or date.fromisoformat(claim['published']) > date.fromisoformat(day)
                    or claim['mode'] not in {'standard', 'event', 'map'}):
                raise ValueError('Invalid review provenance')
            edition = db.execute('''SELECT v.* FROM history_batches b JOIN versions v USING(version)
                WHERE b.batch=? AND v.ordinal>12 AND ? BETWEEN v.start_on AND v.end_on''',
                                 (claim['batch'], day)).fetchone()
            printed = next(dates(claim['printed']), None)
            if (not edition or not printed or day not in possible_days(printed, [
                    (date.fromisoformat(edition['start_on']), date.fromisoformat(edition['end_on']))])):
                raise ValueError(f'Start date and edition do not match the transcription: {claim["location"]}')
            verified_artifacts(claim, root, official)
            image = db.execute('''SELECT asset FROM history_asset_links WHERE batch=? AND location=? AND source=?
                AND relation='event_information_image' AND asset=?''',
                               (claim['batch'], claim['location'], claim['event_xml'], claim['sha256'])).fetchone()
            if not image:
                raise ValueError(f'Unknown HDD image witness: {claim["location"]}')
            for target in claim['targets']:
                music_id, difficulty = target['music_id'], target.get('difficulty')
                rows = matches(db, claim['batch'], music_id, difficulty)
                songs = matches(db, claim['batch'], music_id, None)
                if len(rows) != 1 or len(songs) != 1 or folded(json.loads(songs[0]['payload'])['title']) != folded(target['title']):
                    raise ValueError(f'Unresolved HDD song/chart for {music_id}/{difficulty}')
                row = rows[0]
                if difficulty and row['parent'] != songs[0]['identity']:
                    raise ValueError(f'Wrong parent for {music_id}/{difficulty}')
                children = []
                if difficulty is None and json.loads(row['payload']).get('exType') == '0':
                    candidates = normal_launch_witnesses(db, row['identity'], day)
                    children = [chart for chart in candidates
                                if sum(c['difficulty'] == chart['difficulty'] for c in candidates) == 1]
                evidence = {key: value for key, value in claim.items() if key != 'targets'} | {'target': target}
                primary_status = materialize(db, row, day, edition, evidence)
                for witness in [row, *children]:
                    status = (primary_status if witness == row else
                              materialize(db, witness, day, edition, evidence, inherited=True)
                              if primary_status in {'adopted', 'corroborated'} else 'parent_unresolved')
                    db.execute('INSERT OR IGNORE INTO hdd_notice_review VALUES (?,?,?,?,?,?,?,?)',
                               (claim['sha256'], claim['location'], claim['announcement'], witness['identity'],
                                day, claim['mode'], status, encoded(evidence)))
                    applied.append({'identity': witness['identity'], 'music_id': music_id,
                                    'difficulty': witness['difficulty'], 'day': day, 'status': status})
    return applied


def publish(catalog, output, claims, root, official):
    output = output.resolve()
    if output.is_relative_to(root) or output.is_relative_to(official):
        raise ValueError('Output must be outside original evidence directories')
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='hdd-image-build-', dir=output.parent) as directory:
        staged = Path(directory) / 'catalog.sqlite'
        with closing(open_catalog(catalog)) as source, closing(sqlite3.connect(staged)) as db:
            if (source.execute('PRAGMA application_id').fetchone()[0] != 1431193672
                    or source.execute('PRAGMA user_version').fetchone()[0] != 3
                    or not source.execute('SELECT ready FROM complete WHERE ready=1').fetchone()):
                raise ValueError('Expected a complete unified HDD v3 catalog')
            source.backup(db)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            applied = apply_reviews(db, claims, root, official)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Catalog verification failed')
        # Hard link safely publishes a complete file and fails if another run won the destination.
        output.hardlink_to(staged)
    return applied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('reviews', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--new-root', type=Path, required=True)
    parser.add_argument('--official-dir', type=Path, required=True)
    args = parser.parse_args()
    reviews = json.loads(args.reviews.read_text(encoding='utf-8'))['reviews']
    print(json.dumps(publish(args.catalog, args.output, reviews, args.new_root.resolve(), args.official_dir.resolve()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
