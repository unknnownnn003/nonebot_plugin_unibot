from . import group_policy
"""Persistent query/resource FIFO, scoped cancellation and guarded replay."""
from .error_notices import result_call
import asyncio
from pathlib import Path
from .results import outcome
from .job_store import JobStore, QueueFull, run_one, notify_once

PROVIDER = "nonebot_plugin_unibot"
_store = None
_presentations = None
_task = None
_notifier_task = None
_notify_wake = asyncio.Event()
_contexts = {}
_inline_tasks = set()
_wake = asyncio.Event()
_gate = asyncio.Lock()
_lifecycle = asyncio.Lock()
_closing = False


def valid_request(bot, event):
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    return isinstance(event, (PrivateMessageEvent, GroupMessageEvent)) and str(bot.self_id) == str(event.self_id)


def _scope(bot, event):
    group = getattr(event, "group_id", None)
    return dict(bot_id=str(bot.self_id), context_type="group" if group is not None else "private",
                context_id=str(group if group is not None else event.user_id), actor_id=str(event.user_id))


async def ensure_started():
    global _store, _presentations, _task, _notifier_task, _closing
    async with _lifecycle:
        if _closing and _store is not None:
            return _store
        if _store is None:
            from .paths import DATA_DIR
            _store = JobStore(DATA_DIR / "native_jobs.sqlite3")
            await _store.init()
            # No reconstructed Event can manufacture old authorization. Jobs
            # whose real request context was lost are visible as interrupted.
            await _store.mark_interrupted()
            from .presentation_store import PresentationStore
            _presentations = PresentationStore(Path(_store.path).with_name("native_presentations.sqlite3"))
            await _presentations.recover()
        if _task is None or _task.done():
            _closing = False
            _task = asyncio.create_task(_worker(), name="unibot-native-jobs")
        if _notifier_task is None or _notifier_task.done():
            _notifier_task = asyncio.create_task(_notifications(), name="unibot-native-notifications")
    return _store


async def stop_workers():
    global _task, _notifier_task
    global _closing
    _closing = True
    _wake.set()
    inline = [task for task in _inline_tasks if task is not asyncio.current_task() and not task.done()]
    for task in inline:
        task.cancel()
    await asyncio.gather(*inline, return_exceptions=True)
    if _task is not None:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
    if _notifier_task is not None:
        _notifier_task.cancel()
        await asyncio.gather(_notifier_task, return_exceptions=True)
        _notifier_task = None
    if _store is not None:
        await _store.mark_interrupted()
    _contexts.clear()


async def _fetch_with_cancel(awaitable, job, store):
    work = asyncio.create_task(awaitable)
    try:
        while not work.done():
            await asyncio.wait({work}, timeout=1)
            latest = await store.get(job.job_id)
            if latest and latest.status == "cancel_requested":
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                return None
        return await work
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)


async def _handle_unchecked(job):
    context = _contexts.get(job.job_id)
    if context is None:
        return outcome("JOB_INTERRUPTED", "原始请求上下文已丢失，任务未继续执行。", status="interrupted")
    bot, event = context
    if not valid_request(bot, event) or _scope(bot, event) != {key: getattr(job, key) for key in _scope(bot, event)}:
        return outcome("PERMISSION_DENIED", "任务归属核验失败，本次未执行。", status="denied")
    if job.capability_id == "job.replay":
        return await execute_replay(bot, event, job)
    if job.capability_id in {"maintenance.refresh", "maintenance.import_chunirec"}:
        from .maintenance import run
        return await run(bot, event, job, _store)
    if job.capability_id in {"story.search", "story.random"}:
        from .story import execute
        return await execute(bot, event, job, _store, _presentations)
    from .media_jobs import CAPABILITIES
    if job.capability_id in CAPABILITIES:
        from .media_jobs import execute
        return await execute(bot, event, job, _store, _presentations)
    return outcome("CAPABILITY_RETIRED", "该历史业务已退役，请使用 OAuth 直接查询。", status="unavailable")


