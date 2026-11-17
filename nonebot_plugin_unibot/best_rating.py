"""B30 and disjoint BEST30 + NEW20 presentations on one OAuth snapshot."""
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot import on_command
from PIL import Image, ImageDraw, ImageFont
import asyncio
import io
import math
import platform
import os
from typing import Any, Dict, List, Optional, Tuple
from .song import download_jacket

best_rating_cmd = on_command("b30", priority=5, block=True)
best_rating_50_cmd = on_command("b50", priority=5, block=True)


BG_TOP = (26, 28, 40)
BG_BOTTOM = (38, 31, 58)
PANEL = (34, 38, 54, 238)
PANEL_2 = (42, 46, 64, 238)
LINE = (98, 114, 164, 150)
TEXT = (248, 248, 242)
TEXT_SUB = (191, 191, 191)
CYAN = (139, 233, 253)
GREEN = (80, 250, 123)
PINK = (255, 121, 198)
PURPLE = (189, 147, 249)
YELLOW = (241, 250, 140)
ORANGE = (255, 184, 108)

def get_font(size: int, weight: str = "Normal") -> ImageFont.FreeTypeFont:
    weight = "Regular" if weight == "Normal" else weight
    try:
        if platform.system() == "Windows":
            return ImageFont.truetype("msyh.ttc", size)
        for path in [
            f"/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-{weight}.otf",
            f"/usr/share/fonts/truetype/custom/SourceHanSansSC-{weight}.otf",
            f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    except Exception:
        pass
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def clamp_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    text = str(text or "")
    if text_width(draw, text, font) <= max_width:
        return text
    suffix = "..."
    while text and text_width(draw, text + suffix, font) > max_width:
        text = text[:-1]
    return text + suffix if text else suffix


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


def format_score(score: int) -> str:
    s = f"{max(score, 0):07d}"
    return f"{s[:3]},{s[3:]}"


def format_score_plain(score: int) -> str:
    return f"{max(score, 0):07d}"


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


def section_average(items: List[Dict[str, Any]], divisor: int) -> float:
    from .services.rating import average
    return average(items[:divisor], divisor) if divisor else 0.0


async def prepare_jackets(items: List[Dict[str, Any]]) -> Dict[str, Image.Image]:
    jacket_ids = sorted({str(item["jacket_id"]) for item in items if item.get("jacket_id")})
    numeric_ids = [jacket_id for jacket_id in jacket_ids if jacket_id.isdigit()]
    tasks = [download_jacket(safe_int(jacket_id)) for jacket_id in numeric_ids]
    images = await asyncio.gather(*tasks) if tasks else []
    return {jacket_id: image for jacket_id, image in zip(numeric_ids, images) if image is not None}


def make_background(width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), BG_TOP)
    px = img.load()
    for y in range(height):
        for x in range(width):
            t = (x / width * 0.42) + (y / height * 0.58)
            px[x, y] = tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(-height, width, 240):
        draw.line([(i, height), (i + height, 0)], fill=(98, 114, 164, 34), width=5)
    for y in range(0, height, 220):
        draw.rectangle([0, y, width, y + 1], fill=(98, 114, 164, 42))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def draw_shadowed_panel(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], radius: int, fill, outline=None):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 8, y1 + 8, x2 + 8, y2 + 8), radius=radius, fill=(0, 0, 0, 80))
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2 if outline else 1)


def draw_section_title(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, text: str, color: Tuple[int, int, int]) -> None:
    font = get_font(36, "Bold")
    draw.rounded_rectangle((x, y, x + width, y + 64), radius=6, fill=PANEL, outline=LINE, width=2)
    draw.rounded_rectangle((x + 8, y + 8, x + width - 8, y + 56), radius=4, fill=color)
    tw = text_width(draw, text, font)
    draw.text((x + (width - tw) / 2, y + 11), text, font=font, fill=TEXT, stroke_width=1, stroke_fill=BG_TOP)


def draw_badge(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], text: str, font: ImageFont.FreeTypeFont, fill, text_fill=BG_TOP, radius: int = 7) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)
    tw = text_width(draw, text, font)
    x1, y1, x2, _ = box
    draw.text((x1 + (x2 - x1 - tw) / 2, y1 + 1), text, font=font, fill=text_fill)


