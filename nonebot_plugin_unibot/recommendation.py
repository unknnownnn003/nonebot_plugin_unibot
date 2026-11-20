import asyncio
from collections import OrderedDict
import time

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, artwork, frame, lines
from .services.image_components import badge, label, png, rounded, score_digits, wrap
from .services.job_store import async_render, check_render_cancelled
from .services.recommendation import MODES, parse_filter, select
from .services.results import image, outcome
from .services.song_images import _difficulty
from .song import download_jacket


recommend_cmd = on_regex(r"^推什么\s*(.+)?$", priority=5, block=True)
_recent = OrderedDict()


def remember(scope, mode, level, rows):
    key = (scope, mode, level)
    _recent[key] = time.monotonic(), tuple((row["song_id"], row["level_index"]) for row in rows)
    _recent.move_to_end(key)
    if len(_recent) > 64:
        _recent.popitem(last=False)


def _op(value):
    return "未知" if value is None else f"{value:.2f}"


def render_recommendation_image(picks, jackets, stopped=None):
    cards = []
    for mode, candidate in picks:
        check_render_cancelled(stopped)
        data = candidate.data(mode)
        size = 33
        title = wrap(data["title"], size, 504)
        if len(title) >= 4:
            size = 29
            title = wrap(data["title"], size, 504)
        reason = wrap(data["reason"], 23, 704)
        head = max(162, 89 + 42 * len(title))
        cards.append((mode, candidate, data, title, size, reason, head, head + 224 + len(reason) * 31))

    canvas = frame("推什么", 143 + sum(card[-1] + 12 for card in cards))
    y = 83
    for mode, candidate, data, title, size, reason, head, height in cards:
        check_render_cancelled(stopped)
        rounded(canvas, (20, y, 780, y + height), 16, PAPER, LINE)
        artwork(canvas, jackets.get(data["song_id"]), (36, y + 17, 176, y + 157))
        label(canvas, (193, y + 10, 745, y + 43), MODES[mode], 24, TEAL)
        top = lines(canvas, title, 193, y + 43, 750, size, 42, INK)
        _difficulty(canvas, 193, top, data["level_index"], compact=True)
        label(canvas, (390, top, 746, top + 43), f"{data['level']}  ·  定数 {data['level_value']:.1f}", 25, INK)
        top = y + head + 9
        rounded(canvas, (36, top, 764, top + 96), 10, "#eff6f6")
        label(canvas, (49, top + 3, 367, top + 32), "当前成绩", 22, MUTED)
        if data["score"] is None:
            label(canvas, (49, top + 33, 365, top + 84), "落雪无成绩记录", 28, INK)
        else:
            score_digits(canvas, (47, top + 35, 272, top + 84), data["score"])
            badge(canvas, (288, top + 37, 382, top + 82), "rank", candidate.record.get("rank"))
        label(canvas, (403, top + 3, 748, top + 32), "目标  " + data["objective"], 24, TEAL)
        if data["target_score"] is not None:
            score_digits(canvas, (401, top + 35, 634, top + 84), data["target_score"])
        else:
            label(canvas, (403, top + 34, 749, top + 82), "达成连击标记", 28, INK)
        top += 104
        for index, (caption, value) in enumerate((
            ("当前谱面 OP", data["current_chart_op"]), ("当前歌曲 OP", data["current_song_op"]),
            ("本谱理论上限", data["theoretical_chart_op"]), ("本谱有效理论空间", data["theoretical_effective_room"]),
        )):
            x = 45 + index % 2 * 359
            line = top + index // 2 * 36
            label(canvas, (x, line, x + 344, line + 35), f"{caption}  {_op(value)}", 23, INK)
        lines(canvas, reason, 45, top + 77, 749, 23, 31, MUTED)
        y += height + 12
    label(canvas, (28, y, 772, y + 43), "同曲取最高 OP · 理论空间为上界 · 实际提升未知", 23, MUTED, fit=True)
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