async def _notify(job):
    if not await private_job_allowed(job):
        return {"silent": True}
    if job.context_type == "group":
        from types import SimpleNamespace
        capability = job.capability_id
        if capability == "job.replay":
            source = await _store.get(job.payload.get("source_job_id", ""))
            capability = source.capability_id if source is not None else ""
        if not group_policy.enabled(SimpleNamespace(group_id=int(job.context_id)), capability):
            return {"silent": True}
    from .results import redundant_media_notice
    from .. import llm_api
    if job.response_owner == "agent":
        if llm_api._job_notifier is None:
            return {"deferred": True}
        # LLM notices may wait in another plugin's queue. Do not hand a future
        # notification an irreversible copy of personal data to render later.
        from .player_data import PLAYER_CAPABILITIES
        from dataclasses import replace
        if job.capability_id in PLAYER_CAPABILITIES:
            public = outcome(job.result.get("code", "OK"), "玩家查询任务已结束；原查询结果通过 UniBot 发送，本通知不包含个人资料。", status=job.result.get("status", "ok"),
                             data={key: job.result.get("data", {}).get(key) for key in ("artifact_delivery", "media_cached")})
            job = replace(job, result=public)
        return await llm_api.notify_job(job)
    if redundant_media_notice(job):
        return {"silent": True}
    from nonebot import get_bot
    try:
        bot = get_bot(job.bot_id)
    except KeyError:
        return {"deferred": True}
    label = "剧情" if job.capability_id.startswith("story.") else "资源维护" if job.capability_id.startswith("maintenance.") else "查询图片"
    text = job.result.get("public_hint") or f"{label}未能完成，请稍后重试或查询任务详情。"
    if job.context_type == "group":
        if not group_policy.enabled(type("Scope", (), {"group_id": int(job.context_id)})(), job.capability_id): return {"silent": True}
        await result_call({"status": job.status}, bot.send_group_msg, group_id=int(job.context_id), message=text)
    else:
        await bot.send_private_msg(user_id=int(job.context_id), message=text)


async def _worker():
    while not _closing:
        async with _gate:
            _wake.clear()
            job = await _store.claim_next(PROVIDER)
        if job:
            try:
                await run_one(_store, _handle, job=job)
            finally:
                _contexts.pop(job.job_id, None)
        if _closing:
            return
        _notify_wake.set()
        if job is None:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass


async def _notifications():
    while not _closing:
        _notify_wake.clear()
        for finished in await _store.pending_notifications(PROVIDER, include_unknown_agent=True):
            if finished.notification_state == "unknown":
                from .. import llm_api
                verify = getattr(llm_api._job_notifier, "can_recover_job", None)
                if not callable(verify) or not await verify(finished):
                    continue
                await _store.update(finished.job_id, notification_state="pending")
            await notify_once(_store, finished.job_id, _notify)
        try:
            await asyncio.wait_for(_notify_wake.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


async def submit_operation(bot, event, capability_id, operation_id, payload, *, origin="agent"):
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    if not isinstance(event, (PrivateMessageEvent, GroupMessageEvent)) or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "任务身份不匹配。", status="denied")
    from .player_data import RETIRED, retired
    if capability_id in RETIRED:
        return retired()
    if capability_id != "job.replay" and not group_policy.enabled(event, capability_id):
        return outcome("FEATURE_DISABLED", "本群对应功能已关闭。", status="denied")
    store = await ensure_started()
    if _closing:
        return outcome("PLUGIN_CLOSING", "插件关闭中，本次未接单。", status="unavailable")
    try:
        async with _gate:
            job, created = await store.submit(provider=PROVIDER, capability_id=capability_id,
                operation_id=operation_id, source_message_id=str(event.message_id),
                payload=payload, response_owner=origin, **_scope(bot, event))
            if _closing:
                await store.cancel(job.job_id, **_scope(bot, event))
                return outcome("PLUGIN_CLOSING", "插件关闭中，本次未继续执行。", status="unavailable")
            if created:
                _contexts[job.job_id] = (bot, event)
                from .media_jobs import CAPABILITIES
                image_query = capability_id in CAPABILITIES - {"player.song_scores", "song.search", "song.detail"} or capability_id in {"story.search", "story.random"}
                if origin == "command" and image_query and group_policy.enabled(event, capability_id) and await private_job_allowed(job):
                    # The worker takes this gate too: acknowledge before it can
                    # render/send, and only once for a newly accepted request.
                    try:
                        await asyncio.wait_for(bot.send(event, "正在生成，请稍候"), timeout=10)
                    except Exception:
                        pass  # Unknown receipt delivery is never retried.
    except QueueFull:
        return outcome("QUEUE_FULL", "任务队列已满，本次没有接单。", status="unavailable")
    _wake.set()
    job = await store.get(job.job_id)
    return await submission_result(job, created)


