import asyncio
import io
import json
import math
import os
import platform
import random
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from nonebot import get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .song import download_jacket
from .user_bind import get_bind_info

config = get_plugin_config(Config)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SCORE_DIR = os.path.join(DATA_DIR, "score")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
LXNS_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"

SUPPORTED_VERSION = "2026"
KNOWN_FUTURE_VERSIONS = {"verse", "xverse", "xversex"}
MAX_RATING_SCORE = 1009000

best_rating_cmd = on_command("b", priority=5, block=True)
best_rating_30_cmd = on_command("b30", priority=5, block=True)

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
    try:
        if platform.system() == "Windows":
            return ImageFont.truetype("msyh.ttc", size)
        for path in [
            f"/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-{weight}.otf",
            f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
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


def floor_2(value: float) -> float:
    return math.floor((value + 1e-9) * 100) / 100


def extract_lxns_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def load_song_data() -> Tuple[List[Dict[str, Any]], Set[int]]:
    if not os.path.exists(SONGLIST_PATH):
        return [], set()
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.error(f"读取 songlist.json 失败: {e}")
        return [], set()

    songs = raw.get("songs", []) if isinstance(raw, dict) else raw
    versions = raw.get("versions", []) if isinstance(raw, dict) else []
    new_versions: Set[int] = set()
    for version in versions if isinstance(versions, list) else []:
        title = str(version.get("title", "")).upper()
        number = safe_int(version.get("version"), 0)
        if "VERSE" in title or "LUMINOUS PLUS" in title:
            new_versions.add(number)
    return songs if isinstance(songs, list) else [], new_versions


def build_chart_meta(songs: List[Dict[str, Any]], const_source: str = "default") -> Dict[Tuple[str, int], Dict[str, Any]]:
    meta: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for song in songs:
        if not isinstance(song, dict):
            continue
        song_id = str(song.get("id", ""))
        title = song.get("title") or song.get("song_name") or ""
        version = safe_int(song.get("version"), 0)
        for diff in song.get("difficulties", []) or []:
            if not isinstance(diff, dict):
                continue
            level_index = safe_int(diff.get("difficulty"), -1)
            if level_index < 0:
                continue
            jacket_id = str(diff.get("origin_id") or song.get("origin_id") or song_id) if level_index == 5 else song_id
            level_value = diff.get("level_value")
            level = diff.get("level")
            if const_source == "lx":
                level_value = diff.get("lx_level_value", level_value)
                level = diff.get("lx_level", level)
            elif const_source == "chunirec":
                level_value = diff.get("chunirec_level_value", level_value)
                level = diff.get("chunirec_level", level)
            meta[(song_id, level_index)] = {
                "id": song_id,
                "jacket_id": jacket_id,
                "song_name": title,
                "level": level,
                "level_value": safe_float(level_value, 0.0),
                "level_index": level_index,
                "version": version,
            }
    return meta


def load_score_file(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception as e:
        logger.error(f"读取本地成绩失败: {e}")
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def merge_score_rows(rows: List[Dict[str, Any]]) -> Optional[Dict[Tuple[str, int], Dict[str, Any]]]:
    best: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in rows:
        song_id = str(row.get("id", ""))
        level_index = safe_int(row.get("level_index"), -1)
        score = safe_int(row.get("score"), 0)
        if not song_id or level_index < 0:
            continue
        key = (song_id, level_index)
        if key not in best or score > safe_int(best[key].get("score"), 0):
            best[key] = row
    return best or None


def load_b30_scores(qq: str) -> Optional[Dict[Tuple[str, int], Dict[str, Any]]]:
    rows = [row for row in load_score_file(os.path.join(SCORE_DIR, f"{qq}.json")) if row.get("source") != "manual"]
    rows.extend(load_score_file(os.path.join(SCORE_DIR, f"{qq}_manual.json")))
    return merge_score_rows(rows)


def load_player_info(qq: str) -> Dict[str, Any]:
    info_path = os.path.join(SCORE_DIR, f"{qq}_info.json")
    if not os.path.exists(info_path):
        return {}
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except Exception as e:
        logger.warning(f"读取本地玩家信息失败: {e}")
        return {}


def load_legacy_manual_scores(qq: str) -> List[Dict[str, Any]]:
    rows = load_score_file(os.path.join(SCORE_DIR, f"{qq}.json"))
    return [row for row in rows if row.get("source") == "manual"]


def strip_legacy_manual_scores(qq: str) -> None:
    path = os.path.join(SCORE_DIR, f"{qq}.json")
    rows = load_score_file(path)
    if not rows or not any(row.get("source") == "manual" for row in rows):
        return
    cleaned = [row for row in rows if row.get("source") != "manual"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=4)


def migrate_legacy_manual_scores(qq: str) -> None:
    manual_path = os.path.join(SCORE_DIR, f"{qq}_manual.json")
    legacy = load_legacy_manual_scores(qq)
    if not legacy:
        return
    merged = merge_score_rows(load_score_file(manual_path) + legacy)
    if merged:
        with open(manual_path, "w", encoding="utf-8") as f:
            json.dump(list(merged.values()), f, ensure_ascii=False, indent=4)
    strip_legacy_manual_scores(qq)


def load_local_scores_excluding_manual(qq: str) -> Optional[Dict[Tuple[str, int], Dict[str, Any]]]:
    rows = [row for row in load_score_file(os.path.join(SCORE_DIR, f"{qq}.json")) if row.get("source") != "manual"]
    return merge_score_rows(rows)


def load_local_scores(qq: str) -> Optional[Dict[Tuple[str, int], Dict[str, Any]]]:
    rows = [row for row in load_score_file(os.path.join(SCORE_DIR, f"{qq}.json")) if row.get("source") != "manual"]
    return merge_score_rows(rows)


def rating_from_score(level_value: float, score: int) -> float:
    if level_value <= 0 or score < 900000:
        return 0.0
    if score >= MAX_RATING_SCORE:
        return level_value + 2.15
    if score >= 1007500:
        return level_value + 2.0 + (score - 1007500) / 10000
    if score >= 1005000:
        return level_value + 1.5 + (score - 1005000) / 5000
    if score >= 1000000:
        return level_value + 1.0 + (score - 1000000) / 10000
    if score >= 990000:
        return level_value + 0.6 + (score - 990000) / 25000
    if score >= 975000:
        return level_value + (score - 975000) / 25000
    if score >= 950000:
        return level_value - 1.5 + (score - 950000) * 1.5 / 25000
    if score >= 925000:
        return level_value - 3.0 + (score - 925000) * 1.5 / 25000
    return level_value - 5.0 + (score - 900000) * 2.0 / 25000


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


def build_item(meta: Dict[str, Any], score_row: Dict[str, Any], use_stored_rating: bool = True) -> Dict[str, Any]:
    score = safe_int(score_row.get("score"), 0)
    rating = safe_float(score_row.get("rating"), 0.0) if use_stored_rating else 0.0
    if rating <= 0:
        rating = rating_from_score(safe_float(meta.get("level_value"), 0.0), score)
    return {
        **meta,
        "score": score,
        "rating": rating,
        "rank": rank_to_display(score_row.get("rank")) or score_to_rank(score),
        "clear": score_row.get("clear") or "",
        "full_combo": normalize_fc(score_row.get("full_combo") or score_row.get("full_chain")),
    }


def normalize_lxns_item(item: Dict[str, Any], chart_meta: Dict[Tuple[str, int], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    song_id = str(item.get("id", ""))
    level_index = safe_int(item.get("level_index"), -1)
    if not song_id or level_index < 0:
        return None
    meta = dict(chart_meta.get((song_id, level_index), {}))
    if not meta:
        meta = {
            "id": song_id,
            "jacket_id": song_id,
            "song_name": item.get("song_name") or f"ID {song_id}",
            "level": item.get("level") or "",
            "level_value": safe_float(item.get("level_value"), 0.0),
            "level_index": level_index,
            "version": 0,
        }
    return build_item(meta, item)


def normalize_local_item(
    item: Dict[str, Any],
    chart_meta: Dict[Tuple[str, int], Dict[str, Any]],
    const_source: str = "chunirec",
) -> Optional[Dict[str, Any]]:
    song_id = str(item.get("id", ""))
    level_index = safe_int(item.get("level_index"), -1)
    if not song_id or level_index < 0:
        return None
    meta = dict(chart_meta.get((song_id, level_index), {}))
    if not meta:
        level_value = item.get("chunirec_level_value") if const_source == "chunirec" else item.get("lx_level_value")
        if level_value is None:
            level_value = item.get("level_value")
        meta = {
            "id": song_id,
            "jacket_id": song_id,
            "song_name": item.get("song_name") or f"ID {song_id}",
            "level": item.get("level") or "",
            "level_value": safe_float(level_value, 0.0),
            "level_index": level_index,
            "version": 0,
        }
    return build_item(meta, item, use_stored_rating=False)


def section_average(items: List[Dict[str, Any]], divisor: int) -> float:
    return sum(item["rating"] for item in items[:divisor]) / divisor if divisor else 0.0


def total_rating_raw(b30_items: List[Dict[str, Any]], n20_items: List[Dict[str, Any]]) -> float:
    return (sum(item["rating"] for item in b30_items[:30]) + sum(item["rating"] for item in n20_items[:20])) / 50


def find_min_score_for_rating(level_value: float, target_rating: float, current_score: int) -> Optional[int]:
    if rating_from_score(level_value, MAX_RATING_SCORE) + 1e-9 < target_rating:
        return None
    low = max(0, current_score + 1)
    high = MAX_RATING_SCORE
    result = None
    while low <= high:
        mid = (low + high) // 2
        if rating_from_score(level_value, mid) + 1e-9 >= target_rating:
            result = mid
            high = mid - 1
        else:
            low = mid + 1
    return result


def build_recommendations(
    local_scores: Optional[Dict[Tuple[str, int], Dict[str, Any]]],
    chart_meta: Dict[Tuple[str, int], Dict[str, Any]],
    b30_items: List[Dict[str, Any]],
    n20_items: List[Dict[str, Any]],
    new_versions: Set[int],
) -> List[Dict[str, Any]]:
    current_sum = sum(item["rating"] for item in b30_items) + sum(item["rating"] for item in n20_items)
    current_display = floor_2(current_sum / 50)
    target_sum = (current_display + 0.01) * 50
    needed = max(0.0, target_sum - current_sum)
    reference_levels = [item["level_value"] for item in b30_items + n20_items if item.get("level_value")]
    median_level = sorted(reference_levels)[len(reference_levels) // 2] if reference_levels else 0.0
    b30_keys = {(str(item["id"]), safe_int(item["level_index"])) for item in b30_items}
    n20_keys = {(str(item["id"]), safe_int(item["level_index"])) for item in n20_items}
    b30_floor = b30_items[-1]["rating"] if len(b30_items) >= 30 else 0.0
    n20_floor = n20_items[-1]["rating"] if len(n20_items) >= 20 else 0.0

    if local_scores:
        candidate_entries = list(chart_meta.items())
    else:
        seen_keys = set()
        candidate_entries = []
        for item in b30_items + n20_items:
            key = (str(item["id"]), safe_int(item["level_index"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidate_entries.append((key, item))

    exact: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []
    for key, meta in candidate_entries:
        if not str(meta.get("id", "")).isdigit():
            continue
        level_value = safe_float(meta.get("level_value"), 0.0)
        if level_value <= 0:
            continue
        if median_level and not (median_level - 0.7 <= level_value <= median_level + 0.35):
            continue

        row = local_scores.get(key, {"score": 0}) if local_scores else meta
        current_score = safe_int(row.get("score"), 0)
        if current_score >= MAX_RATING_SCORE:
            continue

        is_new = safe_int(meta.get("version"), 0) in new_versions
        selected = key in (n20_keys if is_new else b30_keys)
        floor_rating = n20_floor if is_new else b30_floor
        current_chart_rating = safe_float(row.get("rating"), 0.0) if not local_scores else rating_from_score(level_value, current_score)
        if current_chart_rating <= 0:
            current_chart_rating = rating_from_score(level_value, current_score)
        max_chart_rating = rating_from_score(level_value, MAX_RATING_SCORE)
        base_rating = current_chart_rating if selected else floor_rating
        max_gain = max_chart_rating - base_rating
        if max_gain <= 1e-9:
            continue

        target_gain = needed if needed > 0 else min(max_gain, 0.01)
        target_chart_rating = base_rating + target_gain
        target_score = find_min_score_for_rating(level_value, target_chart_rating, current_score)
        rec = {
            **meta,
            "score": current_score,
            "target_score": target_score or MAX_RATING_SCORE,
            "current_rating": current_chart_rating,
            "target_rating": rating_from_score(level_value, target_score or MAX_RATING_SCORE),
            "weighted_gain": min(max_gain, target_gain) / 50,
            "chart_gain": min(max_gain, target_gain),
            "is_plus_001": target_score is not None and needed > 0,
        }
        if target_score is not None and needed > 0:
            exact.append(rec)
        else:
            fallback.append(rec)

    pool = exact or fallback
    if not pool:
        return []
    pool.sort(key=lambda x: (abs(x["level_value"] - median_level), x["target_score"], -x["chart_gain"]))
    shortlist = pool[:60]
    return random.sample(shortlist, k=min(8, len(shortlist)))


async def lxns_get(client: httpx.AsyncClient, path: str) -> Tuple[int, Any]:
    response = await client.get(
        f"{LXNS_BASE_URL}{path}",
        headers={"Authorization": config.lxns_token},
        timeout=20.0,
    )
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, response.text


async def resolve_player(client: httpx.AsyncClient, qq: str) -> Tuple[str, Dict[str, Any]]:
    status, payload = await lxns_get(client, f"/player/qq/{qq}")
    data = extract_lxns_data(payload)
    if status == 200 and isinstance(data, dict) and data.get("friend_code"):
        return str(data["friend_code"]), data

    bind_data = get_bind_info()
    friend_code = str(bind_data.get(qq, ""))
    if friend_code:
        status, payload = await lxns_get(client, f"/player/{friend_code}")
        data = extract_lxns_data(payload)
        if status == 200 and isinstance(data, dict):
            return friend_code, data
    return "", {}


async def fetch_lxns_bests(friend_code: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        status, payload = await lxns_get(client, f"/player/{friend_code}/bests")
    data = extract_lxns_data(payload)
    if status != 200 or not isinstance(data, dict):
        return [], [], {"status": status, "payload": payload}
    bests = data.get("bests") if isinstance(data.get("bests"), list) else []
    new_bests = data.get("new_bests") if isinstance(data.get("new_bests"), list) else []
    return bests, new_bests, {}


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


def draw_header(img: Image.Image, player: Dict[str, Any], b30_avg: float, n20_avg: float, version: str) -> None:
    draw = ImageDraw.Draw(img)
    font_title = get_font(34, "Bold")
    font_head = get_font(32, "Bold")
    font_body = get_font(23, "Normal")
    font_small = get_font(19, "Normal")
    draw_shadowed_panel(draw, (40, 38, 640, 206), 8, PANEL, LINE)
    name = player.get("name") or "Unknown"
    title_obj = player.get("trophy") or player.get("title") or {}
    title = title_obj.get("name", "") if isinstance(title_obj, dict) else str(title_obj or "")
    level = safe_int(player.get("level"), 0) + 100 * safe_int(player.get("reborn_count"), 0)
    rating = safe_float(player.get("rating"), 0.0)
    draw.text((70, 58), clamp_text(draw, name, font_title, 520), font=font_title, fill=TEXT)
    draw.text((70, 110), clamp_text(draw, f"Lv.{level}  {title}", font_body, 520), font=font_body, fill=TEXT_SUB)
    draw.text((70, 154), f"Rating {floor_2(rating):.2f}", font=font_head, fill=GREEN)

    draw_shadowed_panel(draw, (680, 38, 1040, 206), 8, PANEL, LINE)
    draw.text((732, 74), f"CHUNITHM {version}", font=font_head, fill=CYAN)
    draw.text((738, 124), "Best Rating", font=font_body, fill=TEXT_SUB)

    draw_shadowed_panel(draw, (1080, 38, 1560, 206), 8, PANEL, LINE)
    total = (b30_avg * 30 + n20_avg * 20) / 50
    draw.text((1112, 62), f"B30 {b30_avg:.3f}  +  N20 {n20_avg:.3f}", font=font_body, fill=TEXT)
    draw.text((1112, 108), f"TOTAL {total:.4f}", font=font_head, fill=GREEN)
    draw.text((1112, 160), "Data from Lxns | generated by Robinbot", font=font_small, fill=TEXT_SUB)


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


def draw_suggestion_card(base: Image.Image, item: Dict[str, Any], x: int, y: int, jacket: Optional[Image.Image], card_w: int, card_h: int) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_name = get_font(22, "Bold")
    font_score = get_font(24, "Normal")
    font_body = get_font(18, "Normal")
    font_small = get_font(14, "Bold")
    draw.rounded_rectangle((x + 4, y + 4, x + card_w + 4, y + card_h + 4), radius=8, fill=(0, 0, 0, 90))
    draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=8, fill=PANEL_2, outline=LINE, width=1)
    base.alpha_composite(overlay)

    jacket_size = card_h - 16
    if jacket:
        base.alpha_composite(jacket.resize((jacket_size, jacket_size), Image.Resampling.LANCZOS), (x + 8, y + 8))
    else:
        base.alpha_composite(Image.new("RGBA", (jacket_size, jacket_size), (68, 71, 90, 255)), (x + 8, y + 8))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    tx = x + jacket_size + 22
    right = x + card_w - 18
    draw.text((tx, y + 12), clamp_text(draw, item["song_name"], font_name, right - tx), font=font_name, fill=TEXT)
    draw_badge(draw, (tx, y + 48, tx + 60, y + 68), f"{item['level_value']:.1f}", font_small, PURPLE, radius=6)
    gain_text = "总Rating +0.01" if item.get("is_plus_001") else f"可提升 +{item['weighted_gain']:.4f}"
    draw_badge(draw, (tx + 72, y + 48, tx + 198, y + 68), gain_text, font_small, GREEN, radius=6)
    score_line = f"{format_score_plain(item['score'])}  ->  {format_score_plain(item['target_score'])}"
    draw.text((tx, y + 82), score_line, font=font_score, fill=CYAN)
    detail = f"单曲 {item['current_rating']:.2f} -> {item['target_rating']:.2f}    替换增量 {item['chart_gain']:.3f}"
    draw.text((tx, y + 118), detail, font=font_body, fill=TEXT_SUB)
    base.alpha_composite(overlay)


def draw_best_image(
    player: Dict[str, Any],
    b30_items: List[Dict[str, Any]],
    n20_items: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
    jacket_images: Dict[str, Image.Image],
    version: str,
) -> bytes:
    width = 1600
    card_w = 284
    card_h = 124
    gap_x = 22
    gap_y = 18
    cols = 5
    margin_x = 40
    section_w = width - margin_x * 2
    b30_rows = math.ceil(max(len(b30_items), 1) / cols)
    n20_rows = math.ceil(max(len(n20_items), 1) / cols)
    sug_cols = 2
    sug_w = (section_w - gap_x) // 2
    sug_h = 150
    sug_rows = math.ceil(len(suggestions[:8]) / sug_cols)
    suggestion_h = 0 if not suggestions else 84 + sug_rows * (sug_h + gap_y) + 20
    top = 244
    section_title_h = 84
    b30_h = section_title_h + b30_rows * (card_h + gap_y)
    n20_h = section_title_h + n20_rows * (card_h + gap_y)
    height = top + b30_h + 62 + n20_h + suggestion_h + 70

    img = make_background(width, height)
    draw = ImageDraw.Draw(img)
    b30_avg = section_average(b30_items, 30)
    n20_avg = section_average(n20_items, 20)
    draw_header(img, player, b30_avg, n20_avg, version)

    y = top
    draw_section_title(draw, margin_x + 120, y, section_w - 240, "非当前版本最好成绩 / BEST 30", PURPLE)
    y += section_title_h
    for index, item in enumerate(b30_items[:30], start=1):
        x = margin_x + ((index - 1) % cols) * (card_w + gap_x)
        card_y = y + ((index - 1) // cols) * (card_h + gap_y)
        draw_score_card(img, item, index, x, card_y, jacket_images.get(str(item["jacket_id"])), card_w, card_h)

    y += b30_rows * (card_h + gap_y) + 38
    draw_section_title(draw, margin_x + 120, y, section_w - 240, "当前版本最好成绩 / NEW 20", PINK)
    y += section_title_h
    for index, item in enumerate(n20_items[:20], start=1):
        x = margin_x + ((index - 1) % cols) * (card_w + gap_x)
        card_y = y + ((index - 1) // cols) * (card_h + gap_y)
        draw_score_card(img, item, index, x, card_y, jacket_images.get(str(item["jacket_id"])), card_w, card_h)

    y += n20_rows * (card_h + gap_y) + 38
    if suggestions:
        label = "推分建议"
        draw_section_title(draw, margin_x + 120, y, section_w - 240, label, GREEN)
        y += section_title_h
        for index, item in enumerate(suggestions[:8]):
            x = margin_x + (index % sug_cols) * (sug_w + gap_x)
            card_y = y + (index // sug_cols) * (sug_h + gap_y)
            draw_suggestion_card(img, item, x, card_y, jacket_images.get(str(item["jacket_id"])), sug_w, sug_h)

    footer_font = get_font(19, "Normal")
    footer = "Data from Lxns Network | suggestions from local score records | generated by Robinbot"
    fw = text_width(draw, footer, footer_font)
    draw.text(((width - fw) / 2, height - 44), footer, font=footer_font, fill=TEXT_SUB)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def draw_b30_header(img: Image.Image, player: Dict[str, Any], b30_avg: float) -> None:
    draw = ImageDraw.Draw(img)
    font_title = get_font(34, "Bold")
    font_head = get_font(32, "Bold")
    font_body = get_font(23, "Normal")
    font_small = get_font(19, "Normal")

    draw_shadowed_panel(draw, (40, 38, 640, 206), 8, PANEL, LINE)
    name = player.get("UserName") or player.get("name") or "Unknown"
    level = safe_int(player.get("Level", player.get("level", 0)), 0)
    draw.text((70, 58), clamp_text(draw, name, font_title, 520), font=font_title, fill=TEXT)
    draw.text((70, 112), f"Lv.{level}", font=font_body, fill=TEXT_SUB)
    draw.text((70, 154), f"B30 Rating {b30_avg:.4f}", font=font_head, fill=GREEN)

    draw_shadowed_panel(draw, (680, 38, 1040, 206), 8, PANEL, LINE)
    draw.text((732, 74), "CHUNITHM B30", font=font_head, fill=CYAN)
    draw.text((738, 124), "All versions | ChuniRec constants", font=font_body, fill=TEXT_SUB)

    draw_shadowed_panel(draw, (1080, 38, 1560, 206), 8, PANEL, LINE)
    draw.text((1112, 78), f"TOTAL {b30_avg:.4f}", font=font_head, fill=GREEN)
    draw.text((1112, 132), "Local scores + manual records", font=font_body, fill=TEXT_SUB)
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
    footer = "Data from local score records | ChuniRec constants | generated by Robinbot"
    fw = text_width(draw, footer, footer_font)
    draw.text(((width - fw) / 2, height - 44), footer, font=footer_font, fill=TEXT_SUB)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def parse_version(raw: str) -> str:
    return raw.strip().lower() or SUPPORTED_VERSION


@best_rating_cmd.handle()
async def _(event: MessageEvent, msg: Message = CommandArg()):
    version = parse_version(msg.extract_plain_text())
    if version != SUPPORTED_VERSION:
        if version in KNOWN_FUTURE_VERSIONS:
            await best_rating_cmd.finish(f"当前只能生成 {SUPPORTED_VERSION} 的 B30/N20。{version} 属于日服新版本，当前本地曲库与落雪中二数据还不支持。")
        await best_rating_cmd.finish("版本参数暂不支持。可用：/b 或 /b 2026")
    if not config.lxns_token:
        await best_rating_cmd.finish("未配置落雪开发者密钥，无法从落雪获取 B30/N20。")

    qq = str(event.get_user_id())
    await best_rating_cmd.send("收到，正在处理...")
    async with httpx.AsyncClient() as client:
        friend_code, player = await resolve_player(client, qq)
    if not friend_code:
        await best_rating_cmd.finish("未找到落雪玩家信息，请先使用 /bind 绑定好友码，或确认落雪已绑定当前账号。")

    raw_b30, raw_n20, err = await fetch_lxns_bests(friend_code)
    if err:
        logger.warning(f"获取落雪 Best 失败: {err}")
        await best_rating_cmd.finish("获取落雪 B30/N20 失败，请稍后再试。")

    songs, new_versions = load_song_data()
    chart_meta = build_chart_meta(songs, const_source="lx")
    b30_items = [item for item in (normalize_lxns_item(row, chart_meta) for row in raw_b30 if isinstance(row, dict)) if item]
    n20_items = [item for item in (normalize_lxns_item(row, chart_meta) for row in raw_n20 if isinstance(row, dict)) if item]
    b30_items.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
    n20_items.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
    if not b30_items and not n20_items:
        await best_rating_cmd.finish("落雪没有返回可展示的 B30/N20 数据。")

    local_scores = load_local_scores(qq)
    suggestions = build_recommendations(local_scores, chart_meta, b30_items[:30], n20_items[:20], new_versions)
    player = dict(player)
    player["rating"] = total_rating_raw(b30_items, n20_items)
    try:
        jacket_images = await prepare_jackets(b30_items[:30] + n20_items[:20] + suggestions)
        img_bytes = draw_best_image(player, b30_items[:30], n20_items[:20], suggestions, jacket_images, version)
    except Exception as e:
        logger.exception(f"生成 B30/N20 图片失败: {e}")
        await best_rating_cmd.finish("生成 B30/N20 图片失败。")
    await best_rating_cmd.finish(MessageSegment.image(img_bytes))


@best_rating_30_cmd.handle()
async def _(event: MessageEvent):
    qq = str(event.get_user_id())
    await best_rating_30_cmd.send("收到，正在处理...")
    migrate_legacy_manual_scores(qq)

    songs, _new_versions = load_song_data()
    chart_meta = build_chart_meta(songs, const_source="chunirec")
    local_scores = load_b30_scores(qq)
    if not local_scores:
        await best_rating_30_cmd.finish("本地没有可用成绩。请先使用 /lxupdate、/chuupdate 或 /传分。")

    items = [
        item
        for item in (normalize_local_item(row, chart_meta, const_source="chunirec") for row in local_scores.values())
        if item and safe_float(item.get("level_value"), 0.0) > 0 and safe_int(item.get("score"), 0) > 0
    ]
    items.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
    b30_items = items[:30]
    if not b30_items:
        await best_rating_30_cmd.finish("本地成绩中没有可用于计算 B30 的谱面。")

    player: Dict[str, Any] = {"UserName": "Local Player", "Level": 0}
    player.update(load_player_info(qq))

    try:
        jacket_images = await prepare_jackets(b30_items)
        img_bytes = draw_b30_image(player, b30_items, jacket_images)
    except Exception as e:
        logger.exception(f"生成 B30 图片失败: {e}")
        await best_rating_30_cmd.finish("生成 B30 图片失败。")
    await best_rating_30_cmd.finish(MessageSegment.image(img_bytes))
