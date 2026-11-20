"""B30 and disjoint BEST30 + NEW20 presentations on one OAuth snapshot."""
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot import on_command
from PIL import Image, ImageDraw
import asyncio
import math
from typing import Any, Dict, List, Tuple
from .song import download_jacket
from .services.image_components import (
    DIFFICULTY_ASSETS, DIFFICULTY_COLORS, LEVELS, RESAMPLE, badge, brand, contain,
    cover, label, playerbar, png, rating_number, rating_pair, rounded, score_digits, ui, wrap,
)
from .services.job_store import check_render_cancelled

best_rating_cmd = on_command("b30", priority=5, block=True)
best_rating_50_cmd = on_command("b50", priority=5, block=True)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_chart_meta(songs: List[Dict[str, Any]]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    from .services.song_model import Catalog
    catalog = Catalog({"songs": songs}, legacy=True)
    meta = {}
    for song in catalog.songs.values():
        for chart in song.charts:
            diff = chart.attributes
            if chart.difficulty == 5:
                continue  # WORLD'S END has no standard rating constant.
            key = (str(song.id), chart.difficulty)
            if key in meta:
                raise ValueError("Ambiguous chart identity")
            meta[key] = dict(id=str(song.id), jacket_id=str(chart.jacket_id),
                song_name=song.attributes.get("title", ""), level=diff.get("level"),
                level_value=diff["level_value"], level_index=chart.difficulty,
                version=diff.get("version", song.attributes.get("version", 0)),
                song_version=song.attributes.get("version", 0))
    return meta


def normalize_fc(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {
        "fc": "fullcombo",
        "full combo": "fullcombo",
        "fullcombo": "fullcombo",
        "aj": "alljustice",
        "all justice": "alljustice",
        "alljustice": "alljustice",
        "ajc": "alljusticecritical",
        "all justice critical": "alljusticecritical",
        "alljusticecritical": "alljusticecritical",
    }.get(text, text)


def rank_to_display(rank: Any) -> str:
    value = str(rank or "").strip()
    return {
        "sssp": "SSS+",
        "sss": "SSS",
        "ssp": "SS+",
        "ss": "SS",
        "sp": "S+",
    }.get(value.lower(), value.upper())


def score_to_rank(score: int) -> str:
    if score >= 1009000:
        return "SSS+"
    if score >= 1007500:
        return "SSS"
    if score >= 1005000:
        return "SS+"
    if score >= 1000000:
        return "SS"
    if score >= 990000:
        return "S+"
    if score >= 975000:
        return "S"
    if score >= 950000:
        return "AAA"
    if score >= 925000:
        return "AA"
    if score >= 900000:
        return "A"
    return "B"


def build_item(meta: Dict[str, Any], score_row: Dict[str, Any]) -> Dict[str, Any]:
    score = safe_int(score_row.get("score"), 0)
    rating = safe_float(score_row.get("rating"), 0.0)
    return {
        **meta,
        "score": score,
        "rating": rating,
        "rank": rank_to_display(score_row.get("rank")) or score_to_rank(score),
        "clear": score_row.get("clear") or "",
        "full_combo": normalize_fc(score_row.get("full_combo")),
        "full_chain": score_row.get("full_chain"),
    }


async def prepare_jackets(items: List[Dict[str, Any]]) -> Dict[str, Image.Image]:
    jacket_ids = sorted({str(item["jacket_id"]) for item in items if item.get("jacket_id")})
    numeric_ids = [jacket_id for jacket_id in jacket_ids if jacket_id.isdigit()]
    tasks = [download_jacket(safe_int(jacket_id)) for jacket_id in numeric_ids]
    images = await asyncio.gather(*tasks) if tasks else []
    return {jacket_id: image for jacket_id, image in zip(numeric_ids, images) if image is not None}


INK = "#20273e"
MUTED = "#45546a"
PAPER = "#ffffff"
CLOUD = "#eff6f9"
LINE = "#d1e1e5"
CARD_WIDTH, CARD_HEIGHT, GAP = 218, 384, 10
COLUMNS = 5
WIDTH = COLUMNS * (CARD_WIDTH + GAP) - GAP + 24


def _card(item, number, jacket, title=None):
    width = CARD_WIDTH
    title = title if title is not None else wrap(item["song_name"], 24, width - 24)
    offset = (len(title) - 1) * 30
    height = CARD_HEIGHT + offset
    card = Image.new("RGB", (width, height), CLOUD)
    rounded(card, (0, 0, width - 1, height - 1), 16, PAPER, LINE)
    side = width - 12
    if jacket is None:
        label(card, (6, 6, width - 6, side + 6), "无曲绘", 24, MUTED, "center")
    else:
        mask = Image.new("L", (side, side))
        rounded(mask, (0, 0, side - 1, side - 1), 11, 255)
        card.paste(cover(jacket, side), (6, 6), mask)
    rounded(card, (10, 10, 60, 39), 12, INK)
    label(card, (10, 10, 60, 39), f"{number:02}", 23, PAPER, "center")
    badge(card, (width - 100, 8, width - 10, 38), "rank", item["rank"])

    difficulty = item["level_index"]
    band = ui("difficulty", DIFFICULTY_ASSETS[difficulty] + "-band")
    if band is None:
        rounded(card, (6, side + 6, width - 6, side + 37), 4, DIFFICULTY_COLORS[difficulty])
    else:
        band = band.resize((side, 31), RESAMPLE)
        card.paste(band, (6, side + 6), band)
    rounded(card, (9, side + 6, 121, side + 32), 10, INK)
    label(card, (10, side + 4, 120, side + 30), LEVELS[difficulty], 19, PAPER, "center")
    rounded(card, (width - 64, side + 6, width - 10, side + 32), 9, INK)
    label(card, (width - 64, side + 4, width - 10, side + 30), f"{item['level_value']:.1f}", 20, PAPER, "center")
    for index, line in enumerate(title):
        top = 247 + index * 30
        label(card, (11, top, width - 10, top + 30), line, 24, INK, fit=True)
    score_digits(card, (10, 280 + offset, width - 10, 322 + offset), item["score"])
    badge(card, (7, 324 + offset, width // 2 - 2, 350 + offset), "clear", item["clear"])
    badge(card, (width // 2 + 2, 324 + offset, width - 7, 350 + offset), "combo", item["full_combo"])
    rounded(card, (7, 353 + offset, width - 8, height - 6), 10, "#e7f1f4")
    rating_pair(card, (7, 353 + offset, width - 7, height - 5), item["rating"], 4,
                gap=8, group="chart-rating", centered=True)
    return card


def _draw_ranking(player, player_assets, jacket_images, groups, heading, value, stopped):
    width = WIDTH
    layouts = []
    for _, limit, items, _, _ in groups:
        titles = []
        for item in items:
            check_render_cancelled(stopped)
            titles.append(wrap(item["song_name"], 24, CARD_WIDTH - 24))
        strides = []
        for start in range(0, math.ceil(limit / COLUMNS) * COLUMNS, COLUMNS):
            lines = max((len(title) for title in titles[start:start + COLUMNS]), default=1)
            strides.append(CARD_HEIGHT + GAP + (lines - 1) * 30)
        layouts.append((titles, strides))
    height = 260 + sum(76 + sum(strides) for _, strides in layouts)
    canvas = Image.new("RGB", (width, height), CLOUD)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, 246), fill="#fff7dc")
    draw.polygon(((int(width * .58), 0), (width, 0), (width, 246), (int(width * .38), 246)), fill="#e6f4f5")
    draw.polygon(((int(width * .86), 0), (width, 0), (width, 246), (int(width * .66), 246)), fill="#f4e8f6")
    draw.line((0, 246, width, 246), fill=LINE, width=2)
    contain(canvas, playerbar(player, player_assets), (15, 8, 683, 238))
    brand(canvas, width - 162, 3, INK)
    label(canvas, (685, 55, width - 20, 89), heading, 24, MUTED, "right", fit=True)
    rating_number(canvas, (width - 267, 95, width - 19, 151), value, 4)
    y = 250
    for (title, _, items, mean, color), (titles, strides) in zip(groups, layouts):
        rounded(canvas, (15, y + 15, 23, y + 57), 4, color)
        label(canvas, (36, y + 5, 330, y + 66), title, 39, INK)
        label(canvas, (335, y + 5, width - 17, y + 66), f"均值 {mean:.4f}", 27, MUTED, "right")
        y += 76
        for row, stride in enumerate(strides):
            for i in range(row * COLUMNS, min((row + 1) * COLUMNS, len(items))):
                check_render_cancelled(stopped)
                item = items[i]
                card = _card(item, i + 1, jacket_images.get(item["jacket_id"]), titles[i])
                canvas.paste(card, (12 + i % COLUMNS * (CARD_WIDTH + GAP), y))
            y += stride
    check_render_cancelled(stopped)
    return png(canvas)


def draw_b30_image(player, items, jacket_images, player_assets, average, stopped=None):
    groups = (("BEST30", 30, items, average, "#ffcc59"),)
    return _draw_ranking(player, player_assets, jacket_images, groups, "B30", average, stopped)


def draw_b50_image(player, best_items, new_items, jacket_images, player_assets, best_mean, new_mean, estimate, stopped=None):
    groups = (("BEST30", 30, best_items, best_mean, "#ffcc59"),
              ("NEW20", 20, new_items, new_mean, "#74d5cd"))
    heading = f"B50（{best_mean:.4f}(B30) + {new_mean:.4f}(N20)）"
    return _draw_ranking(player, player_assets, jacket_images, groups, heading, estimate, stopped)


@best_rating_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "player.best", {}, origin="command")
    await send_command_result(best_rating_cmd, result, finish=True)


@best_rating_50_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "player.b50", {}, origin="command")
    await send_command_result(best_rating_50_cmd, result, finish=True)
