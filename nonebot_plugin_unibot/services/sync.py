"""Provider-owned persistent FIFO and one worker for all LXNS sync entry points."""
from __future__ import annotations
import asyncio
from pathlib import Path
from .results import outcome
from .job_store import JobStore, QueueFull, run_one, notify_once
from .storage import commit_scores, PartialWriteError

PROVIDER = "nonebot_plugin_unibot"
_store = None
_task = None
_notifier_task = None
_notify_wake = asyncio.Event()
_contexts = {}
_wake = asyncio.Event()
_gate = asyncio.Lock()
_lifecycle = asyncio.Lock()
_closing = False


def _scope(bot, event):
    group = getattr(event, "group_id", None)
    return dict(bot_id=str(bot.self_id), context_type="group" if group is not None else "private",
                context_id=str(group if group is not None else event.user_id), actor_id=str(event.user_id))


async def ensure_started():
    global _store, _task, _notifier_task, _closing
    async with _lifecycle:
        if _store is None:
            from .. import get_player_score as s
            _store = JobStore(Path(s.SCORE_DIR).parent / "native_jobs.sqlite3")
            await _store.init()
            # No reconstructed Event can manufacture old authorization. Jobs
            # whose real request context was lost are visible as interrupted.
            await _store.mark_interrupted()
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


async def _run_sync(user_id: str, job, store):
    from .. import get_player_score as s
    import httpx
    token = (s.config.lxns_token or "").strip()
    if not token:
        return outcome("NOT_CONFIGURED", "未配置落雪开发者密钥，无法同步。", status="unavailable")
    try:
        await store.update(job.job_id, current_step="resolving_player")
        async with httpx.AsyncClient() as client:
            resolved = await _fetch_with_cancel(s._resolve_friend_code(client, user_id, token), job, store)
            if resolved is None:
                return outcome("JOB_CANCELLED", "任务已取消，尚未写入成绩。", status="cancelled")
            friend_code, player, _ = resolved
            if not friend_code:
                return outcome("NOT_BOUND", "未找到落雪好友码，请先使用 /bind 绑定。", status="needs_input")
            await store.update(job.job_id, current_step="fetching_scores")
            fetched = await _fetch_with_cancel(s._fetch_lxns_all_scores(client, friend_code, token), job, store)
            if fetched is None:
                return outcome("JOB_CANCELLED", "任务已取消，尚未写入成绩。", status="cancelled")
            rows, status, song_count, failed_required, unavailable_required, failed_optional = fetched
        if status != 200:
            return outcome("UPSTREAM_UNAVAILABLE", "落雪成绩接口请求失败，本次没有写入。", status="unavailable")
        parsed, missing = await asyncio.to_thread(s._normalize_lxns_score_items, rows)
        if not parsed:
            return outcome("INVALID_DATA" if missing else "NO_SCORES", "落雪没有返回可写入的成绩。", status="needs_input")
        if failed_required:
            return outcome("PARTIAL_UPSTREAM", "MASTER/ULTIMA请求失败，本次已取消写入。", status="unavailable")
        latest = await store.get(job.job_id)
        if latest.status == "cancel_requested":
            return outcome("JOB_CANCELLED", "任务已取消，尚未写入成绩。", status="cancelled")
        await store.update(job.job_id, current_step="committing_scores", side_effect_state="preparing")
        profile = s._normalize_lxns_player_info(player) if player else None
        commit = asyncio.create_task(asyncio.to_thread(commit_scores, user_id, parsed, profile))
        try:
            total = await asyncio.shield(commit)
        except asyncio.CancelledError:
            # Thread commits cannot be cancelled. Finish observing the commit
            # before releasing ownership; never report a completed write cancelled.
            total = await commit
        result = outcome(data={"user_id": user_id, "song_count": song_count, "fetched": len(parsed),
                               "total": total, "unavailable_required": unavailable_required, "failed_optional": failed_optional})
        result.update(effect="local_write", side_effect_state="committed")
        result["public_hint"] = f"落雪同步成功：获取{len(parsed)}个谱面，当前共{total}个谱面。"
        return result
    except PartialWriteError:
        result = outcome("PARTIAL_WRITE", "成绩已保存，但玩家资料保存失败，请管理员核对。", status="partial")
        result.update(effect="local_write", side_effect_state="committed")
        return result
    except httpx.HTTPError:
        return outcome("UPSTREAM_UNAVAILABLE", "落雪接口暂时不可用，本次没有写入。", status="unavailable")


