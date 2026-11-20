import asyncio
from collections import OrderedDict
import time

import regex

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, artwork, frame
from .services.image_components import BADGES, badge, label, measure, png, rounded, score_digits, wrap
from .services.job_store import async_render, check_render_cancelled
from .services.rating import current_version
from .services.recommendation import MODES, RANK_TARGETS, parse_filter, select
from .services.results import image, outcome
from .services.song_images import COMPACT_LEVEL_HEIGHT, compact_difficulty
from .song import download_jacket


recommend_cmd = on_regex(r"^推什么\s*(.+)?$", priority=5, block=True)
_recent = OrderedDict()
TARGET_COMBOS = {"FC": "fullcombo", "AJ": "alljustice", "AJC": "alljusticecritical"}
TARGET_RANKS = dict(RANK_TARGETS[:-1])
STATUS_WIDTH, STATUS_HEIGHT, STATUS_STEP = 112, 22, 25


def remember(scope, mode, level, rows):
    key = (scope, mode, level)
    _recent[key] = time.monotonic(), tuple((row["song_id"], row["level_index"]) for row in rows)
    _recent.move_to_end(key)
    if len(_recent) > 64:
        _recent.popitem(last=False)


def _op(value):
    return "未知" if value is None else f"{value:.2f}"


def _draw_target(canvas, x, top, candidate, *, combo_badge=True):
    score, objective = candidate.target, candidate.objective
    combo = TARGET_COMBOS.get(objective)
    rank = TARGET_RANKS.get(score)
    heading = "连击目标" if score is None else "目标成绩"
    if (combo and not combo_badge) or (not combo and rank != objective):
        heading += f" · {objective}"
    label(canvas, (x, top, x + 328, top + 29), heading, 22, TEAL)
    if score is not None:
        score_digits(canvas, (x, top + 33, x + 220, top + 77), score)
    if combo and combo_badge:
        left = x if score is None else x + 231
        badge(canvas, (left, top + 39, x + 328, top + 72), "combo", combo)
    elif rank == objective:
        badge(canvas, (x + 231, top + 41, x + 328, top + 70), "rank", objective)


def _wrap_title(name):
    width = 550
    for size in (33, 31, 29):
        rows = wrap(name, size, width)
        if len(rows) <= 3 and (len(rows) == 1 or measure(rows[-1], size) >= width * .25):
            break
    if len(rows) > 1 and measure(rows[-1], size) < width * .25:
        # Use the original separator so Latin words and adjacent CJK stay intact.
        normalized = " ".join(name.split())
        characters = [match.start() for match in regex.finditer(r"\S", normalized)]
        boundary = sum(len("".join(row.split())) for row in rows[:-1])
        while measure(rows[-1], size) < width * .25:
            prefix = rows[-2]
            match = regex.search(r"[\p{Latin}\p{N}]+$|\X$", prefix)
            if not match or not prefix[:match.start()].rstrip():
                break
            start = boundary - len(match[0])
            tail = normalized[characters[start]:]
            if measure(tail, size) > width:
                break
            rows[-2:] = [prefix[:match.start()].rstrip(), tail]
            boundary = start
    return rows, size


def _statuses(record):
    if record is None:
        return []
    badges = [("rank", record["rank"])] if record.get("rank") else []
    badges.extend((group, record[field]) for group, field in (("clear", "clear"), ("combo", "full_combo"))
                  if (record.get(field) or "").strip().lower() in BADGES[group])
    return badges


def _draw_current(canvas, top, record, statuses):
    if record is None:
        label(canvas, (49, top + 5, 377, top + 80), "落雪无本谱成绩", 25, MUTED)
        return
    label(canvas, (49, top + 5, 269, top + 34), "当前成绩", 22, MUTED)
    score_digits(canvas, (49, top + 36, 269, top + 82), record["score"])
    status_x = 280
    for index, (group, value) in enumerate(statuses):
        status_y = top + 36 + index * STATUS_STEP
        badge(canvas, (status_x, status_y, status_x + STATUS_WIDTH, status_y + STATUS_HEIGHT), group, value)


