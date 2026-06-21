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
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
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
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=4)


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


async def game_timeout(group_id: str) -> None:
    await asyncio.sleep(GAME_TIMEOUT)
    if group_id not in active_games:
        return
    game = active_games.pop(group_id, None)
    game_tasks.pop(group_id, None)
    if game is None:
        return

    result_img = make_game_result_image(
        game["song"],
        game["jacket"],
        game["crop_x"],
        game["crop_y"],
        game["crop_w"],
        game["crop_h"],
        game["difficulty"],
        game["score"],
        guessed=False,
    )
    try:
        bot = get_bot()
        await bot.send_group_msg(
            group_id=int(group_id),
            message=Message(
                f"时间到！答案是：{game['song'].get('title', 'Unknown')}\n"
                + MessageSegment.image(result_img)
            ),
        )
    except Exception as e:
        logger.error(f"发送超时消息失败: {e}")


def parse_difficulty(text: str) -> Optional[str]:
    t = text.strip().lower()
    if t in DIFFICULTY_CONFIG:
        return t
    if t in DIFF_ALIASES:
        return DIFF_ALIASES[t]
    return None


@chuguess_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()
    group_id = None
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
    else:
        await chuguess_cmd.finish("曲绘猜曲仅在群聊中可用。")
        return

    async with await _get_group_lock(group_id):

        # Check if there's an active game in this group
        game = active_games.get(group_id)

        if game and not arg_text:
            # No args during active game — show current status
            diff_cfg = DIFFICULTY_CONFIG[game["difficulty"]]
            elapsed = int(time.time() - game["start_time"])
            remaining = max(0, GAME_TIMEOUT - elapsed)
            hints_remaining = [h for h in HINT_TYPES if h not in game["hints_used"] or h in ("more_area", "name_part")]
            current_score = calc_actual_score(diff_cfg["score"], game["hints_used"])
            await chuguess_cmd.finish(
                f"当前游戏进行中 — {diff_cfg['name']} 难度 (当前{current_score}分)\n"
                f"剩余时间: {remaining}秒\n"
                f"可用提示类型: {len(hints_remaining)} 种\n"
                f"发送 /chuguess <歌名> 答题 | /提示 获取提示 | /放弃 放弃"
            )

        if game and arg_text:
            # Game active — treat arg as a guess (never restart with new diff)
            query = arg_text
            matched = match_song_by_query(query)
            if not matched:
                await chuguess_cmd.finish("没有匹配到歌曲，再猜猜看？")
                return

            matched_id = str(matched.get("id", ""))
            game_song_id = str(game["song"].get("id", ""))

            if matched_id == game_song_id:
                # Correct!
                old_task = game_tasks.pop(group_id, None)
                if old_task:
                    old_task.cancel()
                active_games.pop(group_id, None)

                qq = str(event.get_user_id())
                base_score = game["score"]
                actual_score = calc_actual_score(base_score, game["hints_used"])
                async with _score_lock:
                    scores = load_scores()
                    scores[qq] = scores.get(qq, 0) + actual_score
                    save_scores(scores)

                result_img = make_game_result_image(
                    game["song"],
                    game["jacket"],
                    game["crop_x"],
                    game["crop_y"],
                    game["crop_w"],
                    game["crop_h"],
                    game["difficulty"],
                    actual_score,
                    guessed=True,
                    guesser_name=event.sender.card or event.sender.nickname or qq,
                )

                total_score = scores[qq]
                hint_count = len(game["hints_used"] & SCORE_HINT_TYPES)
                hint_info = f" (使用了{hint_count}个提示，基础{base_score}分)" if hint_count > 0 else ""
                await chuguess_cmd.finish(
                    Message(
                        f"恭喜猜中！答案是：{game['song'].get('title', '')}\n"
                        f"+{actual_score} 分!{hint_info} (总分: {total_score})\n"
                        + MessageSegment.image(result_img)
                    )
                )
            else:
                await chuguess_cmd.finish("不对哦，再猜猜看～")
            return

        # Start a new game (only when no game is active)
        diff_key = parse_difficulty(arg_text) if arg_text else "mas"
        if diff_key is None:
            diff_key = "mas"

        # Cancel any existing game for this group (shouldn't have one, but be safe)
        old_task = game_tasks.pop(group_id, None)
        if old_task:
            old_task.cancel()
        active_games.pop(group_id, None)

        result = pick_random_song_and_jacket()
        if result is None:
            await chuguess_cmd.finish("未能加载曲绘，请检查曲库和曲绘数据。")
            return

        song, jacket, _jacket_id = result
        diff_cfg = DIFFICULTY_CONFIG[diff_key]

        cropped, cx, cy, cw, ch = crop_jacket(jacket, diff_cfg["ratio"])

        game_state = {
            "song": song,
            "jacket": jacket,
            "crop_x": cx,
            "crop_y": cy,
            "crop_w": cw,
            "crop_h": ch,
            "difficulty": diff_key,
            "score": diff_cfg["score"],
            "start_time": time.time(),
            "hints_used": set(),
            "name_reveal_step": 0,
            "revealed_areas": [(cx, cy, cw, ch)],
            "group_id": group_id,
        }

        active_games[group_id] = game_state

        # Start timeout task
        task = asyncio.create_task(game_timeout(group_id))
        game_tasks[group_id] = task

        # Send the cropped image
        crop_buf = io.BytesIO()
        cropped.convert("RGB").save(crop_buf, format="JPEG", quality=90)

        await chuguess_cmd.finish(
            Message(
                f"曲绘猜曲 - {diff_cfg['name']} 难度\n"
                f"猜出这首歌的名字！发送 /chuguess <歌名> 来答题\n"
                f"发送 /提示 获取提示 | /放弃 放弃本题\n"
                f"最多可获得 {diff_cfg['score']} 分（使用提示会降低分数）\n"
                + MessageSegment.image(crop_buf.getvalue())
            )
        )