def draw_score_card(base: Image.Image, item: Dict[str, Any], rank_no: int, x: int, y: int, jacket: Optional[Image.Image], card_w: int, card_h: int) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_tiny = get_font(13, "Bold")
    font_name = get_font(16, "Bold")
    font_score = get_font(28, "Normal")
    font_rank = get_font(25, "Bold")
    font_badge = get_font(11, "Bold")
    draw.rounded_rectangle((x + 4, y + 4, x + card_w + 4, y + card_h + 4), radius=8, fill=(0, 0, 0, 90))
    draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=8, fill=PANEL_2, outline=LINE, width=1)
    base.alpha_composite(overlay)

    jacket_size = card_h - 10
    jacket_x = x + 5
    jacket_y = y + 5
    if jacket:
        base.alpha_composite(jacket.resize((jacket_size, jacket_size), Image.Resampling.LANCZOS), (jacket_x, jacket_y))
    else:
        base.alpha_composite(Image.new("RGBA", (jacket_size, jacket_size), (68, 71, 90, 255)), (jacket_x, jacket_y))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    info_x = x + jacket_size + 14
    info_y = y + 6
    status_w = 44
    status_x = x + card_w - status_w - 8
    level_text = f"{item['level_value']:.1f}" if item.get("level_value") else str(item.get("level") or "-")
    diff_color = {0: GREEN, 1: YELLOW, 2: (255, 85, 85), 3: PURPLE, 4: TEXT, 5: ORANGE}.get(item["level_index"], CYAN)
    draw_badge(draw, (info_x, info_y, info_x + 36, info_y + 18), f"#{rank_no}", font_tiny, PURPLE, radius=6)
    draw_badge(draw, (x + card_w - 112, info_y, x + card_w - 60, info_y + 18), level_text, font_tiny, diff_color, radius=6)
    draw_badge(draw, (status_x + 2, info_y, x + card_w - 8, info_y + 18), f"{item['rating']:.2f}", font_tiny, CYAN, radius=6)
    draw.text((info_x, y + 31), clamp_text(draw, item["song_name"], font_name, card_w - jacket_size - 28), font=font_name, fill=TEXT)
    draw.text((info_x, y + 54), format_score(item["score"]), font=font_score, fill=TEXT)
    draw.text((info_x, y + 88), str(item.get("rank") or ""), font=font_rank, fill=CYAN, stroke_width=1, stroke_fill=BG_TOP)

    fc = str(item.get("full_combo") or "").lower()
    clear = str(item.get("clear") or "").lower()
    label_map = {"fullcombo": "FC", "alljustice": "AJ", "alljusticecritical": "AJC"}
    clear_map = {"clear": "CLR", "hard": "HRD", "absolute": "ABS", "brave": "BRV"}
    clear_label = clear_map.get(clear, clear[:3].upper()) if clear else ""
    if fc and clear_label:
        draw_badge(draw, (status_x - 38, y + 102, status_x - 2, y + 118), label_map.get(fc, fc[:3].upper()), font_badge, YELLOW, radius=5)
        draw_badge(draw, (status_x, y + 102, x + card_w - 8, y + 118), clear_label, font_badge, ORANGE, radius=5)
    elif fc:
        draw_badge(draw, (status_x, y + 102, x + card_w - 8, y + 118), label_map.get(fc, fc[:3].upper()), font_badge, YELLOW, radius=5)
    elif clear_label:
        draw_badge(draw, (status_x, y + 102, x + card_w - 8, y + 118), clear_label, font_badge, ORANGE, radius=5)
    base.alpha_composite(overlay)


