"""Read-only access to a separately built JP old-cabinet HDD audit."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path, PurePosixPath
import sqlite3


APPLICATION_ID = 1431193672
SCHEMA_VERSION = 1


class OldHddHistory:
    """Explicit database path. No LocalStore or command activation occurs."""

    def __init__(self, database):
        self.database = Path(database).resolve(strict=True)
        with closing(self._connect()) as db:
            if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
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
