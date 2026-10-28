"""Persistent job-backed wrapper for the original long-running LXNS sync."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .results import outcome


async def _run_sync(user_id: str):
    from .. import get_player_score as s
    import httpx

    token = (s.config.lxns_token or "").strip()
    if not token:
        return outcome("UPSTREAM_UNAVAILABLE", "未配置落雪开发者密钥，无法同步。", status="unavailable")
    target = Path(s.SCORE_DIR) / f"{user_id}.json"
    info_path = Path(s.SCORE_DIR) / f"{user_id}_info.json"
    try:
        async with httpx.AsyncClient() as client:
            friend_code, player_data, _ = await s._resolve_friend_code(client, user_id, token)
            if not friend_code:
                return outcome("NOT_BOUND", "请先绑定落雪好友码。", status="needs_input")
            rows, status, song_count, failed_required, unavailable_required, failed_optional = await s._fetch_lxns_all_scores(client, friend_code, token)
            if status == 401:
                return outcome("PERMISSION_DENIED", "落雪开发者密钥无效或权限不足。", status="denied")
            if status == 404:
                return outcome("NOT_FOUND", "落雪没有找到该玩家成绩。", status="needs_input")
            if status != 200:
                return outcome("UPSTREAM_UNAVAILABLE", "落雪成绩接口请求失败。", status="unavailable", retryable=True)
            parsed, missing = s._normalize_lxns_score_items(rows)
            if not parsed:
                return outcome("INVALID_DATA" if missing else "NO_SCORES", "落雪没有返回可写入的成绩。", status="needs_input")
            if failed_required:
                return outcome("PARTIAL_UPSTREAM", "关键谱面请求失败，本次取消写入。", status="failed", retryable=True)
            merged = s._merge_score_items(s._read_score_json(str(target)), parsed)
            merged = s._inject_level_value(merged, s._load_song_meta())
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = str(target) + ".tmp"
            with open(temp, "w", encoding="utf-8") as file:
                json.dump(merged, file, ensure_ascii=False, indent=4)
            Path(temp).replace(target)
            if player_data:
                temp_info = str(info_path) + ".tmp"
                with open(temp_info, "w", encoding="utf-8") as file:
                    json.dump(s._normalize_lxns_player_info(player_data), file, ensure_ascii=False, indent=4)
                Path(temp_info).replace(info_path)
            result = outcome(data={"user_id": user_id, "song_count": song_count, "fetched": len(parsed),
                                   "total": len(merged), "unavailable_required": unavailable_required,
                                   "failed_optional": failed_optional})
            result["effect"] = "write"
            return result
    except httpx.TimeoutException:
        return outcome("UPSTREAM_TIMEOUT", "落雪接口请求超时。", status="unknown", retryable=True)
    except Exception:
        return outcome("INTERNAL_ERROR", "成绩同步失败，请稍后重试。", status="unavailable")


async def submit(bot, event, user_id: str):
    from nonebot_plugin_localstore import get_plugin_data_dir
    try:
        from nonebot_plugin_llmchat_jobs import JobStore, run_one, notify_once
    except ImportError:
        return outcome("JOB_SYSTEM_UNAVAILABLE", "任务系统未加载，暂时不能排队同步。", status="unavailable")
    store = JobStore(Path(get_plugin_data_dir()) / "jobs.sqlite3")
    job, created = await store.submit(provider="nonebot_plugin_unibot", capability_id="score.sync",
                                      operation_id=f"lxns:{user_id}", bot_id=str(bot.self_id),
                                      context_type="group" if getattr(event, "group_id", None) is not None else "private",
                                      context_id=str(getattr(event, "group_id", None) or event.user_id),
                                      actor_id=str(user_id), source_message_id=str(getattr(event, "message_id", "")),
                                      payload={"user_id": str(user_id)})
    if created:
        async def handler(_job):
            return await _run_sync(str(user_id))
        async def worker():
            finished = await run_one(store, handler)
            if finished:
                async def notify(item):
                    await bot.send(event, f"成绩同步任务 {item.job_id}：{item.result.get('public_hint') or item.status}")
                await notify_once(store, finished.job_id, notify)
        asyncio.create_task(worker())
    result = outcome(data={"job_id": job.job_id, "deduplicated": not created})
    result["effect"] = "write"
    result.update(status="ok", code="JOB_ACCEPTED", public_hint="成绩同步已排队。")
    return result
