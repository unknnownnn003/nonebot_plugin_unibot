"""Bounded write services for the original binding and manual-score stores."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os

from .results import outcome


async def bind(user_id: str, friend_code: str):
    from .. import user_bind
    friend_code = str(friend_code).strip()
    if not friend_code.isdigit() or len(friend_code) > 32:
        return outcome("INVALID_ARGUMENT", "好友码必须是有效数字。", status="needs_input")
    ok = await asyncio.to_thread(user_bind.save_bind_info, str(user_id), friend_code)
    if not ok:
        result = outcome("WRITE_FAILED", "绑定保存失败，请稍后重试。", status="unavailable")
        result["effect"] = "write"
        return result
    result = outcome(data={"user_id": str(user_id), "bound": True})
    result["effect"] = "local_write"
    result["side_effect_state"] = "committed"
    return result


async def submit_manual(user_id: str, query: str, level_index: int, score: int, combo: str = ""):
    from .. import get_player_score as s
    from ..song import find_song_matches, pick_song_match
    if not (0 <= int(score) <= 1_010_000) or int(level_index) not in s.DIFF_NAMES:
        result = outcome("INVALID_ARGUMENT", "分数或难度不在允许范围内。", status="needs_input"); result["effect"] = "write"; return result
    matches = await asyncio.to_thread(find_song_matches, str(query), 8)
    song, ambiguous = pick_song_match(matches)
    if ambiguous:
        result = outcome("AMBIGUOUS_TARGET", "匹配到多首曲目，请提供明确 ID。", status="needs_input",
                         data={"candidates": [{"id": x["song"].get("id"), "title": x["song"].get("title")} for x in ambiguous[:8]]})
        result["effect"] = "write"; return result
    if not song:
        result = outcome("NOT_FOUND", "未找到匹配的曲目。", status="needs_input"); result["effect"] = "write"; return result
    diff = next((d for d in song.get("difficulties", []) if int(d.get("difficulty", -1)) == int(level_index)), None)
    if not diff:
        result = outcome("INVALID_ARGUMENT", "该曲目没有对应难度。", status="needs_input"); result["effect"] = "write"; return result
    now = datetime.now(timezone.utc).isoformat()
    row = {"id": str(song.get("id")), "level_index": int(level_index), "score": int(score),
           "song_name": song.get("title", ""), "level": diff.get("level"),
           "level_value": diff.get("level_value"), "lx_level_value": diff.get("lx_level_value"),
           "chunirec_level_value": diff.get("chunirec_level_value"), "rank": "", "clear": "",
           "full_combo": s.normalize_manual_combo(combo), "source": "manual", "manual": True, "updated_at": now}
    from .storage import commit_scores
    try:
        await asyncio.to_thread(commit_scores, str(user_id), [row], manual=True)
    except Exception:
        result = outcome("WRITE_FAILED", "手动成绩保存失败，请稍后重试。", status="unavailable")
        result["effect"] = "none"
        return result
    result = outcome(data={"user_id": str(user_id), "song_id": str(song.get("id")),
                           "title": song.get("title", ""), "level_index": int(level_index), "score": int(score)})
    result["effect"] = "local_write"
    result["side_effect_state"] = "committed"
    return result
