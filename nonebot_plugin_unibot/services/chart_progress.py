"""Per-chart progress over the released CN catalog and one OAuth snapshot."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .rating import CN, eligible
from .song_model import Chart, Song

LEVELS = tuple(f"{level}{suffix}" for level in range(10, 16) for suffix in ("", "+"))
COMBO_TIERS = {"fullcombo": 1, "alljustice": 2, "alljusticecritical": 3}
CLEAR = {"clear", "hard", "brave", "absolute", "catastrophy"}
ZERO = Decimal(0)


@dataclass(frozen=True)
class ChartProgress:
    song: Song
    chart: Chart
    constant: Decimal
    record: dict | None

    @property
    def maximum(self):
        return (self.constant + 3) * 5

    @property
    def op(self):
        return Decimal(str(self.record["over_power"])) if self.record is not None else ZERO

    @property
    def tier(self):
        return COMBO_TIERS.get((self.record or {}).get("full_combo"), 0)

    @property
    def level(self):
        return self.chart.attributes.get("level")


def eligible_charts(catalog, scores):
    records = {(row["id"], row["level_index"]): row for row in scores}
    now = datetime.now(CN)
    return [
        ChartProgress(song, chart, Decimal(str(chart.attributes["level_value"])),
                      records.get((song.id, chart.difficulty)))
        for song in catalog.songs.values() for chart in song.charts if eligible(song, chart, now)
    ]


def master_ultima(rows):
    return [row for row in rows if row.chart.difficulty in (3, 4)]


def summarize(rows):
    records = [row.record for row in rows if row.record is not None]
    tiers = Counter(row.tier for row in rows)
    actual = sum((row.op for row in rows), ZERO)
    maximum = sum((row.maximum for row in rows), ZERO)
    return {
        "chart_count": len(rows),
        "played": len(records),
        "fc": tiers[1] + tiers[2] + tiers[3],
        "aj": tiers[2] + tiers[3],
        "ajc": tiers[3],
        "clear": sum(record.get("clear") in CLEAR for record in records),
        "sss": sum(record["score"] >= 1007500 for record in records),
        "sss_plus": sum(record["score"] >= 1009000 for record in records),
        "total_op": float(actual),
        "theoretical_op": float(maximum),
        "op_percent": float(100 * actual / maximum) if maximum else None,
    }


def profile_progress(player, rows):
    # The official percentage is already in percentage points. Show a catalog
    # ceiling estimate only when it agrees with that figure, as in the 4A sample.
    actual = player.get("over_power")
    percent = player.get("over_power_progress")
    official = {"total_op": actual, "op_percent": percent, "estimated_catalog_ceiling": None}
    if rows is None:
        return official, None, {level: None for level in LEVELS}

    maxima = {}
    for row in rows:
        maxima[row.song.id] = max(maxima.get(row.song.id, ZERO), row.maximum)
    ceiling = sum(maxima.values(), ZERO)
    agrees = (ceiling and actual is not None and percent is not None
              and abs(Decimal(str(actual)) * 100 / ceiling - Decimal(str(percent))) <= Decimal("0.05"))
    if agrees:
        official["estimated_catalog_ceiling"] = float(ceiling)
    purple = master_ultima(rows)
    levels = defaultdict(list)
    for row in purple:
        if row.level in LEVELS:
            levels[row.level].append(row)
    return official, summarize(purple), {level: summarize(levels[level]) for level in LEVELS}
