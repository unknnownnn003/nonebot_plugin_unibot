import asyncio
from collections import OrderedDict
import time

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, artwork, frame
from .services.image_components import badge, label, png, rounded, score_digits, wrap
from .services.job_store import async_render, check_render_cancelled
from .services.recommendation import MODES, RANK_TARGETS, parse_filter, select
from .services.results import image, outcome
from .services.song_images import _difficulty, _level
from .song import download_jacket


recommend_cmd = on_regex(r"^推什么\s*(.+)?$", priority=5, block=True)
_recent = OrderedDict()
TARGET_COMBOS = {"FC": "fullcombo", "AJ": "alljustice", "AJC": "alljusticecritical"}
TARGET_RANKS = dict(RANK_TARGETS[:-1])


def remember(scope, mode, level, rows):
    key = (scope, mode, level)
    _recent[key] = time.monotonic(), tuple((row["song_id"], row["level_index"]) for row in rows)
    _recent.move_to_end(key)
    if len(_recent) > 64:
        _recent.popitem(last=False)


def _op(value):
    return "未知" if value is None else f"{value:.2f}"


def _draw_target(canvas, x, top, candidate):
    score, objective = candidate.target, candidate.objective
    combo = TARGET_COMBOS.get(objective)
    rank = TARGET_RANKS.get(score)
    heading = "连击目标" if score is None else "目标成绩"
    if not combo and rank != objective:
        heading += f" · {objective}"
    label(canvas, (x, top, x + 328, top + 29), heading, 22, TEAL)
    if score is not None:
        score_digits(canvas, (x, top + 33, x + 220, top + 77), score)
    if combo:
        left = x if score is None else x + 231
        badge(canvas, (left, top + 39, x + 328, top + 72), "combo", combo)
    elif rank == objective:
        badge(canvas, (x + 231, top + 41, x + 328, top + 70), "rank", objective)


def render_recommendation_image(picks, jackets, stopped=None):
    cards = []
    for mode, candidate in picks:
        check_render_cancelled(stopped)
        name = candidate.song.attributes["title"]
        size = 33
        title = wrap(name, size, 550)
        if len(title) > 3:
            size = 29
            title = wrap(name, size, 550)
        # The compact LEVEL board uses 73 px below the last title line.
        head = max(168, 43 + 41 * len(title) + 73 + 10)
        cards.append((mode, candidate, title, size, head))

    show_op_note = any(mode == "op" for mode, _, _, _, _ in cards)
    body_height, gap = 109, 12
    canvas = frame("推什么", 83 + sum(card[-1] + body_height + gap for card in cards) + (38 if show_op_note else 4))
    y = 83
    for mode, candidate, title, size, head in cards:
        check_render_cancelled(stopped)
        height = head + body_height
        rounded(canvas, (20, y, 780, y + height), 16, PAPER, LINE)
        artwork(canvas, jackets.get(candidate.song.id), (36, y + 17, 176, y + 157))
        label(canvas, (193, y + 10, 745, y + 43), MODES[mode], 24, TEAL)
        top = y + 43
        for row in title:
            label(canvas, (193, top, 751, top + 41), row, size, INK, fit=True)
            top += 41
        _difficulty(canvas, 193, top + 14, candidate.chart.difficulty, compact=True)
        _level(canvas, 389, top, candidate.chart.attributes, compact=True)

        top = y + head
        rounded(canvas, (36, top, 764, top + 96), 10, "#eff6f6")
        if mode == "op":
            _draw_target(canvas, 49, top + 5, candidate)
            if candidate.record is None:
                label(canvas, (211, top + 5, 377, top + 34), "落雪无本谱成绩", 20, MUTED, "right")
            for x, caption, value in (
                (405, "当前歌曲有效 OP", candidate.song_op),
                (586, "本谱理论有效空间", candidate.headroom),
            ):
                label(canvas, (x, top + 10, x + 171, top + 35), caption, 22, MUTED, fit=True)
                label(canvas, (x, top + 40, x + 171, top + 81), _op(value), 34, TEAL)
        else:
            label(canvas, (49, top + 5, 372, top + 34), "当前成绩", 22, MUTED)
            if candidate.record is None:
                label(canvas, (49, top + 38, 372, top + 83), "落雪无本谱成绩", 26, INK)
            else:
                score = candidate.record["score"]
                score_digits(canvas, (49, top + 38, 269, top + 82), score)
                badge(canvas, (280, top + 46, 377, top + 75), "rank", candidate.record.get("rank"))
                if candidate.target is not None and candidate.target > score:
                    label(canvas, (619, top + 5, 748, top + 34),
                          f"还差 {candidate.target - score:,} 分", 22, TEAL, "right")
            _draw_target(canvas, 405, top + 5, candidate)
        y += height + gap
    if show_op_note:
        label(canvas, (28, y - 4, 772, y + 30), "同曲取最高 OP · 理论空间是上界，实际提升未知", 22, MUTED)
    check_render_cancelled(stopped)
    return png(canvas)


async def recommend_chart(snapshot, catalog, mode, level, scope):
    timestamp, previous = _recent.get((scope, mode, level), (0, ()))
    picks = await async_render(select, snapshot, catalog, mode, level,
                               previous if time.monotonic() - timestamp < 300 else ())
    if not picks:
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
