"""Human access to own queued jobs, including when the business feature is off."""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg

from .services.sync import query

jobs = on_command("中二任务", priority=1, block=True)
cancel = on_command("取消中二任务", priority=1, block=True)
replay = on_command("补发中二任务", priority=1, block=True)

_STATUS = {"queued": "排队中", "running": "执行中", "cancel_requested": "已请求取消",
           "cancelled": "已取消", "succeeded": "已完成", "failed": "失败", "partial": "部分完成",
           "unknown": "结果待核对", "interrupted": "已中断"}


def format_result(result):
    if result["status"] != "ok":
        return result.get("public_hint") or "无法查询任务。"
    data = result["data"]
    rows = data.get("jobs", [data] if data.get("job_id") else [])
    if not rows:
        return "当前会话没有你的中二任务。"
    lines = []
    for row in rows:
        text = f"{row['job_id']}：{_STATUS.get(row['status'], row['status'])}"
        if row.get("queue_position") is not None:
            text += f"（排队序号 {row['queue_position']}，不含执行中任务）"
        hint = row.get("result", {}).get("public_hint")
        if hint:
            text += f"\n{hint}"
        lines.append(text)
    return "\n".join(lines)


@jobs.handle()
async def show_jobs(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    identity = args.extract_plain_text().strip()
    if identity and (len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity)):
        await jobs.finish("用法：/中二任务 [任务ID]")
    result = await query(bot, event, "get" if identity else "list", identity or None)
    await jobs.send(format_result(result))
    event.__dict__.setdefault("_native_capability_results", {})["job.get" if identity else "job.list"] = dict(
        status=result["status"], code=result["code"], delivery="sent")


@cancel.handle()
async def cancel_job(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    identity = args.extract_plain_text().strip()
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        await cancel.finish("用法：/取消中二任务 <任务ID>。执行中的任务只能请求取消。")
    result = await query(bot, event, "cancel", identity)
    await cancel.send(format_result(result))
    event.__dict__.setdefault("_native_capability_results", {})["job.cancel"] = dict(
        status=result["status"], code=result["code"], delivery="sent")


@replay.handle()
async def replay_job(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    from .services.sync import replay as replay_cached
    identity = args.extract_plain_text().strip()
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        await replay.finish("用法：/补发中二任务 <任务ID>。仅补发缓存媒体，不重新生成。")
    result = await replay_cached(bot, event, identity)
    await replay.send(result["public_hint"])
    event.__dict__.setdefault("_native_capability_results", {})["job.replay"] = dict(
        status=result["status"], code=result["code"], delivery="sent")