@hint_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    group_id = str(event.group_id)
    game = active_games.get(group_id)
    if not game:
        await hint_cmd.finish("当前没有进行中的猜曲游戏。使用 /chuguess 开始一局！")
        return

    hint_result = await get_hint_text(game)
    if hint_result is None:
        await hint_cmd.finish("获取提示失败，请稍后再试。")
        return

    hint_type, hint_content = hint_result
    if hint_type == "text":
        await hint_cmd.finish(hint_content)
    elif hint_type == "location":
        await hint_cmd.finish(Message([MessageSegment.text("[提示] 截取位置:"), hint_content]))
    elif hint_type == "more_area":
        await hint_cmd.finish(Message([MessageSegment.text("[提示] 额外曲绘区域:"), hint_content]))


@giveup_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    group_id = str(event.group_id)
    game = active_games.pop(group_id, None)
    task = game_tasks.pop(group_id, None)
    if task:
        task.cancel()

    if not game:
        await giveup_cmd.finish("当前没有进行中的猜曲游戏。")
        return

    result_img = make_game_result_image(
        game["song"],
        game["jacket"],
        game["crop_x"],
        game["crop_y"],
        game["crop_w"],
        game["crop_h"],
        game["difficulty"],
        game["score"],
        guessed=False,
        end_reason="giveup",
    )
    await giveup_cmd.finish(
        Message(
            f"已放弃。答案是：{game['song'].get('title', 'Unknown')}\n"
            + MessageSegment.image(result_img)
        )
    )


@guess_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    group_id = str(event.group_id)
    game = active_games.get(group_id)
    if not game:
        return

    # Extract the guess text from the regex match
    msg = event.get_plaintext().strip()
    # Remove leading ./。/猜 prefix
    for prefix in ["猜 ", "猜", ".猜 ", ".猜", "/猜 ", "/猜", "。猜 ", "。猜"]:
        if msg.startswith(prefix):
            query = msg[len(prefix):].strip()
            break
    else:
        query = ""

    if not query:
        await guess_cmd.finish("请输入要猜的歌名，例如：/猜 月葬")
        return

    matched = match_song_by_query(query)
    if not matched:
        await guess_cmd.finish("没有匹配到歌曲，再猜猜看？")
        return

    matched_id = str(matched.get("id", ""))
    game_song_id = str(game["song"].get("id", ""))

    if matched_id == game_song_id:
        old_task = game_tasks.pop(group_id, None)
        if old_task:
            old_task.cancel()
        active_games.pop(group_id, None)

        qq = str(event.get_user_id())
        base_score = game["score"]
        actual_score = calc_actual_score(base_score, game["hints_used"])
        async with _score_lock:
            scores = load_scores()
            scores[qq] = scores.get(qq, 0) + actual_score
            save_scores(scores)

        result_img = make_game_result_image(
            game["song"],
            game["jacket"],
            game["crop_x"],
            game["crop_y"],
            game["crop_w"],
            game["crop_h"],
            game["difficulty"],
            actual_score,
            guessed=True,
            guesser_name=event.sender.card or event.sender.nickname or qq,
        )

        total_score = scores[qq]
        hint_count = len(game["hints_used"] & SCORE_HINT_TYPES)
        hint_info = f" (使用了{hint_count}个提示，基础{base_score}分)" if hint_count > 0 else ""
        await guess_cmd.finish(
            Message(
                f"恭喜猜中！答案是：{game['song'].get('title', '')}\n"
                f"+{actual_score} 分!{hint_info} (总分: {total_score})\n"
                + MessageSegment.image(result_img)
            )
        )
    else:
        await guess_cmd.finish("不对哦，再猜猜看～")
