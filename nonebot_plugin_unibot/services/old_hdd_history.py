"""Read-only access to the separately built JP HDD lifecycle catalog."""
from contextlib import closing
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
from zipfile import ZipFile


APPLICATION_ID = 1431193672
SCHEMA_VERSION = 2


class OldHddHistory:
    """Explicit database path. No LocalStore or command activation occurs."""

    def __init__(self, database):
        self.database = Path(database).resolve(strict=True)
        with closing(self._connect()) as db:
            self.schema_version = db.execute('PRAGMA user_version').fetchone()[0]
            if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or self.schema_version not in (1, SCHEMA_VERSION, 3)):
                raise ValueError('Unsupported JP HDD catalog')
            if not db.execute('SELECT ready FROM complete WHERE ready=1').fetchone():
                raise ValueError('Incomplete old HDD catalog')

    def _connect(self):
        db = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        return db

    def _rows(self, query, args=()):
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(query, args)]

    def versions(self):
        return self._rows('SELECT * FROM versions ORDER BY ordinal')

    def _version(self, version):
        if not self._rows('SELECT 1 FROM versions WHERE version = ?', (version,)):
            raise KeyError(version)

    def songs(self, version, *, active_only=True):
        self._version(version)
        where = " AND state = 'present'" if active_only else ''
        return self._rows('SELECT * FROM music_versions WHERE version = ?' + where + ' ORDER BY music_id, identity',
                          (version,))

    def song_history(self, music_id):
        if self.schema_version >= 3:
            return self._rows('''SELECT m.* FROM music_versions m JOIN versions USING(version)
                                 WHERE m.identity IN (SELECT identity FROM history_entities WHERE music_id=?
                                     UNION SELECT identity FROM sdhd_observed_ids WHERE music_id=?)
                                 ORDER BY ordinal,m.identity''', (music_id, music_id))
        return self._rows('''SELECT m.* FROM music_versions m JOIN versions USING(version)
                             WHERE music_id = ? ORDER BY ordinal, identity''', (music_id,))

    def charts(self, version, *, active_only=True):
        self._version(version)
        where = " AND state = 'present'" if active_only else ''
        return self._rows('SELECT * FROM chart_versions WHERE version = ?' + where + ' ORDER BY music_id, slot, chart_identity',
                          (version,))

    def worlds_end(self, version, *, active_only=True):
        self._version(version)
        where = " AND state = 'present'" if active_only else ''
        return self._rows('SELECT * FROM worlds_end_versions WHERE version = ?' + where + ' ORDER BY music_id, slot, chart_identity',
                          (version,))

    def chart_history(self, chart_identity):
        return self._rows('''SELECT c.* FROM chart_versions c JOIN versions USING(version)
                             WHERE chart_identity = ? ORDER BY ordinal''', (chart_identity,))

    def chart(self, version, music_id, difficulty):
        self._version(version)
        rows = self._rows('''SELECT * FROM chart_versions WHERE version = ? AND music_id = ?
                             AND difficulty = ? ORDER BY chart_identity''', (version, music_id, difficulty))
        if rows:
            if len(rows) != 1:
                return {'state': 'identity_conflict', 'candidates': rows}
            return rows[0]
        history = self._rows('''SELECT DISTINCT c.identity, ordinal, state FROM chart_versions c
                                JOIN versions USING(version) WHERE music_id = ? AND difficulty = ?
                                ORDER BY ordinal''', (music_id, difficulty))
        if not history:
            return {'state': 'not_observed'}
        if len({row['identity'] for row in history}) != 1:
            return {'state': 'identity_conflict'}
        when = self._rows('SELECT ordinal FROM versions WHERE version = ?', (version,))[0]['ordinal']
        if any(row['ordinal'] < when and row['state'] == 'present' for row in history):
            return {'state': 'absent_after_observation'}
        # A future observation alone cannot distinguish never released from a gap in the HDDs.
        return {'state': 'unknown_before_observation'}

    def changes(self, chart_identity):
        legacy = self._rows('''SELECT c.* FROM changes c JOIN versions v ON v.version = c.new_version
                               WHERE identity = ? ORDER BY v.ordinal, kind, field''', (chart_identity,))
        if self.schema_version < 3:
            return legacy
        return legacy + self._rows('''SELECT e.*,b.version AS new_version,
             CASE WHEN e.effective_on IS NOT NULL THEN 'exact' ELSE b.timing END AS timing
             FROM history_events e JOIN history_batches b USING(batch) JOIN versions v USING(version)
             WHERE e.identity=? AND v.ordinal>12
             ORDER BY v.ordinal,coalesce(e.effective_on,e.earliest_on),e.event''', (chart_identity,))

    def jackets(self, version, music_id):
        self._version(version)
        return self._rows('''SELECT j.*, a.format, a.width, a.height, a.pixel_format
                             FROM jacket_manifest j LEFT JOIN jackets a USING(sha256)
                             WHERE j.version = ? AND j.music_id = ? ORDER BY j.identity''', (version, music_id))

    @staticmethod
    def jacket_path(root, record):
        """Resolve a matched DDS inside the supplied read-only HDD tree."""
        if record['status'] != 'matched' or not record['path']:
            return None
        base = Path(root).resolve(strict=True)
        candidate = (base / PurePosixPath(record['path'])).resolve(strict=True)
        if not candidate.is_relative_to(base) or not candidate.is_file():
            raise ValueError('Jacket path outside HDD tree or unavailable')
        return candidate

    def constants(self, version, *, include_inferred=False):
        """Return eligible values keyed by HDD chart identity."""
        self._version(version)
        rows = self._rows('''SELECT chart_identity, selected_hundredths, constant_status FROM chart_versions
                             WHERE version=? AND state='present' AND selected_hundredths IS NOT NULL
                             ORDER BY chart_identity''', (version,))
        return {row['chart_identity']: Decimal(row['selected_hundredths']).scaleb(-2) for row in rows
                if include_inferred or row['constant_status'] in ('hdd_observed', 'hdd_inherited')}

    def score_mappings(self, version):
        self._version(version)
        return self._rows('SELECT * FROM score_mapping WHERE version = ? ORDER BY chart_identity', (version,))

    def cn_mapping_candidates(self, version):
        """Trace a new-cabinet chart to older CN mapping evidence through its canonical identity."""
        self._version(version)
        return self._rows('''SELECT c.chart_identity,c.music_id AS jp_music_id,
                   m.cn_music_id,m.cn_slot,m.cn_fingerprint,m.version AS evidence_version,
                   m.status AS evidence_status,'candidate' AS status
            FROM chart_versions c JOIN score_mapping m USING(chart_identity)
            JOIN versions observed ON observed.version=m.version
            JOIN versions target ON target.version=c.version
            WHERE c.version=? AND target.ordinal>12 AND observed.ordinal<=12 AND m.cn_music_id IS NOT NULL
            AND c.state='present'
            ORDER BY c.chart_identity,observed.ordinal DESC,m.cn_music_id''', (version,))

    def _timeline_rows(self, query, args=()):
        if self.schema_version < SCHEMA_VERSION:
            raise ValueError('Lifecycle queries require a rebuilt old HDD catalog')
        return self._rows(query, args)

    def identities(self, music_id):
        if self.schema_version >= 3:
            return self._rows('''SELECT * FROM history_entities WHERE music_id=? OR identity IN (
                SELECT identity FROM sdhd_observed_ids WHERE music_id=?) ORDER BY kind,identity''',
                              (music_id, music_id))
        return self._timeline_rows('SELECT * FROM history_entities WHERE music_id=? ORDER BY kind,identity',
                                   (music_id,))

    def song_lifecycle(self, identity):
        """Return a song and all canonical chart histories, including archived variants."""
        song = self.lifecycle(identity)
        if song['entity']['kind'] != 'song':
            raise KeyError(identity)
        charts = self._timeline_rows('SELECT identity FROM history_entities WHERE parent=? ORDER BY slot,identity',
                                    (identity,))
        return {'song': song, 'charts': [self.lifecycle(row['identity']) for row in charts]}

    def title_identities(self, title):
        """Match the exact title in a preserved version record."""
        return self._rows('''SELECT DISTINCT e.* FROM music_versions m
                             JOIN history_entities e USING(identity)
                             WHERE m.title=? ORDER BY e.music_id,e.identity''', (title,))

    def first_release(self, identity):
        """Return exact or bounded first-release evidence, if any was indexed."""
        exact = self._timeline_rows('''SELECT effective_on AS exact_on, evidence AS basis
                                       FROM history_events WHERE identity=? AND kind='released'
                                       AND effective_on IS NOT NULL ORDER BY effective_on LIMIT 1''', (identity,))
        if self.schema_version >= 3:
            rows = self._rows('SELECT * FROM sdhd_first_release WHERE identity=?', (identity,))
            if rows:
                return rows[0] | {'evidence': self._rows('''SELECT day,locator,reference
                    FROM sdhd_release_evidence WHERE identity=? ORDER BY day,locator''', (identity,))}
        if exact:
            return exact[0] | {'earliest_on': exact[0]['exact_on'], 'latest_on': exact[0]['exact_on']}
        return None

    def official_notices(self, *, day=None, identity=None):
        """Read official evidence, with any matched HDD source and revision."""
        if day is not None:
            day = self._day(day)
        if not self._rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sdhd_notice'"):
            return []
        rows = self._rows('''SELECT n.*, c.identity,c.effective_on AS claim_on,c.kind,c.mode,
                                  c.detail,c.image,c.image_sha256,c.locator,c.package,c.source,c.revision,c.field
            FROM sdhd_notice n LEFT JOIN sdhd_notice_claim c ON c.announcement=n.url
            WHERE (? IS NULL OR c.effective_on=? OR (c.identity IS NULL AND n.effective_on=?))
              AND (? IS NULL OR c.identity=?)
            ORDER BY coalesce(c.effective_on,n.effective_on,n.published_on),n.url,c.identity,c.kind,c.field
            ''', (day, day, day, identity, identity))
        for row in rows:
            row['related'] = json.loads(row['related'])
        return rows

    def constants_on(self, day):
        """Return date-eligible constants and the catalog coverage label."""
        catalog = self.catalog_on(day)
        values = {}
        for row in catalog['rows']:
            value = (row['payload'] or {}).get('hdd_hundredths')
            if row['kind'] == 'chart' and value is not None:
                values[row['identity']] = Decimal(value).scaleb(-2)
        return {'version': catalog['version'], 'coverage': catalog['coverage'], 'constants': values}

    @staticmethod
    def resource_bytes(root, path, digest):
        """Read and verify an indexed relative file or member of a local ZIP."""
        marker = path.lower().find('.zip!/')
        container, member = (path[:marker + 4], path[marker + 6:]) if marker >= 0 else (path, None)
        base = Path(root).resolve(strict=True)
        candidate = (base / PurePosixPath(container)).resolve(strict=True)
        if not candidate.is_relative_to(base) or not candidate.is_file():
            raise ValueError('Resource outside HDD tree or unavailable')
        limit = 32 << 20
        if member is not None:
            with ZipFile(candidate) as archive:
                info = archive.getinfo(member)
                if info.file_size > limit:
                    raise ValueError('Resource exceeds 32 MiB')
                password = os.getenv('UNIBOT_HDD_ZIP_PASSWORD')
                data = archive.read(info, pwd=password.encode('utf-8') if password else None)
        else:
            if candidate.stat().st_size > limit:
                raise ValueError('Resource exceeds 32 MiB')
            data = candidate.read_bytes()
        if sha256(data).hexdigest() != digest:
            raise ValueError('Resource differs from archived SHA-256')
        return data

    def lifecycle(self, identity):
        """Song, ordinary chart or independent WE history with before/after evidence."""
        entities = self._timeline_rows('SELECT * FROM history_entities WHERE identity=?', (identity,))
        if not entities:
            raise KeyError(identity)
        revisions = self._timeline_rows('''
            SELECT o.*, r.payload, b.version, b.sequence, b.kind AS batch_kind, b.timing
            FROM history_observations o JOIN history_revisions r USING(revision)
            JOIN history_batches b USING(batch) WHERE o.identity=? ORDER BY o.source,o.batch
        ''', (identity,))
        for row in revisions:
            row['payload'] = json.loads(row['payload'])
        events = self._timeline_rows('''
            SELECT * FROM history_events WHERE identity=?
            ORDER BY coalesce(effective_on,earliest_on,'9999'), event
        ''', (identity,))
        related = self._timeline_rows('''
            SELECT * FROM history_identity_links WHERE identity=? OR related=? ORDER BY kind,identity,related
        ''', (identity,identity))
        return {'entity': entities[0], 'observations': revisions, 'events': self._event_values(events), 'related': related}

    @staticmethod
    def _event_values(rows):
        for row in rows:
            row['before'] = json.loads(row['before'])
            row['after'] = json.loads(row['after'])
        return rows

    def batches_on(self, day):
        """Return directly dated batches and bounded changes that might include day."""
        day = self._day(day)
        batches = self._timeline_rows('SELECT * FROM history_batches WHERE effective_on=? ORDER BY batch', (day,))
        events = self._event_values(self._timeline_rows(
            'SELECT * FROM history_events WHERE effective_on=? ORDER BY event', (day,)))
        possible = self._timeline_rows('''
            SELECT b.batch,b.version,b.kind,count(*) AS possible_events
            FROM history_events e JOIN history_batches b USING(batch)
            WHERE e.effective_on IS NULL AND ? BETWEEN e.earliest_on AND e.latest_on
            GROUP BY b.batch,b.version,b.kind ORDER BY b.batch
        ''', (day,))
        return {'day': day, 'exact': batches, 'events': events, 'possible': possible}

    def batch_history(self, batch):
        rows = self._timeline_rows('SELECT * FROM history_batches WHERE batch=?', (batch,))
        if not rows:
            raise KeyError(batch)
        return {'batch': rows[0], 'events': self._event_values(self._timeline_rows('''
                    SELECT * FROM history_events WHERE batch=? ORDER BY event
                ''', (batch,))), 'game_event_changes': self._event_values(self._timeline_rows('''
                    SELECT * FROM history_game_event_changes WHERE batch=? ORDER BY event_id,field,new_source
                ''', (batch,))), 'assets': self.assets(batch=batch)}

    def package_checkpoint(self, batch):
        """Return the local SDHD overlay after a package, including disabled slots."""
        selected = self._timeline_rows("""
            SELECT version,sequence FROM history_batches WHERE batch=? AND kind='package'
            AND version IN (SELECT version FROM versions WHERE ordinal>12)
        """, (batch,))
        if not selected:
            raise KeyError(batch)
        if not self._rows("SELECT 1 FROM sqlite_master WHERE name='sdhd_observation_resources'"):
            raise ValueError('Package checkpoints require a rebuilt SDHD catalog')
        target = selected[0]
        rows = self._rows("""
            WITH packages AS (
                SELECT batch,sequence FROM history_batches
                WHERE version=? AND kind='package' AND sequence<=?
            ), ranked AS (
                SELECT o.*,row_number() OVER (PARTITION BY o.identity ORDER BY p.sequence DESC) AS rank
                FROM packages p JOIN history_observations o USING(batch)
            ), aliases AS MATERIALIZED (
                SELECT ids.* FROM packages p JOIN sdhd_observed_ids ids USING(batch)
            )
            SELECT e.identity,e.kind,e.parent,coalesce(ids.music_id,e.music_id) AS music_id,
                   CASE WHEN e.kind='we' THEN 5 ELSE e.slot END AS slot,
                   e.difficulty,o.batch,o.state,o.source,data.path AS resource_path,r.payload
            FROM ranked o JOIN history_entities e USING(identity)
            JOIN history_revisions r USING(revision)
            LEFT JOIN sdhd_observation_resources data USING(batch,identity,source)
            LEFT JOIN aliases ids USING(batch,identity,source)
            WHERE o.rank=1 ORDER BY music_id,e.slot,e.identity
        """, (target['version'], target['sequence']))
        for row in rows:
            row['payload'] = json.loads(row['payload'])
        return rows

    def batches(self, version=None):
        where = ' WHERE b.version=?' if version is not None else ''
        return self._timeline_rows('SELECT b.* FROM history_batches b LEFT JOIN versions v USING(version)' + where +
                                   ' ORDER BY coalesce(v.ordinal,999),b.effective_on,b.sequence,b.batch',
                                   (version,) if version is not None else ())

    def assets(self, *, batch=None):
        query = '''SELECT a.kind,l.* FROM history_assets a JOIN history_asset_links l USING(asset)'''
        if batch is not None:
            return self._timeline_rows(query + ' WHERE batch=? ORDER BY asset,location,source', (batch,))
        return self._timeline_rows(query + ' ORDER BY asset,location,source')

    def game_events(self, music_id):
        return self._timeline_rows('''
            SELECT e.*,l.role,l.target_id,l.label FROM history_game_events e
            JOIN history_game_event_links l USING(source)
            WHERE l.target_id=? AND l.role IN ('music','map_music','recommend_music')
            ORDER BY source,role
        ''', (music_id,))

    def content_sources(self, digest):
        """Locate actual archived C2S or image bytes using a revision digest."""
        return self._timeline_rows('''
            SELECT path,kind,bytes,sha256 FROM sources
            WHERE sha256=? AND kind IN ('c2s','image') ORDER BY kind,path
        ''', (digest,))

    @staticmethod
    def _day(day):
        parsed = date.fromisoformat(day)
        if day != parsed.isoformat():
            raise ValueError('Use a Japanese calendar date in YYYY-MM-DD format')
        return day

    def checkpoint(self, version, *, active_only=True):
        """Reconstruct a version endpoint through the revision model."""
        self._version(version)
        rows = self._timeline_rows('''
            SELECT e.*,o.state,o.basis,o.source,r.revision,r.payload
            FROM history_observations o JOIN history_entities e USING(identity)
            JOIN history_revisions r USING(revision)
            WHERE o.batch=? AND (?=0 OR o.state='present')
            ORDER BY e.music_id,e.kind,e.identity
        ''', (f'end:{version}', int(active_only)))
        for row in rows:
            row['payload'] = json.loads(row['payload'])
        if self.schema_version >= 3:
            aliases = self._rows('SELECT identity,music_id FROM sdhd_observed_ids WHERE batch=?',
                                 (f'end:{version}',))
            by_identity = {row['identity']: row['music_id'] for row in aliases}
            if by_identity:
                slots = self._rows('''SELECT chart_identity, slot FROM chart_versions WHERE version=?
                    UNION ALL SELECT chart_identity, slot FROM worlds_end_versions WHERE version=?''',
                                   (version, version))
                by_chart = {row['chart_identity']: row['slot'] for row in slots}
                for row in rows:
                    row['music_id'] = by_identity[row['identity']]
                    row['slot'] = by_chart.get(row['identity'])
        return rows

    def catalog_on(self, day, *, include_possible=False):
        """Bulk JP day estimate. Missing dates and indeterminate revisions stay explicit."""
        day = self._day(day)
        editions = self._timeline_rows('SELECT * FROM versions WHERE ? BETWEEN start_on AND end_on', (day,))
        if not editions:
            return {'day': day, 'version': None, 'coverage': 'out_of_scope', 'rows': [], 'uncertain_count': 0}
        edition = editions[0]
        rows = self._timeline_rows('''
            SELECT e.*,w.availability,w.basis,w.revision,w.candidates,r.payload
            FROM history_windows w JOIN history_entities e USING(identity)
            LEFT JOIN history_revisions r USING(revision)
            WHERE w.version=? AND ? BETWEEN w.start_on AND w.end_on
            ORDER BY e.music_id,e.kind,e.identity
        ''', (edition['version'], day))
        alternatives = self._timeline_rows('''
            SELECT revision,payload FROM history_revisions WHERE revision IN (
                SELECT DISTINCT j.value FROM history_windows w, json_each(w.candidates) j
                WHERE w.version=? AND ? BETWEEN w.start_on AND w.end_on AND w.revision IS NULL
                AND (?=1 OR w.availability <> 'possible')
            )
        ''', (edition['version'], day, int(include_possible)))
        payload_by_revision = {row['revision']: json.loads(row['payload']) for row in alternatives}
        aliases = {}
        dated_fields = {}
        if edition['basis'] == 'sdhd_partial_operating_history':
            for alias in self._rows('''SELECT DISTINCT a.identity,a.music_id FROM sdhd_observed_ids a
                        JOIN history_batches b USING(batch) WHERE b.version=? ORDER BY a.music_id''',
                                    (edition['version'],)):
                aliases.setdefault(alias['identity'], []).append(alias['music_id'])
            # A000 defaults already enter through the day's revision candidates.
            # Overlay field-level dates only where the evidence adds information.
            for known in self._rows('''SELECT confirmed.identity,confirmed.field,confirmed.after,confirmed.effective_on
                    FROM history_batches b CROSS JOIN history_events confirmed ON b.batch=confirmed.batch
                    WHERE b.version=? AND confirmed.kind='hdd_revision' AND confirmed.effective_on<=?
                      AND (b.sequence<>0 OR confirmed.evidence<>'SDHD overlay content change')
                      AND NOT EXISTS (
                          SELECT 1 FROM history_events later JOIN history_batches lb ON lb.batch=later.batch
                          WHERE later.identity=confirmed.identity AND later.field=confirmed.field
                            AND later.kind='hdd_revision' AND later.event>confirmed.event
                            AND lb.version=b.version AND coalesce(later.effective_on,later.earliest_on)<=?
                      ) ORDER BY confirmed.event''', (edition['version'], day, day)):
                dated_fields.setdefault(known['identity'], {})[known['field']] = known
        songs = {row['identity'] for row in rows if row['kind'] == 'song'}
        available = {row['identity'] for row in rows
                     if row['kind'] == 'song' and row['availability'] in ('present','inferred_present')}
        uncertain = 0
        result = []
        for row in rows:
            if row['parent'] and row['parent'] not in songs:
                continue
            if row['parent'] and row['parent'] not in available:
                row['availability'] = 'possible'
            if row['availability'] == 'possible' or row['revision'] is None:
                uncertain += 1
            if include_possible or row['availability'] != 'possible':
                if row['identity'] in aliases:
                    observed = aliases[row['identity']]
                    row['observed_music_ids'] = observed
                    if len(observed) == 1:
                        row['music_id'] = observed[0]
                row['candidates'] = json.loads(row['candidates'])
                row['value_status'] = 'unknown'
                if row['payload']:
                    row['payload'] = json.loads(row['payload'])
                    row['value_status'] = 'unique'
                elif row['candidates']:
                    options = [payload_by_revision[key] for key in row['candidates']]
                    row['payload'] = {field: value for field, value in options[0].items()
                                      if all(field in option and option[field] == value for option in options[1:])}
                    row['value_status'] = 'partial'
                if row['identity'] in dated_fields:
                    verified = dated_fields[row['identity']]
                    row['dated_fields'] = {key: value['effective_on'] for key, value in verified.items()}
                    values = {key: json.loads(value['after']) for key, value in verified.items()}
                    payload = row['payload'] or {}
                    if any(payload.get(key) != value or key not in payload for key, value in values.items()):
                        row['value_status'] = 'partial'
                    row['payload'] = payload | values
                if row['kind'] == 'chart':
                    if (row['payload'] or {}).get('hdd_hundredths') is None:
                        row['constant_status'] = 'unknown'
                    else:
                        row['constant_status'] = 'hdd_consensus' if row['value_status'] == 'partial' else 'hdd_revision'
                result.append(row)
        coverage = 'partial'
        if day == edition['end_on']:
            coverage = 'sdhd_inventory_cutoff' if edition['basis'] == 'sdhd_partial_operating_history' else 'checkpoint'
        return {'day': day, 'version': edition['version'], 'coverage': coverage,
                'rows': result, 'uncertain_count': uncertain}
