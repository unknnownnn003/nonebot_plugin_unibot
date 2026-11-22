"""UniBot queries against the published v3 JP HDD endpoint catalog."""
from collections import defaultdict
from contextlib import closing
from datetime import date
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3

from PIL import Image

from .legacy_rating import normalize_title
from .old_hdd_history import OldHddHistory

HISTORY_ERRORS = (OSError, ValueError, sqlite3.Error)
UNAVAILABLE = "日服 HDD 历史库不可用，请管理员检查 v3 正式库配置。"

# Existing UI asset keys. The game's chronological ordering comes from SQLite.
LOGO_IDS = dict(zip(
    ("CHUNITHM", "PLUS", "AIR", "AIR PLUS", "STAR", "STAR PLUS", "AMAZON", "AMAZON PLUS",
     "CRYSTAL", "CRYSTAL PLUS", "PARADISE", "PARADISE LOST", "NEW", "NEW PLUS", "SUN",
     "SUN PLUS", "LUMINOUS", "LUMINOUS PLUS", "VERSE", "X-VERSE", "X-VERSE-X", "MATE"),
    (10000, 10500, 11000, 11500, 12000, 12500, 13000, 13500, 14000, 14500, 15000, 15500,
     20000, 20500, 21000, 21500, 22000, 22500, 23000, 24000, 24500, 25000),
))
TABLES = frozenset({
    "versions", "complete", "music_versions", "chart_versions", "worlds_end_versions",
    "history_observations", "history_revisions", "history_entities", "history_events", "history_windows",
    "history_identity_links", "score_mapping", "sdhd_observed_ids", "sdhd_first_release", "jacket_manifest",
})
HASH = re.compile(r"[0-9a-f]{64}")


