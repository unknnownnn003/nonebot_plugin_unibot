"""Provider-owned jobs for the original query images and candidate cards."""
from __future__ import annotations

import asyncio
import hashlib
import json

from .results import outcome
from weakref import WeakValueDictionary

_submission_locks = WeakValueDictionary()

CAPABILITIES = {"player.best", "player.profile", "player.progress", "player.recommend",
                "song.search", "song.detail", "help.read"}


async def submit(bot, event, capability, arguments, *, origin="agent"):
    if capability not in CAPABILITIES or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "任务身份或能力不匹配。", status="denied")
    key = f"{capability}:{event.message_id}:" + hashlib.sha256(
        json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    from .presentation_store import scope
    identity = (scope(bot, event), key)
    lock = _submission_locks.get(identity)
    if lock is None:
        lock = asyncio.Lock()
        _submission_locks[identity] = lock
    async with lock:
        return await _submit(bot, event, capability, arguments, key, origin=origin)


async def _submit(bot, event, capability, arguments, key, *, origin):
    from .sync import existing_source, submit_operation
    old = await existing_source(bot, event, capability, key)
    if old is not None:
        if capability == "song.search":
            from .sync import _store
            return attach_candidates(old, (await _store.get(old["data"]["job_id"])).payload.get("prepared"))
        return old
    payload = {"arguments": arguments}
    if capability == "song.search":
        from .song import prepare
        prepared = await prepare(arguments["query"])
        if not prepared["song"] and not prepared["ambiguous"]:
            return outcome("NOT_FOUND", "没有找到匹配的曲目。", status="needs_input")
        payload["prepared"] = prepared
    accepted = await submit_operation(bot, event, capability, key, payload, origin=origin)
    if capability == "song.search" and accepted.get("data", {}).get("job_id"):
        from .sync import _store
        return attach_candidates(accepted, (await _store.get(accepted["data"]["job_id"])).payload.get("prepared"))
    return accepted


def attach_candidates(output, prepared):
    if output["code"] in {"JOB_ACCEPTED", "JOB_EXISTS"} and prepared and prepared["ambiguous"]:
        output["code"] = "AMBIGUOUS_TARGET"
        output["public_hint"] = output["fallback_text"] = "有多个候选，请用歌曲ID选择；候选图由原任务生成。"
        output.setdefault("data", {})["candidates"] = [
            {"id": match["song"].get("id"), "title": match["song"].get("title")} for match in prepared["ambiguous"][:8]]
    return output


async def render(bot, event, job):
    args, capability = job.payload["arguments"], job.capability_id
    from . import player, song
    if capability == "player.best":
        return await player.best(str(event.user_id), **args,
            migrate_legacy=job.response_owner == "command" and args.get("mode") == "b30")
    if capability == "player.profile":
        return await player.profile(str(event.user_id))
    if capability == "player.progress":
        return await player.progress(str(event.user_id), args["query"])
    if capability == "player.recommend":
        return await player.recommend(str(event.user_id), args.get("filter", ""))
    if capability == "song.search":
        return await song.search(args["query"], prepared=job.payload["prepared"])
    if capability == "song.detail":
        return await song.detail(args["song_id"])
    from .help import read
    return await read()


async def execute(bot, event, job, store, presentations):
    from nonebot.adapters.onebot.v11 import MessageSegment
    from .presentation_store import scope
    started, sent, cached = False, 0, False
    migration = job.capability_id == "player.best" and job.response_owner == "command" and job.payload["arguments"].get("mode") == "b30"
    async def admit():
        nonlocal started
        if str(bot.self_id) != str(event.self_id) or not await store.start_effect(job.job_id, "sending_query_media"):
            return False
        started = True
        return True
    try:
        if (await store.get(job.job_id)).status == "cancel_requested":
            return outcome("JOB_CANCELLED", "任务已取消，未继续生成。", status="cancelled")
        await store.update(job.job_id, current_step="generating_query_media",
                           side_effect_state="preparing" if migration else "none")
        output = await render(bot, event, job)
        if migration:
            output.update(effect="local_write", side_effect_state="committed")
        artifacts = output.pop("artifacts", [])
        if not artifacts:
            return output
        if (await store.get(job.job_id)).status == "cancel_requested":
            output.update(status="partial" if migration else "cancelled", code="JOB_CANCELLED",
                          public_hint="已停止后续图片发送。")
            return output
        rows = []
        identity = scope(bot, event)
        for index, artifact in enumerate(artifacts):
            if artifact["kind"] != "image":
                return outcome("UNSUPPORTED_MEDIA_PLAN", "图片尚不能建立缓存计划。", status="failed")
            rows.append(await presentations.stage(job.job_id, identity, f"{index:06}:main",
                                                  MessageSegment.image(artifact["content"])))
        cached = True
        for row in rows:
            attempt = await presentations.send(row, bot, event, before_send=admit)
            if attempt.get("denied"):
                output.update(status="partial" if migration or sent else "cancelled", code="JOB_CANCELLED",
                              public_hint="已停止后续图片发送。")
                break
            sent += attempt["delivery"] == "sent"
            if attempt["delivery"] != "sent":
                output.update(status="partial" if sent or migration else "unknown", code="MEDIA_DELIVERY_INCOMPLETE",
                              public_hint="图片未确认送达，可补发原任务缓存，无需重新查询或生成。")
                break
        else:
            if output["code"] == "AMBIGUOUS_TARGET":
                output.update(status="ok", current_step="awaiting_choice", public_hint="候选图已发送，请按原候选ID选择。")
                output.setdefault("data", {})["selection_status"] = "needs_input"
            else:
                output["public_hint"] = "查询图片已发送。"
        output.setdefault("data", {}).update(media_cached=True,
            artifact_delivery="sent" if sent == len(rows) else "partial" if sent else "unknown" if started else "not_sent")
        output["side_effect_state"] = "committed" if sent == len(rows) else "unknown" if started else "committed" if migration else "none"
        return output
    except asyncio.CancelledError:
        raise
    except Exception:
        result = outcome("MEDIA_DELIVERY_UNKNOWN" if started else "QUERY_FAILED",
            "媒体投递结果需要核对，可补发已有缓存。" if started else "查询或图片生成失败。",
            status="unknown" if started or migration else "failed", data={"media_cached": cached})
        result["side_effect_state"] = "unknown" if started or migration else "none"
        return result
