"""Select practice targets from actual CN chart records and one OAuth snapshot."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
import random
import re
from statistics import median
import unicodedata

from .job_store import check_render_cancelled
from .rating import CN, eligible
from .song_model import Chart, Song

MODES = {"score": "推分", "op": "推 OP", "fc": "FC", "aj": "AJ", "ajc": "AJC"}
COMBOS = {"": 0, "none": 0, "fullcombo": 1, "alljustice": 2, "alljusticecritical": 3}
COMBO_TARGETS = {"fc": (1, 975_000), "aj": (2, 1_004_000), "ajc": (3, 1_009_000)}
RANK_TARGETS = ((500_000, "C"), (600_000, "B"), (700_000, "BB"), (800_000, "BBB"),
                (900_000, "A"), (925_000, "AA"), (950_000, "AAA"), (975_000, "S"),
                (990_000, "S+"), (1_000_000, "SS"), (1_005_000, "SS+"),
                (1_007_500, "SSS"), (1_009_000, "SSS+"), (1_010_000, "AJC"))
USAGE = "用法：推什么 [推分 / 推OP / aj / ajc / fc] [等级或定数]，例如「推什么 14+ 推OP」。"


def parse_filter(value):
    """Accept one mode and one optional display level or exact constant."""
    tokens = unicodedata.normalize("NFKC", value).casefold().split()
    aliases = {"推分": "score", "推op": "op", "综合": "auto", **{key: key for key in COMBO_TARGETS}}
    mode, level = None, None
    for token in tokens:
        if token in aliases and mode is None:
            mode = aliases[token]
        elif level is None and re.fullmatch(r"[0-9]{1,2}(?:\+|\.[0-9])?", token):
            level = token
        else:
            raise ValueError(USAGE)
    return mode or "auto", level


@dataclass(frozen=True)
class Candidate:
    song: Song
    chart: Chart
    record: dict | None
    song_op: float | None
    chart_ceiling: float
    song_ceiling: float | None
    headroom: float | None
    target: int | None
    objective: str
    reason: str
    fit: float
    reach: float
    op_priority: float

    @property
    def key(self):
        return self.song.id, self.chart.difficulty

    def data(self, mode):
        reason = self.reason
        if mode == "op" and self.song_op is not None and (
                self.record is None or self.record["over_power"] < self.song_op):
            reason += f"本谱需超过同曲已记录的 {self.song_op:.2f} OP 才有有效提升。"
        return dict(song_id=self.song.id, title=self.song.attributes["title"], mode=mode,
                    level_index=self.chart.difficulty, level=self.chart.attributes.get("level"),
                    level_value=self.chart.attributes["level_value"],
                    score=self.record["score"] if self.record else None,
                    rating=self.record["rating"] if self.record else None,
                    current_chart_op=self.record["over_power"] if self.record else None,
                    current_song_op=self.song_op, theoretical_chart_op=self.chart_ceiling,
                    theoretical_song_op=self.song_ceiling, theoretical_effective_room=self.headroom,
                    predicted_effective_gain=None, target_score=self.target, objective=self.objective,
                    reason=reason)


def _neighbors(bands, constant):
    # Use one record per other song so playing multiple charts cannot supply the
    # entire evidence for an unplayed chart.
    nearby = {}
    for tenth in range(round(constant * 10) - 7, round(constant * 10) + 8):
        for other_id, other_constant, score, op_gap in bands.get(tenth, ()):
            distance = abs(other_constant - constant)
            if distance > .75:
                continue
            old = nearby.get(other_id)
            if old is None or (distance, -score) < old[:2]:
                nearby[other_id] = (distance, -score, op_gap)
    return sorted(nearby.items(), key=lambda pair: pair[1])[:9]


def _target(record, nearby, mode):
    observed = round(median(-row[1] for row in nearby)) if nearby else None
    distance = median(row[0] for row in nearby) if nearby else 1
    confidence = min(len(nearby) / 5, 1) * (1 - distance) if nearby else 0
    if record is None and len(nearby) < 3:
        return None
    if mode in COMBO_TARGETS:
        required, reference = COMBO_TARGETS[mode]
        if record:
            if "full_combo" not in record:
                return None
            combo = COMBOS.get((record.get("full_combo") or "").lower())
            if combo is None or combo >= required:
                return None
        baseline = record["score"] if record else observed
        if baseline < reference:
            return None
        marker = mode.upper()
        return (1_010_000 if mode == "ajc" else None, marker,
                f"已有 {baseline:,} 分的{'本谱' if record else '邻近定数'}记录，可尝试达成 {marker}。",
                .55 + .35 * confidence if record else .45 * confidence,
                .55 + .45 * min((baseline - reference) / 10_000, 1))
    if record is None:
        # A sight-read target is an observed band milestone. Never assume a
        # missing record starts at zero or can reach a theoretical maximum.
        target, rank = next(((points, label) for points, label in reversed(RANK_TARGETS[:-1])
                             if points <= observed), (None, None))
        if target is None:
            return None
        return target, rank, f"邻近定数有 {len(nearby)} 首已记录歌曲，中位分 {observed:,}，可先建立成绩。", .55 * confidence, .6
    score = record["score"]
    if score >= 1_010_000:
        return None
    milestone, rank = next((points, label) for points, label in RANK_TARGETS if points > score)
    # Break large jumps into practice steps. Near the top, prefer the next
    # achievable rank over a distant ceiling.
    step = max(500, min(5000, round((1_010_000 - score) / 8 / 100) * 100))
    if observed is not None and observed > score:
        step = min(step, max(500, observed - score))
    target = min(milestone, score + step)
    gap = target - score
    stretch = max(0, target - observed) if observed is not None else gap
    reach = 1 / (1 + gap / step + min(stretch / 10_000, 1) * confidence)
    ceiling_gap = 1_010_000 - score
    # A small gap near the ceiling is credible but has little room for progress.
    fit = (.65 + .25 * confidence) * (.60 + .40 * min(ceiling_gap / 1800, 1))
    fit *= .55 + .45 * min(score / 975_000, 1)
    reason = f"本谱最高 {score:,} 分，距目标 {gap:,} 分"
    if observed is not None:
        reason += f"，邻近定数中位 {observed:,}"
    return target, (rank if target == milestone else "提高最高分"), reason + "。", fit, reach


def candidates(snapshot, catalog, mode, level, stopped=None):
    records = {(row["id"], row["level_index"]): row for row in snapshot.scores}
    now = datetime.now(CN)
    charts = defaultdict(list)
    bands = defaultdict(list)
    for song in catalog.songs.values():
        check_render_cancelled(stopped)
        for chart in song.charts:
            constant = chart.attributes["level_value"]
            if not eligible(song, chart, now):
                continue
            charts[song.id].append(chart)
            record = records.get((song.id, chart.difficulty))
            if constant and record:
                gap = max(0, _ceiling(chart) - record["over_power"])
                bands[round(constant * 10)].append((song.id, constant, record["score"], gap))

    @lru_cache(maxsize=None)
    def neighbors(constant):
        return _neighbors(bands, constant)

    exact = Decimal(level) if level and "." in level else None
    for song in catalog.songs.values():
        check_render_cancelled(stopped)
        entries = charts.get(song.id, ())
        known = [chart for chart in entries if chart.attributes["level_value"]]
        if not known:
            continue
        song_ceiling = max(_ceiling(chart) for chart in known) if len(known) == len(entries) else None
        song_op = max((records[(song.id, chart.difficulty)]["over_power"] for chart in entries
                       if (song.id, chart.difficulty) in records), default=None)
        for chart in known:
            constant = chart.attributes["level_value"]
            if level and (Decimal(str(constant)) != exact if exact is not None
                          else str(chart.attributes.get("level")) != level):
                continue
            record = records.get((song.id, chart.difficulty))
            ceiling = _ceiling(chart)
            room = round(max(0, ceiling - song_op), 4) if song_op is not None else None
            nearby = [row for sid, row in neighbors(constant) if sid != song.id][:8]
            target = _target(record, nearby, mode)
            if target is None:
                continue
            points, objective, reason, fit, reach = target
            if record:
                # Account Rating is only a tie-breaker for a known record.
                player_rating = snapshot.player.get("rating")
                if player_rating:
                    fit += .08 * max(0, 1 - abs(record["rating"] - player_rating) / 4)
            # Use nearby observed gaps to favor charts where that standard of
            # play could beat the song's existing contribution. This is a
            # ranking signal only, since OP at the proposed target is unknown.
            op_priority = 0
            if room:
                op_priority = .35 * min(room / 8, 1)
                if len(nearby) >= 3:
                    observed_gap = median(row[2] for row in nearby)
                    op_priority += .65 * min(max(0, room - observed_gap) / 5, 1)
                if record and record["over_power"] < song_op:
                    deficit = song_op - record["over_power"]
                    op_priority *= 1 / (1 + deficit / 4)
            yield Candidate(song, chart, record, song_op, ceiling, song_ceiling, room,
                            points, objective, reason, fit, reach, op_priority)


def _ceiling(chart):
    return float((Decimal(str(chart.attributes["level_value"])) + 3) * 5)


def _weight(candidate, mode, picked, recent):
    fit, reach = candidate.fit, candidate.reach
    op_room = candidate.op_priority
    if mode == "op":
        score = fit * (.35 + .65 * op_room) * (.65 + .35 * reach)
    else:
        score = fit * (.55 + .45 * reach)
    if candidate.song.id in recent:
        score *= .55
    if picked:
        constant = candidate.chart.attributes["level_value"]
        distance = min(abs(constant - item.chart.attributes["level_value"]) for _, item in picked)
        different = all(candidate.chart.difficulty != item.chart.difficulty for _, item in picked)
        score *= 1 + .12 * min(distance, 1) + .08 * different
    return score


def select(snapshot, catalog, mode, level, previous=(), stopped=None, *, rng=random):
    """Sample a small high-ranked pool with one chart per song."""
    options = list(candidates(snapshot, catalog, mode, level, stopped))
    modes = ("score", "op") if mode == "auto" else (mode, mode)
    pools = {kind: [c for c in options if (kind != "op" or c.headroom)
                    and (kind != "score" or c.record is not None)] for kind in modes}
    picked = []
    used = set()
    recent = {song_id for song_id, _ in previous}
    # Assign the scarcest mode first so a flexible slot cannot take its only song.
    for kind in sorted(modes, key=lambda k: len({c.song.id for c in pools[k]})):
        check_render_cancelled(stopped)
        ranked = sorted(((_weight(c, kind, picked, recent), c) for c in pools[kind]
                         if c.song.id not in used), key=lambda pair: pair[0], reverse=True)
        if not ranked:
            continue
        pool = [pair for pair in ranked[:12] if pair[0] >= .60 * ranked[0][0]]
        if len(picked) == len(previous) - 1 and all(c.key in previous for _, c in picked):
            alternatives = [pair for pair in pool if pair[1].key not in previous]
            pool = alternatives or pool
        chosen = rng.choices([c for _, c in pool], weights=[weight ** 3 for weight, _ in pool])[0]
        picked.append((kind, chosen))
        used.add(chosen.song.id)
    return sorted(picked, key=lambda pair: modes.index(pair[0]))
