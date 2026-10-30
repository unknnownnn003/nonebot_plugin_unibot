import asyncio
import io
import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw
from nonebot import get_bot, on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from .services.storage import atomic_json
from .song import (
    get_font,
    get_version_name,
    match_song_by_query,
)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
JACKET_DIR = os.path.join(DATA_DIR, "jacket")
SCORE_FILE = os.path.join(DATA_DIR, "chuguess_scores.json")

GAME_TIMEOUT = 300

DIFFICULTY_CONFIG: Dict[str, Dict[str, Any]] = {
    "bas": {"name": "BASIC", "ratio": 2, "score": 1},
    "adv": {"name": "ADVANCED", "ratio": 4, "score": 2},
    "exp": {"name": "EXPERT", "ratio": 6, "score": 3},
    "mas": {"name": "MASTER", "ratio": 8, "score": 5},
    "ult": {"name": "ULTIMA", "ratio": 10, "score": 7},
}

DIFF_ALIASES: Dict[str, str] = {
    "basic": "bas",
    "adv": "adv",
    "advanced": "adv",
    "exp": "exp",
    "expert": "exp",
    "mas": "mas",
    "master": "mas",
    "ult": "ult",
    "ultima": "ult",
}

HINT_TYPES = ["name_part", "artist", "level", "version", "location", "more_area"]
SCORE_HINT_TYPES = {"artist", "level", "version", "location"}


def calc_actual_score(base_score: int, hints_used: set) -> int:
    penalty_hints = len(hints_used & SCORE_HINT_TYPES)
    total_penalty_types = len(SCORE_HINT_TYPES)
    raw = round(base_score * (total_penalty_types - penalty_hints) / total_penalty_types)
    return max(1, raw)

active_games: Dict[str, Dict[str, Any]] = {}
game_tasks: Dict[str, asyncio.Task] = {}

_group_locks: Dict[str, asyncio.Lock] = {}
_lock_factory = asyncio.Lock()
_score_lock = asyncio.Lock()


async def _get_group_lock(group_id: str) -> asyncio.Lock:
    async with _lock_factory:
        if group_id not in _group_locks:
            _group_locks[group_id] = asyncio.Lock()
        return _group_locks[group_id]


async def _guess_game_active(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent) and str(event.group_id) in active_games


chuguess_cmd = on_command("chuguess", priority=5, block=True)
hint_cmd = on_command("提示", aliases={"chu提示"}, priority=5, block=True)
giveup_cmd = on_command("放弃", aliases={"chu放弃"}, priority=5, block=True)
guess_cmd = on_regex(r"^[\.。/]?猜\s*(.+)?$", rule=_guess_game_active, priority=5, block=True)


def load_songlist() -> List[Dict[str, Any]]:
    if not os.path.exists(SONGLIST_PATH):
        return []
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("songs", [])
    except Exception as e:
        logger.error(f"读取 songlist 失败: {e}")
        return []


def get_available_jacket_ids() -> List[str]:
    if not os.path.exists(JACKET_DIR):
        return []
    ids = []
    for fname in os.listdir(JACKET_DIR):
        if fname.endswith(".png"):
            ids.append(fname[:-4])
    return ids


def load_scores() -> Dict[str, int]:
    if not os.path.exists(SCORE_FILE):
        return {}
    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_scores(scores: Dict[str, int]) -> None:
    atomic_json(SCORE_FILE, scores)


