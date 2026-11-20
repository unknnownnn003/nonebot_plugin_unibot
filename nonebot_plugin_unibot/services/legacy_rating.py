"""PARADISE LOST simulation on the requesting player's existing OAuth snapshot."""
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import unicodedata

VERSION = "PARADISE LOST"
VERSION_ID = 15500
HISTORY_PATH = Path(__file__).resolve().parents[1] / "assets" / "historical" / "paradise_lost.json"
SOURCE_COMMIT = "ee801ea72184a44fa274a3eaa0b983508ff6c2e4"
SOURCE_SHA256 = "edde71d7889411939c91f30af514344bb086dfac8480bf04da8ef9c65d3e1cd4"
ZERO = Decimal(0)


def normalize_title(title):
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def chart_identity(song, chart):
    """Guard pinned IDs against subsequent title, author or chart identity changes."""
    attributes = (song.get("title"), song.get("artist"), song.get("version"),
                  chart.get("version"), chart.get("note_designer"), chart.get("notes"))
    packed = json.dumps(attributes, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HistoricalChart:
    title: str
    constant: Decimal
    identity: str


def parse_history(payload):
    source = payload["source"]
    if (payload["schema_version"] != 1 or payload["version"] != VERSION
            or source["commit"] != SOURCE_COMMIT or source["sha256"] != SOURCE_SHA256):
        raise ValueError("Unexpected historical source")
    charts, titles = {}, set()
    for row in payload["songs"]:
        title = row["title"]
        normalized = normalize_title(title)
        if not normalized or normalized in titles:
            raise ValueError("Duplicate or empty historical title")
        titles.add(normalized)
        constants = row["constants"]
        if len(constants) != 4 or any(type(value) is not int or not 10 <= value <= 141 for value in constants):
            raise ValueError("Invalid historical constant")
        if "lxns_id" not in row:
            continue
        sid = row["lxns_id"]
        if type(sid) is not int or sid <= 0 or len(row["identities"]) != 4:
            raise ValueError("Invalid historical mapping")
        for difficulty, identity in enumerate(row["identities"]):
            if identity is None:
                continue
            key = sid, difficulty
            if key in charts or len(identity) != 64 or any(ch not in "0123456789abcdef" for ch in identity):
                raise ValueError("Duplicate or invalid historical chart identity")
            charts[key] = HistoricalChart(title, Decimal(constants[difficulty]).scaleb(-1), identity)
    if len(titles) != 893 or len(charts) != payload["mapping"]["mapped_charts"] or not charts:
        raise ValueError("Incomplete historical snapshot")
    return charts


@lru_cache(maxsize=1)
def load_history():
    try:
        return parse_history(json.loads(HISTORY_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, LookupError) as error:
        raise ValueError("Historical catalog unavailable") from error


def chart_rating(constant, score):
    """Unrounded pre-NEW rating, including the two low-score slopes."""
    constant = Decimal(str(constant))
    if score >= 1_007_500:
        value = constant + 2
    elif score >= 1_005_000:
        value = constant + Decimal("1.5") + Decimal(score - 1_005_000) / 5_000
    elif score >= 1_000_000:
        value = constant + 1 + Decimal(score - 1_000_000) / 10_000
    elif score >= 975_000:
        value = constant + Decimal(score - 975_000) / 25_000
    elif score >= 900_000:
        value = constant - 5 + Decimal(score - 900_000) / 15_000
    elif score >= 800_000:
        value = (constant - 5) * (score - 700_000) / 200_000
    elif score > 500_000:
        value = (constant - 5) * (score - 500_000) / 600_000
    else:
        value = ZERO
    return max(ZERO, value)


def score_rank(score):
    bounds = (500_000, 600_000, 700_000, 800_000, 900_000, 925_000,
              950_000, 975_000, 1_000_000, 1_007_500)
    return ("D", "C", "B", "BB", "BBB", "A", "AA", "AAA", "S", "SS", "SSS")[bisect_right(bounds, score)]


def rating_frame(value):
    """Return the existing HDD frame whose color represents the 2021 band."""
    return (1, 2, 3, 4, 5, 6, 7, 8, 9)[bisect_right((4, 7, 10, 12, 13, 14, Decimal("14.50"), 15), value)]


@dataclass(frozen=True)
class Simulation:
    b30: Decimal
    repeat_best: Decimal
    top_ten: Decimal
    difference: Decimal


def simulate(ratings):
    best = sorted(ratings, reverse=True)[:30]
    total = sum(best, ZERO)
    repeat = 10 * best[0] if best else ZERO
    top_ten = sum(best[:10], ZERO)
    return Simulation(total / 30, (total + repeat) / 40, (total + top_ten) / 40, (repeat - top_ten) / 40)


def select(snapshot, catalog, history, *, now=None):
    from .rating import eligible
    from ..best_rating import normalize_fc

    records = {}
    for record in snapshot.scores:
        key = record["id"], record["level_index"]
        if key in history and (key not in records or record["score"] > records[key]["score"]):
            records[key] = record
    items = []
    for (sid, difficulty), record in records.items():
        historical = history[sid, difficulty]
        song = catalog.songs.get(sid)
        chart = next((c for c in song.charts if c.difficulty == difficulty), None) if song else None
        if (chart is None or not eligible(song, chart, now)
                or chart_identity(song.attributes, chart.attributes) != historical.identity):
            continue
        score = record["score"]
        items.append(dict(id=sid, jacket_id=str(chart.jacket_id), song_name=historical.title,
                          level_index=difficulty, level_value=historical.constant,
                          score=score,
                          rank=score_rank(score), rating=chart_rating(historical.constant, score),
                          clear=record.get("clear"), full_combo=normalize_fc(record.get("full_combo"))))
    items.sort(key=lambda row: (-row["rating"], -row["score"], row["id"], row["level_index"]))
    return items[:30]
