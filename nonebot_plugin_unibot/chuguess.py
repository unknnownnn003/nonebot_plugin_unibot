from .services.error_notices import error_call as _error_call, result_call as _result_call
import asyncio
import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, artwork, frame, lines
from .services.image_components import label, png, rounded, wrap
from .services.job_store import async_call
from PIL import Image, ImageDraw
from nonebot import get_bot, on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from .services.storage import atomic_json
from .song import (

    get_version_name,
    match_song_by_query,
)

from .services.paths import DATA_DIR as _DATA_DIR
DATA_DIR = str(_DATA_DIR)
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
from .services.resources import local_jacket
from .services.paths import JACKET_DIR
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
        from .services.song_model import Catalog
        catalog = Catalog(data, legacy=True)
        versions = catalog.metadata.get("versions", [])
        return [dict(song.as_dict(), _versions=versions, parent_song_id=catalog.parent_id(song.id))
                for song in catalog.songs.values()]
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

    max_dim = min(w, h)
    min_dim = min(max_dim, max(15, int(math.sqrt(target_area)) // 3))

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
    overlay = Image.new("RGB", jacket.size, "#c4dadb")
    mask = Image.new("L", jacket.size, 255)
    mask_draw = ImageDraw.Draw(mask)
    x1, y1 = max(0, x - 3), max(0, y - 3)
    x2, y2 = min(jw, x + w + 3), min(jh, y + h + 3)
    mask_draw.rectangle([x1, y1, x2, y2], fill=0)
    preview = jacket.copy()
    preview.paste(overlay, mask=mask)
    ImageDraw.Draw(preview).rectangle((x1, y1, x2, y2), outline=TEAL, width=max(2, jw // 100))
    return preview


def make_revealed_preview(jacket: Image.Image, revealed_areas: List[Tuple[int, int, int, int]]) -> Image.Image:
    overlay = Image.new("RGB", jacket.size, "#c4dadb")
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
    from .services.song_model import jacket_id as resource_id
    for song in songs:
        # Associated WE charts use the same question jacket as their ordinary song.
        if song.get("parent_song_id", song["id"]) != song["id"]:
            continue
        jacket_id = str(resource_id(song))
        if jacket_id in available_ids:
            candidates.append((song, jacket_id))

    if not candidates:
        return None

    random.shuffle(candidates)
    for song, jacket_id in candidates:
        jacket = local_jacket(jacket_id)
        if jacket is not None:
            return song, jacket, jacket_id
    return None


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
    status, color = ("猜中", TEAL) if guessed else (
        ("已放弃", "#995d37") if end_reason == "giveup" else ("时间到", "#42658f"))
    title = wrap(song.get("title") or "—", 34, 712)
    result = f"猜中者  {guesser_name}" if guessed else "无人得分"
    details = wrap(result, 26, 712)
    height = 574 + len(title) * 44 + len(details) * 36
    canvas = frame("曲绘猜曲", height)
    rounded(canvas, (20, 82, 780, height - 20), 16, PAPER, LINE)
    label(canvas, (42, 94, 420, 144), status, 33, color)
    summary = DIFFICULTY_CONFIG[difficulty]["name"]
    label(canvas, (430, 146, 756, 180), summary, 24, MUTED, "right")
    if guessed:
        label(canvas, (430, 94, 756, 146), f"+{score} 分", 42, color, "right")
    artwork(canvas, jacket, (238, 156, 562, 480))
    label(canvas, (44, 489, 756, 521), "正确答案", 24, color)
    y = lines(canvas, title, 44, 522, 756, 34, 44, INK)
    lines(canvas, details, 44, y + 9, 756, 26, 36, MUTED)
    return png(canvas)


def make_clue_image(picture, heading):
    canvas = frame("曲绘猜曲", 506)
    rounded(canvas, (20, 82, 780, 486), 16, PAPER, LINE)
    label(canvas, (42, 94, 756, 139), heading, 29, TEAL)
    artwork(canvas, picture, (238, 146, 562, 470))
    return png(canvas)


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
        version_name = get_version_name(version_raw, song.get("_versions", []))
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
        content = await async_call(make_clue_image, preview, "截取位置")
        return ("location", MessageSegment.image(content))

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
        content = await async_call(make_clue_image, preview, "额外曲绘区域")
        return ("more_area", MessageSegment.image(content))

    return None


async def game_timeout(group_id: str, expected=None) -> None:
    from .services.group_policy import enabled
    from types import SimpleNamespace
    if not enabled(SimpleNamespace(group_id=int(group_id)), "game.status"):
        async with await _get_group_lock(group_id):
            active_games.pop(str(group_id), None)
        return
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
            await deliver(bot, event, content, "game.timeout", result["data"], notify=owner == "agent")
        else:
            await bot.send_group_msg(group_id=int(group_id), message=Message(
                MessageSegment.image(result["artifacts"][0]["content"])))
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
        await _result_call(result, matcher.finish, result["fallback_text"])
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
        text = f"累计 {data['total_score']} 分{extra}\n"
    elif mode == "give_up":
        text = ""
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
        await _error_call(guess_cmd.finish, "请输入要猜的歌名，例如：/猜 月葬")
    from .services.game import guess
    result = await guess(event.group_id, query, str(event.user_id),
                         guesser_name=event.sender.card or event.sender.nickname or str(event.user_id))
    await present_game_result(guess_cmd, result, "guess", bot, event)
