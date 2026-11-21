"""Shared import and resolution logic for the offline history builder."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from _unibot_history.history import normalize_name


def identifier(kind, *parts):
    # Include the namespace and boundaries of each component in the canonical seed.
    return uuid5(NAMESPACE_URL, "unibot/chunithm/history/" + json_text([kind, *parts])).hex


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def offset(day, days=1):
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def decimal_tenths(value):
    if value in (None, ""):
        return None
    scaled = Decimal(str(value)) * 10
    if not 1 <= scaled <= 200 or scaled != scaled.to_integral_value():
        raise ValueError(f"Invalid chart constant: {value}")
    return int(scaled)


class CatalogImport:
    def __init__(self, conn, artifacts):
        self.conn = conn
        self.artifacts = artifacts
        self.songs = set()
        self.revisions = set()
        self.song_states = defaultdict(list)
        self.chart_states = defaultdict(list)
        self.constants = defaultdict(list)
        self.editions = {}
        self.points = []
        self.baseline = {}
        self.ordinary = defaultdict(set)
        self.jp_anchor = {}
        self.current = defaultdict(dict)
        self.current_songs = {}
        self.legacy = {}
        self.otoge_notes = defaultdict(set)
        self.issues = defaultdict(int)

    def record_name(self, song, name, basis, artifact, locator):
        if song not in self.songs:
            self.conn.execute("INSERT INTO song VALUES (?,?)", (song, basis))
            self.songs.add(song)
        self.conn.execute("INSERT OR IGNORE INTO song_name VALUES (?,?,?,?,?)",
                          (song, name, normalize_name(name), artifact, locator))
        return song

    def song(self, seed, name, artifact, locator):
        return self.record_name(identifier("song", *seed), name, seed[0], artifact, locator)

    def chart(self, song, difficulty, basis, *, variant="", fingerprint=None, metadata=None):
        chart = identifier("chart", song, difficulty, variant)
        revision = identifier("revision", chart, basis, fingerprint)
        if revision not in self.revisions:
            self.conn.execute("INSERT OR IGNORE INTO chart VALUES (?,?,?,?)", (chart, song, difficulty, variant))
            self.conn.execute("INSERT INTO chart_revision VALUES (?,?,?,?,?)",
                              (revision, chart, basis, fingerprint, json_text(metadata or {})))
            self.revisions.add(revision)
        return revision

    def external(self, artifact, region, key, chart_key, song, locator, revision=None, status="observed"):
        observed = self.artifacts[artifact].get("observed_on")
        self.conn.execute("INSERT INTO external_id VALUES (?,?,?,?,?,?,?,?,?)",
                          (artifact, region, str(key), chart_key, song, revision, observed, status, locator))

    def evidence(self, artifact, locator, region, song, revision, field, raw, value, *,
                 start=None, end=None, grain="unknown", quality="B", kind="fact"):
        oid = identifier("observation", artifact, locator, region, song, revision, field, start, end)
        self.conn.execute("INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (oid, artifact, locator, region, song, revision, field, json_text(raw), json_text(value),
                           start, end, grain, quality, kind))
        return oid

    def states(self, artifact, locator, region, song, title, periods, *, revision=None, display=None):
        intervals = self.chart_states if revision else self.song_states
        for start, end, state, quality, grain, priority in periods:
            if end is not None and start >= end:
                continue
            oid = self.evidence(artifact, locator, region, song, revision, "availability",
                                {"title": title, "level": display}, state, start=start, end=end,
                                grain=grain, quality=quality)
            intervals[region, revision or song].append((start, end, priority, (state, oid, display)))

    def constant(self, artifact, locator, region, song, revision, raw, value, *,
                 start, end=None, grain="point", quality="B", kind="exact", activate=True, priority=10,
                 observed_start=None, observed_end=None):
        # A version-qualified number is projected from its checkpoint. The original
        # possible effective window stays on the observation, never the resolved row.
        oid = self.evidence(artifact, locator, region, song, revision, "constant", raw, value,
                            start=observed_start or start, end=observed_end or end,
                            grain=grain, quality=quality, kind=kind)
        if activate:
            self.constants[region, revision].append((start, end, priority, (kind, value, oid, None)))
        return oid

    def dispute(self, region, version, revision, field, day, reason, candidates, *, reject=None):
        cid = identifier("conflict", region, version, revision, field, day, reason, sorted(candidates))
        self.conn.execute("INSERT INTO conflict VALUES (?,?,?,?,?,?,?,?)",
                          (cid, region, version, revision, field, day, reason, "resolved" if reject else "open"))
        self.conn.executemany("INSERT INTO conflict_observation VALUES (?,?)",
                              [(cid, oid) for oid in sorted(candidates)])
        if reject:
            artifact, chosen, why = reject
            self.conn.execute("INSERT INTO resolution VALUES (?,?,?,?)", (cid, chosen, artifact, why))
        return cid

    def compare_point(self, region, version, song, revision, day, compared, *, role, artifact, anchor=None):
        key = (region, revision)
        points = self.constants.get(key, ())
        baseline = covering(points, day)
        # Other evidence at this checkpoint must remain visible even when the
        # selected projection has already flagged a dispute.
        if baseline and baseline[0] == "conflict":
            baseline = covering([p for p in points if p[3][0] != "conflict"], day)
        oid, value = compared
        if baseline and baseline[1] != value:
            if role == "quarantined":
                self.dispute(region, version, revision, "constant", day, "cross_version_contamination",
                             (baseline[2], oid),
                             reject=(artifact, None, "Archive column quarantined; did not resolve the underlying number"))
                return
            cid = self.dispute(region, version, revision, "constant", day,
                               "different_constants", (baseline[2], oid))
            # A conflicting checkpoint invalidates the continuation of the
            # selected observation until it actually ends. An adjacent day
            # alone cannot resolve the dispute.
            end = offset(day)
            if role == "audit":
                end = next(stop for start, stop, _, fact in points
                           if fact == baseline and start <= day and (stop is None or day < stop))
            self.constants[key].append((day, end, 100, ("conflict", None, baseline[2], cid)))
        elif role == "checkpoint" and not baseline and anchor:
            self.constants[key].append((day, offset(day), 25, ("exact", value, oid, None)))

    def jacket(self, artifact, region, song, locator, key, url, *, width=None, height=None, sha256=None):
        if not key:
            return
        image_id = identifier("jacket", artifact, key, url)
        extension = url.rsplit(".", 1)[-1].lower() if url else ""
        format_name = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(extension)
        self.conn.execute("INSERT OR IGNORE INTO jacket_asset VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (image_id, artifact, key, url, format_name, width, height, sha256, "link_only", locator))
        self.conn.execute("INSERT OR IGNORE INTO song_jacket VALUES (?,?,?,?)",
                          (song, image_id, region, self.artifacts[artifact].get("observed_on")))

    def release(self, region, version, start, end, through, timezone, kind, artifact, locator, rules=None):
        self.conn.execute("INSERT INTO region_release VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (region, version, start, end, timezone, through, rules, kind, artifact, locator))

    def materialize_intervals(self):
        for (region, song), periods in sorted(self.song_states.items()):
            self.conn.executemany("INSERT INTO region_song_state VALUES (?,?,?,?,?,?)",
                                  [(region, song, start, end, state, oid)
                                   for start, end, (state, oid, _) in partition(periods)])
        for (region, rev), periods in sorted(self.chart_states.items()):
            self.conn.executemany("INSERT INTO region_chart_state VALUES (?,?,?,?,?,?,?)",
                                  [(region, rev, start, end, state, oid, level)
                                   for start, end, (state, oid, level) in partition(periods)])
        for (region, rev), periods in sorted(self.constants.items()):
            self.conn.executemany("INSERT INTO resolved_event VALUES (?,?,?,?,?,?,?,?,?)",
                                  [(identifier("event", region, rev, start), oid, rev, region, start, end,
                                    kind, value, conflict)
                                   for start, end, (kind, value, oid, conflict) in partition(periods)])


def covering(periods, day):
    accepted = [(priority, start, value) for start, end, priority, value in periods
                if start <= day and (end is None or day < end)]
    if not accepted:
        return None
    accepted.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return accepted[0][2]


def partition(periods):
    """Sweep all bounds and coalesce adjacent intervals with identical evidence."""
    bounds = sorted({bound for start, end, _, _ in periods for bound in (start, end) if bound is not None})
    rows = []
    for n, start in enumerate(bounds):
        value = covering(periods, start)
        end = bounds[n + 1] if n + 1 < len(bounds) else None
        if value is None:
            continue
        if rows and rows[-1][1] == start and rows[-1][2] == value:
            rows[-1] = (rows[-1][0], end, value)
        else:
            rows.append((start, end, value))
    return rows


def json_file(data):
    return json.loads(data.decode("utf-8-sig"), parse_float=Decimal)


def js_array(data, variable):
    content = data.decode("utf-8-sig").strip()
    prefix = f"window.{variable}="
    if not content.startswith(prefix) or not content.endswith(";"):
        raise ValueError(f"Unexpected format for {variable}")
    parsed = json_file(content[len(prefix):-1].encode("utf-8"))
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"Empty data for {variable}")
    return parsed


def absolute_url(url, suffix):
    if not re.fullmatch(r"[\w./-]+", suffix) or ".." in suffix:
        raise ValueError(f"Unexpected public resource key {suffix!r}")
    return url + suffix