async def existing_source(bot, event, capability_id, operation_id):
    if not valid_request(bot, event):
        return outcome("PERMISSION_DENIED", "任务身份不匹配。", status="denied")
    store = await ensure_started()
    job = await store.find_submission(provider=PROVIDER, capability_id=capability_id, operation_id=operation_id,
        source_message_id=str(event.message_id), **_scope(bot, event))
    return await submission_result(job, False) if job is not None else None


async def submission_result(job, created):
    data = await public_job(job)
    state = {"queued": "running", "running": "running", "cancel_requested": "running", "succeeded": "ok",
             "cancelled": "ok", "partial": "partial", "unknown": "unknown"}.get(job.status, "unavailable")
    return outcome("JOB_ACCEPTED" if created else "JOB_EXISTS", "已接收任务，请以当前任务状态为准。",
                   status=state, data=data)


async def private_job_allowed(job, depth=0):
    from .player_data import RETIRED, job_authorized
    if job is None or depth > 8 or job.capability_id in RETIRED:
        return False
    if job.capability_id == "job.replay":
        source = await _store.get(job.payload.get("source_job_id", ""))
        if source is None or any(getattr(source, key) != getattr(job, key) for key in ("bot_id", "actor_id", "context_type", "context_id")):
            return False
        return await private_job_allowed(source, depth + 1)
    return await job_authorized(job)


async def public_jobs(jobs):
    """Gather metadata first, then release one owner's results at a final gate."""
    from .player_data import PLAYER_CAPABILITIES, RETIRED, get_reader, player_policy_allowed
    from .oauth import OAuthError
    resolved, rows = [], []
    for job in jobs:
        source, depth = job, 0
        while source and source.capability_id == "job.replay" and depth < 8:
            source = await _store.get(source.payload.get("source_job_id", ""))
            depth += 1
        if source and source.capability_id == "job.replay":
            source = None
        if source and any(getattr(source, key) != getattr(job, key) for key in ("bot_id", "actor_id", "context_type", "context_id")):
            source = None
        resolved.append(source)
        rows.append(dict(job_id=job.job_id, capability_id=job.capability_id, provider=job.provider,
            actor_id=job.actor_id, source_message_id=job.source_message_id,
            source_capability=job.payload.get("source_capability", job.capability_id),
            source_job_id=job.payload.get("source_job_id"), status=job.status, current_step=job.current_step,
            queue_position=await _store.queue_position(job.job_id), queue_position_scope="queued_only",
            updated_at=job.updated_at, side_effect_state=job.side_effect_state,
            delivery=job.delivery_state, notification=job.notification_state,
            public_error_code=job.public_error_code))
    # query/list and submission receipts always belong to a single real owner.
    # No await is permitted after the final credential read and policy checks.
    owners = {(j.bot_id, j.actor_id) for j in jobs}
    binding = None
    if len(owners) == 1 and any(j and j.capability_id in PLAYER_CAPABILITIES for j in resolved):
        try:
            binding = await get_reader().oauth.current_binding(*next(iter(owners)))
        except OAuthError:
            pass
    for job, source, row in zip(jobs, resolved, rows):
        private = source and source.capability_id in PLAYER_CAPABILITIES
        allowed = source is not None and source.capability_id not in RETIRED
        if private:
            allowed = bool(binding and source.payload.get("oauth_binding") == binding and player_policy_allowed(source))
        row["result"] = job.result if allowed else outcome("AUTHORIZATION_CHANGED", "授权已变化或历史能力已退役，隐藏旧玩家结果；本人任务管理仍可用。", status="denied")
    return rows


async def public_job(job):
    return (await public_jobs([job]))[0]


async def query(bot, event, action="list", job_id=None):
    if not valid_request(bot, event):
        return outcome("PERMISSION_DENIED", "任务身份不匹配。", status="denied")
    store = await ensure_started()
    scope = _scope(bot, event)
    if action == "list":
        jobs = await store.list(**scope, limit=5)
        return outcome(data={"jobs": await public_jobs(jobs)})
    job = await store.get(job_id or "")
    if job is None or any(str(getattr(job, key)) != value for key, value in scope.items()):
        return outcome("JOB_NOT_FOUND", "当前会话中没有你可访问的这个任务。", status="needs_input")
    if action == "cancel":
        job = await store.cancel(job.job_id, **scope)
        if job.status == "cancelled":
            _contexts.pop(job.job_id, None)
        _wake.set()
        _notify_wake.set()
    return outcome(data=await public_job(job))


