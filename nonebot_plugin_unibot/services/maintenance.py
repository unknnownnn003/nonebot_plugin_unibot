"""One typed maintenance entry point on the provider's existing persistent FIFO."""
import asyncio
import hashlib
import json

from .results import outcome
from . import group_policy
from .permissions import native_superuser

RESOURCES = {"songlist": "落雪曲库", "jackets": "曲绘",
             "collections": "收藏品", "stories": "剧情"}


class Stopped(Exception):
    def __init__(self, code, hint):
        self.code, self.hint = code, hint


async def observed_io(function, *args):
    work = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        try:
            work.result()
        except BaseException:
            pass
        raise asyncio.CancelledError()
    return work.result()


class RunContext:
    def __init__(self, bot, event, job, store):
        self.bot, self.event, self.job, self.store = bot, event, job, store
        self.writes = 0
        self.possible_effect = False
        self._write_lock = asyncio.Lock()

    def permission(self):
        if not native_superuser(self.bot, self.event):
            raise Stopped("PERMISSION_DENIED", "维护权限已失效，本次停止执行。")

        if not group_policy.enabled(self.event, "maintenance.refresh"):
            raise Stopped("FEATURE_DISABLED", "本群资源维护已关闭，本次停止执行。")

    async def check(self, step=None):
        self.permission()
        latest = await self.store.get(self.job.job_id)
        if latest is None:
            raise Stopped("JOB_INTERRUPTED", "任务记录已丢失，本次停止维护。")
        if latest.status == "cancel_requested":
            raise Stopped("JOB_CANCELLED", "已停止维护任务。")
        if step:
            await self.store.update(self.job.job_id, current_step=step)

    async def network(self, awaitable, step):
        try:
            await self.check(step)
        except BaseException:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise
        work = asyncio.ensure_future(awaitable)
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=1)
                await self.check()
            return await work
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            elif not work.cancelled():
                work.exception()

    async def write(self, step, function, *args):
        async with self._write_lock:
            await self.check(step)
            await self.store.update(self.job.job_id, side_effect_state="preparing")
            def commit():
                self.permission()
                self.possible_effect = True
                return function(*args)
            result = await observed_io(commit)
            self.writes += 1
            self.possible_effect = False
            await self.store.update(self.job.job_id, side_effect_state="committed")
            return result


async def run(bot, event, job, store):
    context = RunContext(bot, event, job, store)
    args = job.payload.get("arguments", {})
    try:
        await context.check("checking_maintenance_permission")
        resource = args.get("resource")
        from . import resource_tasks
        if resource == "songlist":
            result = await resource_tasks.refresh_songlist(context)
        elif resource in {"chunirec", "chunirec_file"}:
            return retired_source()
        elif resource == "jackets":
            result = await resource_tasks.refresh_jackets(context, args.get("full_refresh", False))
        elif resource == "collections":
            result = await resource_tasks.refresh_collections(context)
        elif resource == "stories":
            result = await resource_tasks.refresh_stories(context)
        else:
            return outcome("INVALID_ARGUMENT", "未知维护资源。", status="needs_input")
    except Stopped as exc:
        status = "partial" if context.writes else "cancelled" if exc.code == "JOB_CANCELLED" else "interrupted" if exc.code == "JOB_INTERRUPTED" else "denied"
        result = outcome(exc.code, exc.hint + ("已完成的资源写入保留，请核对任务结果。" if context.writes else ""), status=status)
    except Exception as exc:
        # Provider diagnostics retain the class, never token-bearing URLs/body.
        from nonebot.log import logger
        logger.warning(f"维护任务失败 job={job.job_id} error={type(exc).__name__}")
        unknown = context.possible_effect
        result = outcome("RESULT_UNKNOWN" if unknown else "PARTIAL_MAINTENANCE" if context.writes else "MAINTENANCE_FAILED",
            "维护写入结果需要核对。" if unknown else "维护未全部完成，已完成的写入保留。" if context.writes else "维护失败，请稍后重试或查看本地日志。",
            status="unknown" if unknown else "partial" if context.writes else "unavailable")
    result.setdefault("data", {})["completed_writes"] = context.writes
    if context.writes or context.possible_effect:
        result.update(effect="local_write", side_effect_state="preparing" if context.possible_effect else "committed")
    return result


async def submit(bot, event, resource, full_refresh=False, *, origin="agent"):
    from .sync import submit_operation
    if not native_superuser(bot, event):
        return outcome("PERMISSION_DENIED", "资源维护仅SUPERUSER可用。", status="denied")
    if resource in {"chunirec", "chunirec_file"}:
        return retired_source()
    if resource not in RESOURCES or (full_refresh and resource != "jackets"):
        return outcome("INVALID_ARGUMENT", "请指定已支持的资源；强制刷新仅适用于曲绘。", status="needs_input")
    args = dict(resource=resource, full_refresh=full_refresh)
    digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    return await submit_operation(bot, event, "maintenance.refresh", f"maintenance:{digest}",
                                  {"arguments": args}, origin=origin)


def retired_source():
    return outcome("SOURCE_RETIRED", "日服曲库维护已退役，请使用 /更新songlist 更新落雪国服曲库；历史文件已保留。", status="unavailable")
