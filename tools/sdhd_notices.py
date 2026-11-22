"""Resolve reviewed official availability claims against observed SDHD identities."""
from collections import defaultdict
from datetime import date
import json
import re
from urllib.parse import urlsplit

from old_hdd_evidence import normalize_name
from old_hdd_scan import encoded
from old_hdd_timeline import batch, event

KINDS = {'first', 'general_open', 'restored', 'removed', 'limited_end', 'access_change', 'revision'}
MODES = {'standard', 'course', 'map', 'serial', 'event', 'ticket', 'gate', 'unknown'}
OFFICIAL_HOSTS = {'info-chunithm.sega.jp', 'chunithm.sega.jp', 'www.sega.jp', 'x.com', 'twitter.com'}


def verified_day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f'Expected ISO calendar date: {value}')
    return value


def add_official_notices(db, histories, calendar, path):
    if not path:
        return {}, {}, {}, None
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    db.executescript("""
        CREATE TABLE sdhd_notice (
            url TEXT PRIMARY KEY, published_on TEXT NOT NULL, title TEXT NOT NULL,
            channel TEXT NOT NULL, sha256 TEXT NOT NULL, related TEXT NOT NULL,
            effective_on TEXT
        );
        CREATE TABLE sdhd_notice_claim (
            announcement TEXT NOT NULL REFERENCES sdhd_notice(url),
            identity TEXT NOT NULL REFERENCES history_entities,
            effective_on TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL,
            detail TEXT NOT NULL, image TEXT, image_sha256 TEXT, locator TEXT NOT NULL,
            package TEXT NOT NULL REFERENCES history_batches(batch),
            source TEXT NOT NULL REFERENCES sources(path), revision TEXT NOT NULL REFERENCES history_revisions,
            field TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(announcement,identity,effective_on,kind,mode,field)
        );
        CREATE INDEX sdhd_notice_identity ON sdhd_notice_claim(identity,effective_on);
        CREATE INDEX sdhd_notice_day ON sdhd_notice_claim(effective_on,kind);
    """)
    first = {}
    public = defaultdict(dict)
    changes = {}
    observations = defaultdict(list)
    for version, (traces, _) in histories.traces.items():
        for identity, trace in traces.items():
            observations[identity].extend((version, row) for row in trace)
    for notice in data['notices']:
        url = notice['url']
        domain = urlsplit(url)
        if domain.scheme != 'https' or domain.hostname not in OFFICIAL_HOSTS:
            raise ValueError(f'Unsupported official notice URL: {url}')
        if domain.hostname in ('x.com', 'twitter.com') and not re.fullmatch(r'/chunithm/status/\d+', domain.path):
            raise ValueError(f'Unexpected official X account: {url}')
        published = verified_day(notice['published'])
        if not re.fullmatch(r'[0-9a-f]{64}', notice['sha256']):
            raise ValueError(f'Missing announcement digest: {url}')
        explicit_day = notice.get('effective')
        db.execute('INSERT INTO sdhd_notice VALUES (?,?,?,?,?,?,?)',
                   (url, published, notice['title'], notice['channel'], notice['sha256'],
                    encoded(notice.get('related', [])), verified_day(explicit_day) if explicit_day else None))
        for claim in notice['claims']:
            day = verified_day(claim['day'])
            version = next((v for v, lo, hi in calendar if lo <= day <= hi), None)
            kind, mode = claim['kind'], claim['mode']
            if not version or kind not in KINDS or mode not in MODES:
                raise ValueError(f'Unsupported announcement claim: {claim}')
            identity = claim['identity']
            witness = next((r for v, r in observations[identity] if r.batch == claim['batch'] and r.enabled), None)
            if witness is None:
                raise ValueError(f'Missing real HDD witness: {url} / {identity} / {claim["batch"]}')
            song = witness if witness.kind == 'song' else next(
                (r for v, r in observations[witness.parent] if r.batch == witness.batch), None)
            if (song is None or normalize_name(song.payload['title']) != normalize_name(claim['title'])
                    or witness.music_id != claim['music_id'] or witness.difficulty != claim.get('difficulty')):
                raise ValueError(f'Unexpected witness identity/title/difficulty: {url} / {identity}')
            field = claim.get('field', '')
            image = claim.get('image')
            if image and not re.fullmatch(r'[0-9a-f]{64}', claim.get('image_sha256', '')):
                raise ValueError(f'Missing announcement image digest: {url}')
            image_source = claim.get('hdd_image_event')
            if image_source and not db.execute('''SELECT 1 FROM history_asset_links
                    WHERE source=? AND location=? AND asset=? AND batch=? AND relation='event_information_image' ''',
                    (image_source, image, claim.get('image_sha256'), witness.batch)).fetchone():
                raise ValueError(f'Unverified HDD notice image: {image_source}')
            db.execute('INSERT OR IGNORE INTO sdhd_notice_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (url, identity, day, kind, mode, claim['detail'], image, claim.get('image_sha256'),
                        claim['locator'], witness.batch, witness.source, witness.revision, field))
            day_batch = batch(db, f'day:{day}', version, 'public_day', 'Official Japanese notice', day=day)
            if kind == 'first' and identity in histories.first:
                previous = first.get(identity)
                if previous and previous[0] != day:
                    raise ValueError(f'Conflicting official first dates: {identity}')
                first[identity] = (day, url, claim['locator'])
            elif kind == 'revision':
                if not field or 'before' not in claim or witness.payload.get(field) != claim.get('after'):
                    raise ValueError(f'Unverified revision value: {url} / {identity} / {field}')
                key = (identity, witness.batch, field)
                if key in changes and changes[key][0] != day:
                    raise ValueError(f'Conflicting revision dates: {key}')
                changes[key] = (day, url, claim['before'])
            else:
                if kind in ('restored', 'removed') and mode in ('standard', 'unknown'):
                    current = public[identity].get(day)
                    if current and current != kind:
                        raise ValueError(f'Conflicting public availability: {identity} / {day}')
                    public[identity][day] = kind
                if kind == 'limited_end':
                    # End of one announced route leaves other routes unresolved.
                    public[identity].setdefault(day, 'removal_boundary_candidate')
                    if witness.kind == 'we':
                        public[witness.parent].setdefault(day, 'removal_boundary_candidate')
                exists = db.execute('SELECT 1 FROM history_events WHERE identity=? AND kind=? AND effective_on=?',
                                    (identity, kind, day)).fetchone()
                if not exists:
                    event(db, identity, day_batch, kind, 'access', None, mode, None, witness.source,
                          encoded({'url': url, 'locator': claim['locator'], 'detail': claim['detail']}), day=day)
    designer = 'https://info-chunithm.sega.jp/6357/'
    if not db.execute('SELECT 1 FROM sdhd_notice WHERE url=?', (designer,)).fetchone():
        designer = None
    return first, changes, public, designer


