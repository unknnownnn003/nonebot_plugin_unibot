"""Read-only view of the existing per-group picture guessing game."""
from __future__ import annotations

import asyncio
import time

from .results import outcome


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