async def replay(bot, event, job_id, *, origin=None, before_send=None):
    checked = await query(bot, event, "get", job_id)
    if checked["status"] != "ok":
        return checked
    if checked["data"]["status"] in {"queued", "running", "cancel_requested"}:
        return outcome("JOB_RUNNING", "原任务尚未结束，请先查询进度。", status="running", data=checked["data"])
    target = await _store.get(job_id)
    if not await private_job_allowed(target):
        return outcome("AUTHORIZATION_CHANGED", "原玩家授权已变化，不能补发旧结果。", status="denied")
    if target.capability_id == "job.replay":
        return await replay(bot, event, target.payload["source_job_id"], origin=origin, before_send=before_send)
    if not group_policy.enabled(event, target.capability_id):
        return outcome("FEATURE_DISABLED", "本群对应功能已关闭，不能补发。", status="denied")
    from .media_jobs import CAPABILITIES
    if (await _store.get(job_id)).capability_id not in {"story.search", "story.random", "media.inline"} | CAPABILITIES:
        return outcome("NO_CACHED_MEDIA", "此任务没有可补投的媒体计划。", status="needs_input")
    from .presentation_store import scope
    if not await _presentations.exists(job_id, scope(bot, event)):
        return outcome("NO_CACHED_MEDIA", "此任务尚未保存完整媒体计划，无法补投。", status="needs_input")
    if origin is not None:
        return await submit_operation(bot, event, "job.replay", f"replay:{event.message_id}:{job_id}",
                                      {"source_job_id": job_id}, origin=origin)
    original_before_send = before_send
    async def checked_send():
        if not await private_job_allowed(target):
            return False
        return await original_before_send() if original_before_send else True
    result = await _presentations.replay(job_id, bot, event, limit=200 if before_send else 20, before_send=checked_send)
    if not result["remaining"]:
        old = await _store.get(job_id)
        saved = dict(old.result)
        saved["data"] = dict(saved.get("data", {}), artifact_delivery="sent", media_cached=True)
        saved["public_hint"] = "缓存媒体已全部送达；原任务执行状态保留，没有重新生成。"
        await _store.update(job_id, result=saved)
    return outcome("MEDIA_DELIVERY_INCOMPLETE" if result["remaining"] else "OK",
                   hint=("这条补发请求已处理，未再次发送。" if result.get("deduplicated") else f"已补投 {result['sent']} 条缓存内容。") +
                   ("仍有未确认送达的内容，可稍后再次明确请求补投。" if result["remaining"] else ""),
                   status="partial" if result["remaining"] and result["sent"] else "unknown" if result["remaining"] else "ok", data=result)


async def execute_replay(bot, event, job):
    count = 0
    async def admit():
        nonlocal count
        count += 1
        source = await _store.get(job.payload["source_job_id"])
        if not await private_job_allowed(source):
            return False
        admitted = await _store.start_effect(job.job_id, f"replaying_media:{count}")
        return admitted and await private_job_allowed(source)
    output = await replay(bot, event, job.payload["source_job_id"], before_send=admit)
    latest = await _store.get(job.job_id)
    if latest.status == "cancel_requested" and output.get("data", {}).get("remaining", True):
        uncertain = output.get("data", {}).get("uncertain", 0)
        output.update(code="MEDIA_DELIVERY_UNKNOWN" if uncertain else "JOB_CANCELLED",
                      public_hint="已停止后续补发；已开始的投递结果需要核对。" if uncertain else "已停止后续缓存补发，已送达的内容不会撤回。",
                      status="partial" if output.get("data", {}).get("sent") else "unknown" if uncertain else "cancelled")
        output.setdefault("data", {})["cancel_requested"] = True
    output.setdefault("data", {})["source_job_id"] = job.payload["source_job_id"]
    output["side_effect_state"] = "unknown" if output["status"] == "unknown" or output["data"].get("uncertain") else "committed" if output["data"].get("sent") else "none"
    return output


async def _handle(job):
    context = _contexts.get(job.job_id)
    if context is None: return outcome("JOB_INTERRUPTED", "原请求上下文已丢失。", status="interrupted")
    bot, event = context
    capability = job.capability_id
    if capability == "job.replay":
        source = await _store.get(job.payload["source_job_id"])
        capability = source.capability_id if source is not None else ""
    if not group_policy.enabled(event, capability):
        return outcome("FEATURE_DISABLED", "本群对应功能已关闭，任务未继续执行。", status="cancelled")
    with group_policy.scope(event, capability):
        return await _handle_unchecked(job)