async def _handle(job):
    context = _contexts.get(job.job_id)
    if context is None:
        return outcome("JOB_INTERRUPTED", "原始请求上下文已丢失，任务未继续执行。", status="interrupted")
    bot, event = context
    if _scope(bot, event) != {key: getattr(job, key) for key in _scope(bot, event)} or job.payload.get("user_id") != job.actor_id:
        return outcome("PERMISSION_DENIED", "任务归属核验失败，本次未执行。", status="denied")
    return await _run_sync(job.actor_id, job, _store)


async def _notify(job):
    from .. import llm_api
    if job.response_owner == "agent" and llm_api._job_notifier is not None:
        return await llm_api.notify_job(job)
    from nonebot import get_bot
    try:
        bot = get_bot(job.bot_id)
    except KeyError:
        return {"deferred": True}
    text = f"成绩同步任务 {job.job_id}：{job.result.get('public_hint') or job.status}"
    if job.context_type == "group":
        await bot.send_group_msg(group_id=int(job.context_id), message=text)
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
        for finished in await _store.pending_notifications(PROVIDER):
            await notify_once(_store, finished.job_id, _notify)
        try:
            await asyncio.wait_for(_notify_wake.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


async def submit(bot, event, user_id, *, origin="agent"):
    if str(user_id) != str(event.user_id) or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "任务必须属于真实发起人。", status="denied")
    store = await ensure_started()
    try:
        async with _gate:
            job, created = await store.submit(provider=PROVIDER, capability_id="score.sync",
                operation_id=f"lxns:{user_id}", source_message_id=str(event.message_id),
                payload={"user_id": str(user_id)}, response_owner=origin, **_scope(bot, event))
            if created:
                _contexts[job.job_id] = (bot, event)
    except QueueFull:
        return outcome("QUEUE_FULL", "同步队列已满，本次没有接单。", status="unavailable")
    _wake.set()
    job = await store.get(job.job_id)
    data = await public_job(job)
    state = {"queued": "running", "running": "running", "cancel_requested": "running", "succeeded": "ok",
             "cancelled": "ok", "partial": "partial", "unknown": "unknown"}.get(job.status, "unavailable")
    return outcome("JOB_ACCEPTED" if created else "JOB_EXISTS", "已接收任务，请以当前任务状态为准。",
                   status=state, data=data)


async def public_job(job):
    return dict(job_id=job.job_id, status=job.status, current_step=job.current_step,
                queue_position=await _store.queue_position(job.job_id), queue_position_scope="queued_only",
                updated_at=job.updated_at, side_effect_state=job.side_effect_state,
                delivery=job.delivery_state, notification=job.notification_state,
                public_error_code=job.public_error_code, result=job.result)


async def query(bot, event, action="list", job_id=None):
    store = await ensure_started()
    scope = _scope(bot, event)
    if action == "list":
        jobs = await store.list(**scope, limit=5)
        return outcome(data={"jobs": [await public_job(job) for job in jobs]})
    job = await store.get(job_id or "")
    if job is None or any(str(getattr(job, key)) != value for key, value in scope.items()):
        return outcome("JOB_NOT_FOUND", "当前会话中没有你可访问的这个任务。", status="needs_input")
    if action == "cancel":
        job = await store.cancel(job.job_id, **scope)
        _wake.set()
        _notify_wake.set()
    return outcome(data=await public_job(job))
