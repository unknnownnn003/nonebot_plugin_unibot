"""Provider-owned jobs for the original query images and candidate cards."""
from __future__ import annotations

import asyncio
import hashlib
import json

from .results import outcome
from weakref import WeakValueDictionary

_submission_locks = WeakValueDictionary()

CAPABILITIES = {"player.b50", "player.best", "player.profile", "player.progress", "player.recommend",
                "player.song_scores", "song.search", "song.detail", "help.read"}


async def submit(bot, event, capability, arguments, *, origin="agent"):
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    if not isinstance(event, (PrivateMessageEvent, GroupMessageEvent)) or capability not in CAPABILITIES or str(bot.self_id) != str(event.self_id):
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
    from .player_data import PLAYER_CAPABILITIES, get_reader, failure
    from .oauth import OAuthError
    from . import group_policy
    if not group_policy.enabled(event, capability):
        return outcome("FEATURE_DISABLED", "本群对应功能已关闭。", status="denied")
    if capability in PLAYER_CAPABILITIES:
        try:
            _, payload["oauth_binding"] = await get_reader().oauth.credential_lease(bot.self_id, event.user_id)
        except OAuthError as error:
            return failure(error)
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
        output["public_hint"] = output["fallback_text"] = "有多个候选，请用歌曲ID选择。"
        output.setdefault("data", {})["candidates"] = [
            {"id": match["song"].get("id"), "title": match["song"].get("title")} for match in prepared["ambiguous"][:8]]
    return output


async def render(bot, event, job, checkpoint=None):
    args, capability = job.payload["arguments"], job.capability_id
    from . import player, song
    from .player_data import PLAYER_CAPABILITIES
    if capability in PLAYER_CAPABILITIES:
        if not job.payload.get("oauth_binding"):
            return outcome("AUTHORIZATION_CHANGED", "历史任务没有本次 OAuth 授权，请重新查询。", status="denied")
        return await player.query(bot, event, capability, args, expected=job.payload["oauth_binding"], checkpoint=checkpoint)
    if capability == "song.search":
        return await song.search(args["query"], prepared=job.payload["prepared"])
    if capability == "song.detail":
        return await song.detail(args["song_id"])
    from .help import read
    return await read()


async def execute(bot, event, job, store, presentations):
    from nonebot.adapters.onebot.v11 import MessageSegment
    from .presentation_store import scope
    from .player_data import job_authorized, PLAYER_CAPABILITIES, PlayerError, failure
    from . import group_policy
    started, sent, cached = False, 0, False
    async def checkpoint():
        current = await store.get(job.job_id)
        if current is None or current.status in {"cancel_requested", "cancelled"}:
            raise PlayerError("JOB_CANCELLED")
        if not group_policy.enabled(event, job.capability_id):
            raise PlayerError("FEATURE_DISABLED")
        if not await job_authorized(job):
            raise PlayerError("AUTHORIZATION_CHANGED")
    async def admit():
        nonlocal started
        try:
            await checkpoint()
        except PlayerError:
            return False
        if not await store.start_effect(job.job_id, "sending_query_media"):
            return False
        # start_effect awaits a thread: check live authorization once more.
        try:
            await checkpoint()
        except PlayerError:
            return False
        started = True
        return True
    try:
        await checkpoint()
        await store.update(job.job_id, current_step="generating_query_media", side_effect_state="none")
        from .sync import _fetch_with_cancel
        output = await _fetch_with_cancel(render(bot, event, job, checkpoint), job, store)
        if output is None:
            raise PlayerError("JOB_CANCELLED")
        await checkpoint()
        # Failed queries are presented once by the task notifier, never cached
        # as successful media or followed by a misleading delivery receipt.
        if output.get("status") != "ok" and output.get("code") != "AMBIGUOUS_TARGET":
            return output
        artifacts = output.pop("artifacts", [])
        contents = [MessageSegment.image(a["content"]) for a in artifacts if a["kind"] == "image"]
        if len(contents) != len(artifacts):
            return outcome("UNSUPPORTED_MEDIA_PLAN", "媒体类型尚不支持。", status="failed")
        text = output.get("public_hint") or output.get("fallback_text")
        if job.capability_id in {"player.song_scores", "player.best", "player.b50", "player.progress"} and text:
            contents.append(MessageSegment.text(text))
        elif contents and output.get("data", {}).get("unmatched_scores"):
            contents.append(MessageSegment.text(text))
        if not contents:
            return output
        rows = []
        identity = scope(bot, event)
        for index, content in enumerate(contents):
            await checkpoint()
            rows.append(await presentations.stage(job.job_id, identity, f"{index:06}:main", content))
        cached = True
        for row in rows:
            attempt = await presentations.send(row, bot, event, before_send=admit)
            if attempt.get("denied"):
                return outcome("DELIVERY_STOPPED", "已停止后续发送，请核对授权、任务及群设置。", status="partial" if sent else "cancelled")
            sent += attempt["delivery"] == "sent"
            if attempt["delivery"] != "sent":
                output.update(status="partial" if sent else "unknown", code="MEDIA_DELIVERY_INCOMPLETE",
                              public_hint="投递未确认送达，可在本次授权有效时补发原任务。")
                break
        else:
            if output["code"] == "AMBIGUOUS_TARGET":
                output.update(status="ok", current_step="awaiting_choice")
                output.setdefault("data", {})["selection_status"] = "needs_input"
            output["public_hint"] = "查询结果已发送。"
        output.setdefault("data", {}).update(media_cached=True, artifact_delivery="sent" if sent == len(rows) else "partial" if sent else "unknown" if started else "not_sent")
        output["side_effect_state"] = "committed" if sent == len(rows) else "unknown" if started else "none"
        return output
    except PlayerError as error:
        return failure(error)
    except asyncio.CancelledError:
        raise
    except Exception:
        result = outcome("MEDIA_DELIVERY_UNKNOWN" if started else "QUERY_FAILED",
            "媒体投递结果需要核对。" if started else "查询或图片生成失败。",
            status="unknown" if started else "failed", data={"media_cached": cached})
        result["side_effect_state"] = "unknown" if started else "none"
        return result
