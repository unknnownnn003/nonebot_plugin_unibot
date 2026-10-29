"""Durable transport for an already-applied guessing-game result.

No game function is called here or from replay. Normal native presentation stays
inline; this record tracks media delivery, not an invented queued game action.
"""
from __future__ import annotations

import asyncio

from .results import outcome


async def deliver(bot, event, content, source_capability, business=None, *, notify=False):
    from . import sync
    if sync._closing:
        return outcome("PLUGIN_CLOSING", "猜曲操作已生效，插件关闭中，媒体需要核对。", status="partial")
    task = asyncio.current_task()
    sync._inline_tasks.add(task)
    try:
        return await _deliver(bot, event, content, source_capability, business, notify=notify)
    finally:
        sync._inline_tasks.discard(task)


async def _deliver(bot, event, content, source_capability, business=None, *, notify=False):
    from . import sync
    from .presentation_store import scope
    if str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "媒体Bot/会话不匹配。", status="denied")
    try:
        store = await sync.ensure_started()
        job, created = await store.begin_inline(provider=sync.PROVIDER,
            operation_id=f"{source_capability}:{event.message_id}", source_message_id=str(event.message_id),
            payload={"source_capability": source_capability}, response_owner="agent" if notify else "inline",
            initial_result={"data": {"source_capability": source_capability, "business_snapshot": business or {}}},
            **sync._scope(bot, event))
    except asyncio.CancelledError:
        raise
    except Exception:
        return outcome("MEDIA_STORE_UNAVAILABLE", "猜曲操作已生效，媒体未能登记；请查询状态，不要重执行业务。", status="partial",
                       data={"artifact_delivery": "not_sent", "media_cached": False, "business_snapshot": business or {}})
    if not created:
        return outcome("ALREADY_HANDLED", "该投递已接管，不重复游戏操作或消息。", status="unknown",
                       data={"job_id": job.job_id, "artifact_delivery": job.result.get("data", {}).get("artifact_delivery", "unknown")})
    started = False
    async def admit():
        nonlocal started
        started = await store.start_effect(job.job_id, "sending_game_media")
        return started
    try:
        row = await sync._presentations.stage(job.job_id, scope(bot, event), "000000:main", content)
        attempt = await sync._presentations.send(row, bot, event, before_send=admit)
        delivery = attempt["delivery"]
        state = "succeeded" if delivery == "sent" else "cancelled" if attempt.get("denied") else "unknown" if started else "failed"
        result = outcome("OK" if delivery == "sent" else "MEDIA_DELIVERY_INCOMPLETE",
            "猜曲操作已生效，媒体已发送。" if delivery == "sent" else "猜曲操作已生效，媒体未确认送达；可补发缓存，不会再消耗提示或记分。",
            status="ok" if state == "succeeded" else state,
            data={"job_id": job.job_id, "artifact_delivery": delivery, "media_cached": True,
                  "source_capability": source_capability, "business_snapshot": business or {}})
        await store.update(job.job_id, status=state, current_step="finished", result=result,
            side_effect_state="committed" if delivery == "sent" else "unknown" if started else "none",
            delivery_state=delivery, notification_state="pending" if notify else "not_required")
    except BaseException as exc:
        result = outcome("MEDIA_DELIVERY_UNKNOWN" if started else "MEDIA_STORE_UNAVAILABLE",
                         "猜曲操作已生效，媒体记录或投递需要核对，不会重执行业务。", status="unknown",
                         data={"job_id": job.job_id, "source_capability": source_capability, "business_snapshot": business or {}})
        await store.update(job.job_id, status="unknown" if started else "failed", current_step="interrupted",
            result=result, side_effect_state="unknown" if started else "none", notification_state="pending" if notify else "not_required")
        if isinstance(exc, asyncio.CancelledError):
            raise
    if notify:
        sync._notify_wake.set()
    return result


async def deliver_artifacts(bot, event, artifacts, source_capability, business=None):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    content = Message()
    for artifact in artifacts:
        if artifact["kind"] == "image":
            content += MessageSegment.image(artifact["content"])
        elif artifact["kind"] == "message":
            content += artifact["content"]
        else:
            return outcome("UNSUPPORTED_MEDIA_PLAN", "猜曲已生效，媒体需核对。", status="partial")
    return await deliver(bot, event, content, source_capability, business)