def render_recommendation_image(picks, jackets, stopped=None):
    cards = []
    gap, padding = 12, 13
    for mode, candidate in picks:
        check_render_cancelled(stopped)
        title, size = _wrap_title(candidate.song.attributes["title"])
        step = size + 8
        head = max(168, 43 + step * len(title) + COMPACT_LEVEL_HEIGHT + 10)
        statuses = _statuses(candidate.record)
        status_bottom = 36 + max(0, len(statuses) - 1) * STATUS_STEP + STATUS_HEIGHT
        panel = max(96, status_bottom + 8)
        body = panel
        if mode == "score" and candidate.score_target:
            body += 34
        height = head + body + padding
        cards.append((mode, candidate, title, size, step, head, statuses, panel, height))

    show_op_note = any(card[0] == "op" for card in cards)
    canvas = frame("推什么", 83 + sum(card[-1] + gap for card in cards) + (38 if show_op_note else 4))
    y = 83
    for mode, candidate, title, size, step, head, statuses, panel, height in cards:
        check_render_cancelled(stopped)
        rounded(canvas, (20, y, 780, y + height), 16, PAPER, LINE)
        artwork(canvas, jackets.get(candidate.song.id), (36, y + 17, 176, y + 157))
        label(canvas, (193, y + 10, 745, y + 43), MODES[mode], 24, TEAL)
        top = y + 43
        for row in title:
            label(canvas, (193, top, 751, top + step), row, size, INK, fit=True)
            top += step
        compact_difficulty(canvas, 193, top, candidate.chart.difficulty, candidate.chart.attributes)

        top = y + head
        rounded(canvas, (36, top, 764, top + panel), 10, "#eff6f6")
        _draw_current(canvas, top, candidate.record, statuses)
        if mode == "op":
            for x, caption, value in (
                (405, "当前歌曲有效 OP", candidate.song_op),
                (586, "本谱理论有效空间", candidate.headroom),
            ):
                label(canvas, (x, top + 10, x + 171, top + 35), caption, 22, MUTED, fit=True)
                label(canvas, (x, top + 40, x + 171, top + 81), _op(value), 34, TEAL)
        else:
            if candidate.record is not None and candidate.target is not None:
                diff = candidate.target - candidate.record["score"]
                if diff > 0:
                    label(canvas, (619, top + 5, 748, top + 34), f"还差 {diff:,} 分", 22, TEAL, "right")
            _draw_target(canvas, 405, top + 5, candidate, combo_badge=mode in {"fc", "aj", "ajc"})
        if mode == "score" and candidate.score_target:
            gain = f"{candidate.score_target[2]:.9f}".rstrip("0").rstrip(".")
            label(canvas, (49, top + panel + 5, 750, top + panel + 33),
                  f"达成后预计 Rating +{gain}", 22, TEAL, fit=True)
        y += height + gap
    if show_op_note:
        label(canvas, (28, y - 4, 772, y + 30), "同曲取最高 OP · 理论空间是上界，实绩以落雪为准", 22, MUTED)
    check_render_cancelled(stopped)
    return png(canvas)


async def recommend_chart(snapshot, catalog, mode, level, scope):
    timestamp, previous = _recent.get((scope, mode, level), (0, ()))
    picks = await async_render(select, snapshot, catalog, mode, level,
                               previous if time.monotonic() - timestamp < 300 else ())
    if not picks:
        if mode == "score":
            try:
                current_version(catalog)
            except ValueError:
                return outcome("RATING_VERSION_UNAVAILABLE", "当前曲库缺少国服版本信息，暂时无法计算 Rating 增量。",
                               status="unavailable")
            return outcome("NOT_FOUND", "当前筛选范围内没有能在合理练习幅度内提高总 Rating 的谱面。",
                           status="needs_input")
        return outcome("NOT_FOUND", "没有符合筛选条件且有足够成绩依据的谱面。"
                       "可以放宽筛选，或先在落雪记录几首同定数附近的成绩。", status="needs_input")
    song_ids = [candidate.song.id for _, candidate in picks]
    jackets = dict(zip(song_ids, await asyncio.gather(*(download_jacket(sid) for sid in song_ids))))
    content = await async_render(render_recommendation_image, picks, jackets)
    return outcome(data={"mode": mode, "level": level,
                         "recommendations": [candidate.data(kind) for kind, candidate in picks]},
                   artifacts=[image(content)])


@recommend_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    from .services.media_jobs import submit
    from .services.results import send_command_result

    try:
        filters = event.get_plaintext().strip()[3:].strip()
        parse_filter(filters)
    except ValueError as error:
        await send_command_result(recommend_cmd, outcome("INVALID_ARGUMENTS", str(error), status="needs_input"),
                                  finish=True)
        return
    result = await submit(bot, event, "player.recommend", {"filter": filters}, origin="command")
    await send_command_result(recommend_cmd, result, finish=True)