class JpHistory(OldHddHistory):
    def __init__(self, database):
        self.signature = file_signature(Path(database))
        super().__init__(database)
        if self.schema_version != 3:
            raise ValueError("UniBot requires the unified JP HDD v3 catalog")
        with closing(self._connect()) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if TABLES - tables or [row[0] for row in db.execute("PRAGMA quick_check(1)")] != ["ok"]:
                raise ValueError("Invalid JP HDD catalog")
        self.editions = self.versions()
        self.by_name = {row["version"]: row for row in self.editions}
        if (set(self.by_name) != set(LOGO_IDS) or len(self.editions) != len(LOGO_IDS)
                or len({row["ordinal"] for row in self.editions}) != len(self.editions)):
            raise ValueError("Incomplete JP HDD calendar")
        previous = None
        for row in self.editions:
            start, end = date.fromisoformat(row["start_on"]), date.fromisoformat(row["end_on"])
            if start > end or (previous is not None and start <= previous):
                raise ValueError("Invalid JP HDD calendar")
            previous = end
        self._check_snapshot()

    def _check_snapshot(self):
        if file_signature(self.database) != self.signature:
            raise ValueError("JP HDD catalog changed during query")

    def _rows(self, query, args=()):
        self._check_snapshot()
        rows = super()._rows(query, args)
        self._check_snapshot()
        return rows

    def _connect(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro&immutable=1", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    def parse_version(self, value):
        name = normalize_title(value)
        return next((row for row in self.editions if normalize_title(row["version"]) == name), None)

    def split_query(self, value):
        value = " ".join(value.split())
        for name in sorted(self.by_name, key=len, reverse=True):
            words = len(name.split())
            parts = value.rsplit(" ", words)
            if len(parts) > words and normalize_title(" ".join(parts[-words:])) == normalize_title(name):
                titles = self._rows("SELECT DISTINCT title FROM music_versions")
                if any(normalize_title(row["title"] or "") == normalize_title(value) for row in titles):
                    break
                return parts[0], self.by_name[name]
        return value, None

    def search(self, query, edition=None):
        query = normalize_title(query)
        if not query:
            return []
        compact = "".join(character for character in query if character.isalnum())
        rows = self._rows("""SELECT m.*,v.ordinal, o.revision FROM music_versions m
            JOIN versions v USING(version)
            LEFT JOIN history_observations o ON o.batch='end:'||m.version AND o.identity=m.identity
            ORDER BY v.ordinal DESC,m.identity""")
        matches, by_identity = {}, defaultdict(list)
        for row in rows:
            identity = row["identity"]
            by_identity[identity].append(row)
            title = normalize_title(row["title"] or "")
            rank = 0
            if query.startswith("#"):
                rank = 2 if query[1:] == identity.casefold() else 0
            elif query.isascii() and query.isdecimal():
                rank = 2 if str(row["music_id"]) == (query.lstrip("0") or "0") else 0
            elif query == title:
                rank = 2
            elif query in title or (len(compact) >= 3 and compact in "".join(
                    character for character in title if character.isalnum())):
                rank = 1
            if rank:
                matches[identity] = max(matches.get(identity, 0), rank)
        if query.isascii() and query.isdecimal() and len(query) <= 18:
            for row in self.identities(int(query)):
                if row["kind"] == "song" and row["identity"] in by_identity:
                    matches[row["identity"]] = 2
        target = edition["version"] if edition else self.editions[-1]["version"]
        title_search = not query.startswith("#") and not query.isdecimal()
        if title_search:
            playable = {identity: rank for identity, rank in matches.items() if any(
                row["version"] == target and row["state"] == "present" for row in by_identity[identity])}
            # Keep exact historical aliases ahead of generic partial titles.
            if playable and max(playable.values()) == max(matches.values()):
                matches = playable
        highest = max(matches.values(), default=0)
        result = []
        for identity, rank in matches.items():
            if rank != highest:
                continue
            history = by_identity[identity]
            latest = next((row for row in history if row["state"] == "present"), None)
            chosen = (next((row for row in history if row["version"] == edition["version"]), None)
                      if edition else latest)
            result.append({"row": chosen, "reference": latest or history[0]})
        if title_search and len(result) > 1:
            # WE with a proven parent is displayed on that song's card in the
            # same edition. Numeric IDs and explicit identities stay separate.
            links = self._rows("SELECT identity,related FROM history_identity_links WHERE kind='worlds_end_origin'")
            found = {entry["row"]["identity"]: entry["row"]["version"] for entry in result if entry["row"]}
            combined = {link["identity"] for link in links
                        if found.get(link["identity"]) and found[link["identity"]] == found.get(link["related"])}
            result = [entry for entry in result if entry["reference"]["identity"] not in combined]
        return sorted(result, key=lambda entry: (
            entry["row"] is None, entry["reference"]["title"], entry["reference"]["identity"]))

    def song_charts(self, song):
        version, identity = song["version"], song["identity"]
        normal = self._rows("""SELECT c.*,o.revision,
            coalesce(nullif(c.notes_designer,''),json_extract(r.payload,'$.notes_designer')) AS designer
            FROM chart_versions c
            JOIN history_observations o ON o.batch='end:'||c.version AND o.identity=c.chart_identity
            JOIN history_revisions r USING(revision)
            WHERE c.version=? AND c.identity=? ORDER BY c.slot,c.chart_identity""", (version, identity))
        extras = self._rows("""SELECT w.*,o.revision,
            coalesce(nullif(w.notes_designer,''),json_extract(r.payload,'$.notes_designer')) AS designer
            FROM worlds_end_versions w
            JOIN music_versions m USING(version,identity)
            JOIN history_observations o ON o.batch='end:'||w.version AND o.identity=w.chart_identity
            JOIN history_revisions r USING(revision)
            WHERE w.version=? AND (w.identity=? OR m.origin_identity=? OR EXISTS (
                SELECT 1 FROM history_identity_links l WHERE l.kind='worlds_end_origin'
                AND l.identity=w.identity AND l.related=?))
            ORDER BY w.music_id,w.chart_identity""", (version, identity, identity, identity))
        return [
            dict(row, difficulty=row["slot"], song_id=row["music_id"], level=row["display_level"],
                 level_value=hdd_constant(row), note_designer=row["designer"])
            for row in normal
        ] + [
            dict(row, difficulty=5, song_id=row["music_id"], kanji=row["tag"], star=row["stars"],
                 note_designer=row["designer"]) for row in extras
        ]

    def manifests(self, version=None, music_id=None):
        return self._rows("""SELECT j.*,o.revision FROM jacket_manifest j
            JOIN history_observations o ON o.batch='end:'||j.version AND o.identity=j.identity
            WHERE (? IS NULL OR j.version=?) AND (? IS NULL OR j.music_id=?)
            ORDER BY j.version,j.identity""", (version, version, music_id, music_id))


def hdd_constant(chart):
    if chart["constant_status"] in {"hdd_observed", "hdd_inherited"}:
        value = chart["selected_hundredths"]
        return Decimal(value).scaleb(-2) if value is not None else None
    return None


def file_signature(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=2)
def _validated(database, signature):
    history = JpHistory(database)
    if history.signature != signature:
        raise ValueError("JP HDD catalog changed during validation")
    return history


def load():
    from .paths import jp_history_database
    path = jp_history_database().resolve(strict=True)
    return _validated(path, file_signature(path))


def jacket_path(root, digest):
    if isinstance(digest, str) and HASH.fullmatch(digest):
        return Path(root) / "jackets" / digest[:2] / (digest + ".png")
    return None


def load_jacket(root, digest):
    path = jacket_path(root, digest)
    if path is None:
        return None
    try:
        data = path.read_bytes()
        if sha256(data).hexdigest() != path.with_suffix(".sha256").read_text(encoding="ascii").strip():
            return None
        with Image.open(BytesIO(data), formats=["PNG"]) as source:
            return source.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def genre_name(value):
    def strings(node):
        if isinstance(node, list):
            if len(node) == 2 and node[0] == "str" and isinstance(node[1], str):
                yield node[1]
            else:
                for child in node:
                    yield from strings(child)
    return " / ".join(dict.fromkeys(strings(json.loads(value)))) if value else ""


def release_label(release, editions):
    if not release:
        return "未知"
    day = release["exact_on"]
    basis = release["basis"].lower()
    if not day:
        return f"{release['earliest_on'] or '?'} ～ {release['latest_on'] or '?'}（区间）"
    edition = next((row["version"] for row in editions if row["start_on"] <= day <= row["end_on"]), "")
    quality = "推定" if any(word in basis for word in ("assum", "inferred", "continuity", "observation")) else "有日期记录"
    return f"{day} {edition}（{quality}）"


def lookup(history, query):
    from .results import image, outcome
    from .paths import HISTORY_DIR
    from .song_images import render_jp_song

    term, edition = history.split_query(query)
    matches = history.search(term, edition)
    if not matches:
        return outcome("NOT_FOUND", "日服 HDD 未找到曲目，请核对曲名、日服 ID 或版本。", status="needs_input")
    if len(matches) > 1:
        choices = [entry["row"] or entry["reference"] for entry in matches[:8]]
        lines = [f"{row['music_id']} {row['title']} · #{row['identity']}" for row in choices]
        suffix = " " + edition["version"] if edition else ""
        hint = f"找到 {len(matches)} 个身份，请用 /chu查歌 #身份{suffix} 选择。\n" + "\n".join(lines)
        if len(matches) > 8:
            hint += "\n可缩短候选范围后再查。"
        return outcome("JP_HISTORY_AMBIGUOUS", hint, status="needs_input")
    match = matches[0]
    row = match["row"]
    if row is None:
        title = match["reference"]["title"]
        version = edition["version"] if edition else history.editions[-1]["version"]
        return outcome("JP_HISTORY_NOT_AVAILABLE", f"{title} 在 {version} 未收录或已停配。", status="needs_input")
    current = history.editions[-1]["version"]
    state = {"present": "已收录", "removed": "已停配", "disabled": "已禁用",
             "possible": "可玩状态待确认", "not_released": "未配信", "tutorial": "教程"}.get(row["state"], "状态待确认")
    if edition is None and row["version"] != current:
        lifecycle = history._rows("""SELECT kind,effective_on FROM history_events
            WHERE identity=? AND kind IN ('removed','restored') AND effective_on BETWEEN ? AND ?
            ORDER BY effective_on DESC,event DESC LIMIT 2""",
            (row["identity"], history.by_name[row["version"]]["end_on"], history.by_name[current]["end_on"]))
        removed = lifecycle and lifecycle[0]["kind"] == "removed" and not any(
            event["kind"] == "restored" and event["effective_on"] == lifecycle[0]["effective_on"] for event in lifecycle)
        status = "有停配记录" if removed else "无有效记录"
        state += f" · {current} {status}，显示最后有效收录"
    charts = history.song_charts(row)
    content = render_jp_song(row | {"id": row["music_id"], "genre": genre_name(row["genre"])},
                             load_jacket(HISTORY_DIR, row["jacket_sha256"]), charts, state,
                             release_label(history.first_release(row["identity"]), history.editions),
                             history.by_name[row["version"]]["end_on"], LOGO_IDS.get(row["version"]))
    return outcome(data={"version": row["version"], "identity": row["identity"],
                         "revision": row["revision"], "state": row["state"]}, artifacts=[image(content)])
