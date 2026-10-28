"""Read-only view of the existing per-group picture guessing game."""
from __future__ import annotations

import asyncio
import time

from .results import outcome, image


async def status(group_id: int):
    from .. import chuguess

    game = await asyncio.to_thread(chuguess.active_games.get, str(group_id))
    if not game:
        return outcome("NO_ACTIVE_GAME", "当前群没有进行中的猜曲游戏。", status="needs_input")
    hints = game.get("hints_used", set())
    return outcome(data={"group_id": int(group_id), "active": True,
                         "difficulty": str(game.get("difficulty", "")),
                         "score": game.get("score"),
                         "hints_used": len(hints) if hasattr(hints, "__len__") else 0,
                         "elapsed_seconds": max(0, int(time.time() - float(game.get("start_time", time.time()))))})


async def hint(group_id: int):
    from .. import chuguess
    game = chuguess.active_games.get(str(group_id))
    if not game:
        return outcome("NO_ACTIVE_GAME", "当前群没有进行中的猜曲游戏。", status="needs_input")
    hint_type, content = await chuguess.get_hint_text(game)
    result = outcome(data={"group_id": int(group_id), "hint_type": hint_type})
    result["effect"] = "write"
    if isinstance(content, (bytes, bytearray)):
        result["artifacts"] = [image(bytes(content))]
    else:
        result["data"]["hint"] = str(content)
    return result


async def give_up(group_id: int):
    from .. import chuguess
    key = str(group_id)
    game = chuguess.active_games.pop(key, None)
    task = chuguess.game_tasks.pop(key, None)
    if task:
        task.cancel()
    if not game:
        return outcome("NO_ACTIVE_GAME", "当前群没有进行中的猜曲游戏。", status="needs_input")
    song = game.get("song", {})
    result = outcome(data={"group_id": int(group_id), "title": str(song.get("title", "Unknown")),
                           "difficulty": str(game.get("difficulty", ""))})
    result["effect"] = "write"
    return result


async def start(group_id: int, difficulty: str = "mas"):
    from .. import chuguess
    key = str(group_id)
    if key in chuguess.active_games:
        return outcome("ALREADY_ACTIVE", "当前群已有进行中的猜曲游戏。", status="needs_input")
    difficulty = difficulty.lower()
    if difficulty not in chuguess.DIFFICULTY_CONFIG:
        return outcome("INVALID_ARGUMENT", "难度必须是 bas、adv、exp、mas 或 ult。", status="needs_input")
    picked = await asyncio.to_thread(chuguess.pick_random_song_and_jacket)
    if not picked:
        return outcome("NO_SONG", "当前没有可用曲绘。", status="unavailable")
    song, jacket, _ = picked
    cfg = chuguess.DIFFICULTY_CONFIG[difficulty]
    cropped, cx, cy, cw, ch = chuguess.crop_jacket(jacket, cfg["ratio"])
    chuguess.active_games[key] = {"song": song, "jacket": jacket, "crop_x": cx, "crop_y": cy,
                                  "crop_w": cw, "crop_h": ch, "difficulty": difficulty,
                                  "score": cfg["score"], "start_time": time.time(), "hints_used": set(),
                                  "name_reveal_step": 0, "revealed_areas": [(cx, cy, cw, ch)], "group_id": key}
    chuguess.game_tasks[key] = asyncio.create_task(chuguess.game_timeout(key))
    import io
    buffer = io.BytesIO(); cropped.convert("RGB").save(buffer, format="JPEG", quality=90)
    result = outcome(data={"group_id": int(group_id), "difficulty": difficulty, "score": cfg["score"]})
    result["effect"] = "write"
    result["artifacts"] = [{"kind": "image", "content": buffer.getvalue()}]
    return result


async def guess(group_id: int, query: str, user_id: str):
    from .. import chuguess
    key = str(group_id); game = chuguess.active_games.get(key)
    if not game:
        return outcome("NO_ACTIVE_GAME", "当前群没有进行中的猜曲游戏。", status="needs_input")
    matched = await asyncio.to_thread(chuguess.match_song_by_query, query)
    if not matched:
        return outcome("NOT_FOUND", "没有匹配到歌曲。", status="needs_input")
    if str(matched.get("id")) != str(game["song"].get("id")):
        return outcome("WRONG_GUESS", "答案不对，再猜猜看。", status="needs_input")
    task = chuguess.game_tasks.pop(key, None)
    if task: task.cancel()
    chuguess.active_games.pop(key, None)
    score = chuguess.calc_actual_score(game["score"], game["hints_used"])
    async with chuguess._score_lock:
        scores = chuguess.load_scores(); scores[str(user_id)] = scores.get(str(user_id), 0) + score; chuguess.save_scores(scores)
    image_bytes = chuguess.make_game_result_image(game["song"], game["jacket"], game["crop_x"], game["crop_y"], game["crop_w"], game["crop_h"], game["difficulty"], score, guessed=True, guesser_name=str(user_id))
    result = outcome(data={"group_id": int(group_id), "title": game["song"].get("title", ""), "score": score, "total_score": scores[str(user_id)]})
    result["effect"] = "write"; result["artifacts"] = [image(image_bytes)]
    return result
