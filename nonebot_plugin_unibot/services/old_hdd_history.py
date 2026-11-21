"""Read-only access to a separately built JP old-cabinet HDD audit."""
from contextlib import closing
from datetime import date
from decimal import Decimal
import json
from pathlib import Path, PurePosixPath
import sqlite3


APPLICATION_ID = 1431193672
SCHEMA_VERSION = 2


class OldHddHistory:
    """Explicit database path. No LocalStore or command activation occurs."""

    def __init__(self, database):
        self.database = Path(database).resolve(strict=True)
        with closing(self._connect()) as db:
            self.schema_version = db.execute('PRAGMA user_version').fetchone()[0]
            if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or self.schema_version not in (1, SCHEMA_VERSION)):
                raise ValueError('Unsupported old HDD catalog')
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
        return self._rows('''SELECT c.* FROM changes c JOIN versions v ON v.version = c.new_version
                             WHERE identity = ? ORDER BY v.ordinal, kind, field''', (chart_identity,))

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
                if include_inferred or row['constant_status'] == 'hdd_observed'}

    def score_mappings(self, version):
        self._version(version)
        return self._rows('SELECT * FROM score_mapping WHERE version = ? ORDER BY chart_identity', (version,))

    def _timeline_rows(self, query, args=()):
        if self.schema_version < SCHEMA_VERSION:
            raise ValueError('Lifecycle queries require a rebuilt old HDD catalog')
        return self._rows(query, args)

    def identities(self, music_id):
        return self._timeline_rows('SELECT * FROM history_entities WHERE music_id=? ORDER BY kind,identity',
                                   (music_id,))

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
                if row['kind'] == 'chart':
                    if (row['payload'] or {}).get('hdd_hundredths') is None:
                        row['constant_status'] = 'unknown'
                    else:
                        row['constant_status'] = 'hdd_consensus' if row['value_status'] == 'partial' else 'hdd_revision'
                result.append(row)
        return {'day': day, 'version': edition['version'], 'coverage': 'checkpoint' if day == edition['end_on']
                else 'partial', 'rows': result, 'uncertain_count': uncertain}