def designer_revision(db, announcement, item, field, before, after):
    """Date only the exact display-label transformation in the LUMINOUS notice."""
    if (not announcement or field != 'notes_designer' or item.batch != 'pkg:SDHD_2.20:A000'
            or not isinstance(before, str) or not before.startswith('【Ex】')
            or after != before.removeprefix('【Ex】').lstrip()):
        return None
    day = '2023-12-14'
    db.execute('INSERT INTO sdhd_notice_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (announcement, item.identity, day, 'revision', 'standard', '公告明确删除谱师名的【Ex】前缀',
                None, None, '■ExTAPノーツについて', item.batch, item.source, item.revision, field))
    return day, announcement


def merge_availability(db, archived, official):
    for identity, changes in official.items():
        previous = archived.setdefault(identity, {})
        for day, kind in changes.items():
            # One archive treats a maintenance-day removal as playable through
            # that day. Keep the rejected assertion with both source dates.
            rivals = [other for other, what in previous.items()
                      if what == kind and abs((date.fromisoformat(other) - date.fromisoformat(day)).days) == 1]
            for other in rivals:
                db.execute("""UPDATE history_events SET kind='superseded_availability_candidate',effective_on=NULL,
                              earliest_on=NULL,latest_on=NULL,evidence=evidence||?
                              WHERE identity=? AND kind=? AND effective_on=?""",
                           (f' | rejected historical date {other}; official date {day}', identity, kind, other))
                del previous[other]
        previous.update(changes)
        archived[identity] = dict(sorted(previous.items()))