def draw_b30_header(img: Image.Image, player: Dict[str, Any], b30_avg: float) -> None:
    draw = ImageDraw.Draw(img)
    font_title = get_font(34, "Bold")
    font_head = get_font(32, "Bold")
    font_body = get_font(23, "Normal")
    font_small = get_font(19, "Normal")

    draw_shadowed_panel(draw, (40, 38, 640, 206), 8, PANEL, LINE)
    name = player.get("UserName") or player.get("name") or "Unknown"
    from .services.player_data import player_level
    level = player_level(player)
    draw.text((70, 58), clamp_text(draw, name, font_title, 520), font=font_title, fill=TEXT)
    draw.text((70, 112), f"Lv.{level}", font=font_body, fill=TEXT_SUB)
    draw.text((70, 154), f"B30 Mean {b30_avg:.4f}", font=font_head, fill=GREEN)

    draw_shadowed_panel(draw, (680, 38, 1040, 206), 8, PANEL, LINE)
    draw.text((732, 74), "CHUNITHM B30", font=font_head, fill=CYAN)
    draw.text((738, 124), "Lxns OAuth | CN charts", font=font_body, fill=TEXT_SUB)

    draw_shadowed_panel(draw, (1080, 38, 1560, 206), 8, PANEL, LINE)
    draw.text((1112, 78), "All versions / top 30", font=font_head, fill=GREEN)
    draw.text((1112, 132), "Not total player Rating", font=font_body, fill=TEXT_SUB)
    draw.text((1112, 166), "generated by Robinbot", font=font_small, fill=TEXT_SUB)


def draw_b30_image(
    player: Dict[str, Any],
    b30_items: List[Dict[str, Any]],
    jacket_images: Dict[str, Image.Image],
) -> bytes:
    width = 1600
    card_w = 284
    card_h = 124
    gap_x = 22
    gap_y = 18
    cols = 5
    margin_x = 40
    section_w = width - margin_x * 2
    rows = math.ceil(max(len(b30_items), 1) / cols)
    top = 244
    section_title_h = 84
    height = top + section_title_h + rows * (card_h + gap_y) + 70

    img = make_background(width, height)
    draw = ImageDraw.Draw(img)
    b30_avg = section_average(b30_items, 30)
    draw_b30_header(img, player, b30_avg)

    y = top
    draw_section_title(draw, margin_x + 120, y, section_w - 240, "单榜 BEST 30", PURPLE)
    y += section_title_h
    for index, item in enumerate(b30_items[:30], start=1):
        x = margin_x + ((index - 1) % cols) * (card_w + gap_x)
        card_y = y + ((index - 1) // cols) * (card_h + gap_y)
        draw_score_card(img, item, index, x, card_y, jacket_images.get(str(item["jacket_id"])), card_w, card_h)

    footer_font = get_font(19, "Normal")
    footer = "Data from Lxns OAuth | Lxns CN constants | generated by Robinbot"
    fw = text_width(draw, footer, footer_font)
    draw.text(((width - fw) / 2, height - 44), footer, font=footer_font, fill=TEXT_SUB)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def draw_b50_image(player, best_items, new_items, jacket_images, version):
    width, card_w, card_h, gap_x, gap_y, cols = 1600, 284, 124, 22, 18, 5
    rows = [math.ceil(max(len(items), 1) / cols) for items in (best_items, new_items)]
    height = 244 + sum(84 + count * (card_h + gap_y) for count in rows) + 70
    img = make_background(width, height)
    draw = ImageDraw.Draw(img)
    from .services.rating import average
    font = get_font(27, "Bold")
    draw.text((40, 42), clamp_text(draw, player.get("name", "Unknown"), font, 700), font=font, fill=TEXT)
    from .services.player_data import player_level
    draw.text((1050, 42), f"Lv.{player_level(player)}", font=font, fill=TEXT_SUB)
    draw.text((40, 94), f"B50 Rating estimate: {average(best_items + new_items, 50):.4f}", font=font, fill=GREEN)
    draw.text((40, 146), f"Lxns CN version {version} | Missing slots = 0 | / 50", font=get_font(23), fill=TEXT_SUB)
    y = 244
    for title, items, count, color in [("BEST 30 · 旧版本", best_items, rows[0], PURPLE),
                                       ("NEW 20 · 当前版本", new_items, rows[1], CYAN)]:
        draw_section_title(draw, 160, y, 1280, title, color)
        y += 84
        if not items:
            draw.text((60, y + 30), "No recorded eligible scores (0)", font=get_font(24), fill=TEXT_SUB)
        for index, item in enumerate(items, 1):
            x = 40 + ((index - 1) % cols) * (card_w + gap_x)
            card_y = y + ((index - 1) // cols) * (card_h + gap_y)
            draw_score_card(img, item, index, x, card_y, jacket_images.get(str(item["jacket_id"])), card_w, card_h)
        y += count * (card_h + gap_y)
    draw.text((40, height - 44), "Lxns OAuth | Disjoint song versions | generated by Robinbot", font=get_font(19), fill=TEXT_SUB)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


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