def crop_jacket(img: Image.Image, ratio: int) -> Tuple[Image.Image, int, int, int, int]:
    w, h = img.size
    target_area = w * h / ratio

    min_dim = max(15, int(math.sqrt(target_area)) // 3)
    max_dim = min(w, h)

    crop_w = random.randint(min_dim, max_dim)
    crop_h = max(15, int(target_area / crop_w))
    crop_w = min(crop_w, w)
    crop_h = min(crop_h, h)

    x = random.randint(0, max(0, w - crop_w))
    y = random.randint(0, max(0, h - crop_h))

    cropped = img.crop((x, y, x + crop_w, y + crop_h))
    return cropped, x, y, crop_w, crop_h


def make_crop_preview(jacket: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
    jw, jh = jacket.size
    overlay = Image.new("RGBA", jacket.size, (0, 0, 0, 220))
    mask = Image.new("L", jacket.size, 255)
    mask_draw = ImageDraw.Draw(mask)
    x1, y1 = max(0, x - 3), max(0, y - 3)
    x2, y2 = min(jw, x + w + 3), min(jh, y + h + 3)
    mask_draw.rectangle([x1, y1, x2, y2], fill=0)
    preview = jacket.copy()
    preview.paste(overlay, mask=mask)
    return preview


def make_revealed_preview(jacket: Image.Image, revealed_areas: List[Tuple[int, int, int, int]]) -> Image.Image:
    overlay = Image.new("RGBA", jacket.size, (0, 0, 0, 240))
    mask = Image.new("L", jacket.size, 255)
    mask_draw = ImageDraw.Draw(mask)
    for rx, ry, rw, rh in revealed_areas:
        mask_draw.rectangle([rx, ry, rx + rw, ry + rh], fill=0)
    preview = jacket.copy()
    preview.paste(overlay, mask=mask)
    return preview


def pick_random_song_and_jacket() -> Optional[Tuple[Dict[str, Any], Image.Image, str]]:
    songs = load_songlist()
    if not songs:
        return None
    available_ids = set(get_available_jacket_ids())
    if not available_ids:
        return None

    candidates = []
    for song in songs:
        song_id = str(song.get("id", ""))
        jacket_id = song_id
        if song_id in available_ids:
            candidates.append((song, jacket_id))
        if song.get("origin_id") and str(song["origin_id"]) in available_ids:
            candidates.append((song, str(song["origin_id"])))

    if not candidates:
        return None

    song, jacket_id = random.choice(candidates)
    jacket_path = os.path.join(JACKET_DIR, f"{jacket_id}.png")
    try:
        jacket = Image.open(jacket_path).convert("RGBA")
    except Exception as e:
        logger.error(f"读取曲绘失败 {jacket_id}: {e}")
        return None

    return song, jacket, jacket_id


def make_game_result_image(
    song: Dict[str, Any],
    jacket: Image.Image,
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    difficulty: str,
    score: int,
    guessed: bool,
    guesser_name: str = "",
    end_reason: str = "timeout",
) -> bytes:
    BG_COLOR = (30, 30, 35, 255)
    TEXT_MAIN = (245, 245, 245, 255)
    TEXT_SUB = (180, 180, 180, 255)
    ACCENT = (98, 114, 164, 255)
    GREEN = (80, 250, 123)
    RED = (255, 85, 85)

    font_title = get_font(32, "Bold")
    font_body = get_font(20, "Normal")
    font_small = get_font(16, "Normal")

    jacket_w, jacket_h = jacket.size
    card_w = max(700, jacket_w + 80)
    header_h = 110
    footer_h = 50
    card_h = header_h + jacket_h + 20 + footer_h

    img = Image.new("RGBA", (card_w, card_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    title = song.get("title", "Unknown")
    artist = song.get("artist", "-")
    diff_name = DIFFICULTY_CONFIG[difficulty]["name"]

    if guessed:
        status_text = f"猜中! {guesser_name} 获得 {score} 分"
        status_color = GREEN
    elif end_reason == "giveup":
        status_text = "已放弃"
        status_color = RED
    else:
        status_text = "时间到! 无人猜中"
        status_color = RED

    draw.text((40, 20), f"曲绘猜曲 - {diff_name}", font=font_title, fill=TEXT_MAIN)
    draw.text((40, 62), status_text, font=font_body, fill=status_color)
    draw.text((40, 90), f"答案: {title} / {artist}", font=font_body, fill=TEXT_SUB)

    jacket_x = (card_w - jacket_w) // 2
    jacket_y = header_h + 10
    img.paste(jacket, (jacket_x, jacket_y), jacket if jacket.mode == "RGBA" else None)

    draw.line([(40, card_h - footer_h - 5), (card_w - 40, card_h - footer_h - 5)], fill=ACCENT, width=2)
    draw.text((40, card_h - footer_h + 5), "Generated by Robinbot | unibot", font=font_small, fill=TEXT_SUB)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def partial_title(title: str, step: int) -> str:
    if len(title) <= 1:
        return title
    length = len(title)
    reveal_count = min(length, max(1, (step + 1) * length // 5))
    indices = sorted(random.sample(range(length), reveal_count))
    result = list("＊" * length)
    for i in indices:
        result[i] = title[i]
    return "".join(result)


async def get_hint_text(game: Dict[str, Any]) -> Optional[Tuple[str, Any]]:
    available = [h for h in HINT_TYPES if h not in game["hints_used"] or h in ("more_area", "name_part")]
    if not available:
        return ("text", "所有提示已用完！")

    hint_type = random.choice(available)
    song = game["song"]

    if hint_type == "name_part":
        step = game.setdefault("name_reveal_step", 0)
        game["name_reveal_step"] = step + 1
        return ("text", f"[提示] 曲名一部分: {partial_title(song.get('title', ''), step)}")

    elif hint_type == "artist":
        game["hints_used"].add("artist")
        return ("text", f"[提示] 曲师: {song.get('artist', '未知')}")

    elif hint_type == "level":
        game["hints_used"].add("level")
        diffs = song.get("difficulties", [])
        mas_level = ult_level = "?"
        for d in diffs:
            di = d.get("difficulty", -1)
            lv = d.get("level", "?")
            if di == 3:
                mas_level = lv
            elif di == 4:
                ult_level = lv
        lines = [f"[提示] MAS 定级: {mas_level}"]
        if ult_level != "?":
            lines.append(f"ULT 定级: {ult_level}")
        return ("text", "\n".join(lines))

    elif hint_type == "version":
        game["hints_used"].add("version")
        version_raw = song.get("version", "-")
        version_name = get_version_name(version_raw)
        return ("text", f"[提示] 收录版本: {version_name}")

    elif hint_type == "location":
        game["hints_used"].add("location")
        preview = make_crop_preview(
            game["jacket"],
            game["crop_x"],
            game["crop_y"],
            game["crop_w"],
            game["crop_h"],
        )
        buf = io.BytesIO()
        preview.convert("RGB").save(buf, format="JPEG", quality=85)
        return ("location", MessageSegment.image(buf.getvalue()))

    elif hint_type == "more_area":
        jacket = game["jacket"]
        crop_w, crop_h = game["crop_w"], game["crop_h"]
        jw, jh = jacket.size

        for _ in range(50):
            new_x = random.randint(0, max(0, jw - crop_w))
            new_y = random.randint(0, max(0, jh - crop_h))
            overlap = False
            for rx, ry, rw, rh in game["revealed_areas"]:
                if (
                    new_x < rx + rw
                    and new_x + crop_w > rx
                    and new_y < ry + rh
                    and new_y + crop_h > ry
                ):
                    overlap = True
                    break
            if not overlap:
                break

        game["revealed_areas"].append((new_x, new_y, crop_w, crop_h))
        preview = make_revealed_preview(jacket, game["revealed_areas"])
        buf = io.BytesIO()
        preview.convert("RGB").save(buf, format="JPEG", quality=85)
        return ("more_area", MessageSegment.image(buf.getvalue()))

    return None


async def game_timeout(group_id: str, expected=None) -> None:
    from .services.game import give_up
    expected = expected or active_games.get(group_id)
    await asyncio.sleep(GAME_TIMEOUT)
    result = await give_up(group_id, expected=expected, reason="timeout")
    if result["status"] != "ok":
        return
    try:
        bot = get_bot(expected.get("bot_id")) if expected.get("bot_id") else get_bot()
        context = expected.get("delivery_context")
        if context:
            from .services.inline_media import deliver
            _, event, owner = context
            content = Message(MessageSegment.image(result["artifacts"][0]["content"]))
            if owner == "command":
                content = Message(MessageSegment.text(f"时间到！答案是：{result['data']['title']}\n")) + content
            await deliver(bot, event, content, "game.timeout", result["data"], notify=owner == "agent")
        else:
            await bot.send_group_msg(group_id=int(group_id), message=Message(
                MessageSegment.text(f"时间到！答案是：{result['data']['title']}\n")
                + MessageSegment.image(result["artifacts"][0]["content"])))
    except Exception as exc:
        logger.warning(f"发送超时消息失败: {type(exc).__name__}")


def parse_difficulty(text: str) -> Optional[str]:
    t = text.strip().lower()
    if t in DIFFICULTY_CONFIG:
        return t
    if t in DIFF_ALIASES:
        return DIFF_ALIASES[t]
    return None


async def present_game_result(matcher, result, mode, bot, event):
    if result["status"] != "ok":
        await matcher.finish(result["fallback_text"])
    data = result["data"]
    if mode == "status":
        cfg = DIFFICULTY_CONFIG[data["difficulty"]]
        await matcher.finish(
            f"当前游戏进行中 — {cfg['name']} 难度 (当前{data['score']}分)\n"
            f"剩余时间: {data['remaining_seconds']}秒\n可用提示类型: {data['hints_remaining']} 种\n"
            "发送 /chuguess <歌名> 答题 | /提示 获取提示 | /放弃 放弃")
    if mode == "hint" and not result["artifacts"]:
        text = data["hint"]
    elif mode == "start":
        cfg = DIFFICULTY_CONFIG[data["difficulty"]]
        text = (f"曲绘猜曲 - {cfg['name']} 难度\n猜出这首歌的名字！发送 /chuguess <歌名> 来答题\n"
                f"发送 /提示 获取提示 | /放弃 放弃本题\n最多可获得 {cfg['score']} 分（使用提示会降低分数）\n")
    elif mode == "guess":
        extra = f" (使用了{data['hint_count']}个提示，基础{data['base_score']}分)" if data['hint_count'] else ""
        text = f"恭喜猜中！答案是：{data['title']}\n+{data['score']} 分!{extra} (总分: {data['total_score']})\n"
    elif mode == "give_up":
        text = f"已放弃。答案是：{data['title']}\n"
    else:
        text = ""
    outgoing = Message(MessageSegment.text(text)) if text else Message()
    for artifact in result["artifacts"]:
        if artifact["kind"] == "image":
            outgoing += MessageSegment.image(artifact["content"])
        else:
            outgoing += artifact["content"]
    from .services.inline_media import deliver
    delivery = await deliver(bot, event, outgoing, "game." + mode, data)
    if delivery.get("data", {}).get("artifact_delivery") == "sent":
        await matcher.finish()
    from .services.results import send_command_result
    await send_command_result(matcher, delivery, finish=True)


@chuguess_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    from .services import game
    if not isinstance(event, GroupMessageEvent):
        await chuguess_cmd.finish("曲绘猜曲仅在群聊中可用。")
    query = args.extract_plain_text().strip()
    state = await game.status(event.group_id)
    if state["status"] == "ok":
        if not query:
            await present_game_result(chuguess_cmd, state, "status", bot, event)
        result = await game.guess(event.group_id, query, str(event.user_id),
                                  guesser_name=event.sender.card or event.sender.nickname or str(event.user_id))
        await present_game_result(chuguess_cmd, result, "guess", bot, event)
    difficulty = parse_difficulty(query) if query else "mas"
    result = await game.start(event.group_id, difficulty or "mas", bot_id=str(event.self_id), delivery_context=(bot, event, "command"))
    await present_game_result(chuguess_cmd, result, "start", bot, event)


@hint_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        from .services.game import hint
        await present_game_result(hint_cmd, await hint(event.group_id), "hint", bot, event)


@giveup_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        from .services.game import give_up
        await present_game_result(giveup_cmd, await give_up(event.group_id), "give_up", bot, event)


@guess_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    import re
    query = re.sub(r"^[.。/]?猜\s*", "", event.get_plaintext().strip(), count=1)
    if not query:
        await guess_cmd.finish("请输入要猜的歌名，例如：/猜 月葬")
    from .services.game import guess
    result = await guess(event.group_id, query, str(event.user_id),
                         guesser_name=event.sender.card or event.sender.nickname or str(event.user_id))
    await present_game_result(guess_cmd, result, "guess", bot, event)
