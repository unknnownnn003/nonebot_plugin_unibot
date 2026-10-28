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
