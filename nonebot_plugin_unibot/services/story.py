"""Read-only story queries shared by the original commands and Agent API."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from .results import outcome
from .job_store import _db_thread


def _load_matches(story, query: str):
    index = story.load_index()
    if not isinstance(index, dict):
        index = {}
    mapping = index.get("mapping", {}) if isinstance(index, dict) else {}
    if not isinstance(mapping, dict):
        mapping = {}
    normalized = query.casefold().strip()
    matches = [(str(name), str(path)) for name, path in mapping.items()
               if normalized in str(name).casefold()]
    return index, matches


def _safe_local_path(story, file_path: str) -> Path | None:
    root = Path(story.DATA_DIR).resolve()
    path = (root / str(file_path).lstrip("/")).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


class StoryStopped(Exception):
    pass


def _read_markdown(story, file_path):
    path = _safe_local_path(story, file_path)
    if path is None or not path.is_file():
        raise FileNotFoundError("story not cached")
    raw = path.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


async def _render(bot, name, markdown, checkpoint):
    """Generate the original nodes once; no transport is called while rendering."""
    from .. import story
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    await checkpoint("downloading_story_images")
    markdown = await story.predownload_images(markdown)
    chunks = await _db_thread(lambda: story.split_markdown(markdown, max_lines=400))
    nodes = [MessageSegment.node_custom(user_id=int(bot.self_id), nickname="uni",
                                      content=Message(f"当前角色：{name}"))]
    for index, chunk in enumerate(chunks):
        await checkpoint(f"rendering_story:{index + 1}/{len(chunks)}")
        header = f"\n# {name}\n\n" if index == 0 else ""
        base_style = """<style>
.markdown-body { background-color: #fdf6e3 !important; color: #333333 !important; }
.markdown-body table tr { background-color: #fdf6e3 !important; }
.markdown-body table tr:nth-child(2n) { background-color: #f2e9ce !important; }
</style>"""
        image = await story.md_to_pic(base_style + header + chunk, width=800, type="jpeg", quality=90,
                                      device_scale_factor=1.5)
        pieces = await _db_thread(lambda: story.split_image(image, max_height=6000))
        for piece in pieces:
            nodes.append(MessageSegment.node_custom(user_id=int(bot.self_id), nickname="uni",
                         content=Message(MessageSegment.image(piece))))
    return nodes


async def submit_selected(bot, event, name, file_path, *, capability="story.search", origin="agent"):
    from .. import story
    from .sync import submit_operation, existing_source
    key = f"{capability}:{event.message_id}"
    if capability == "story.search":
        key += ":" + hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()
    existing = await existing_source(bot, event, capability, key)
    if existing is not None:
        return existing
    try:
        _, revision = await _db_thread(lambda: _read_markdown(story, file_path))
    except (OSError, ValueError):
        return outcome("STORY_NOT_CACHED", "剧情虽有索引，但本地文件缺失或不可读，请管理员更新剧情。", status="unavailable")
    if not getattr(story, "HTMLRENDER_AVAILABLE", False):
        return outcome("NOT_CONFIGURED", "未安装 HtmlRender，无法渲染剧情。请管理员安装 Playwright 与 HtmlRender。", status="unavailable")
    # Scope and source-message dedupe fixes selection for this actual event.
    return await submit_operation(bot, event, capability, key,
        {"name": name, "file_path": file_path, "revision": revision}, origin=origin)


async def execute(bot, event, job, store, presentations):
    from .. import story
    from .presentation_store import scope
    started, sent = False, 0
    async def checkpoint(step):
        if (await store.get(job.job_id)).status == "cancel_requested":
            raise StoryStopped()
        await store.update(job.job_id, current_step=step)
    try:
        await checkpoint("reading_story")
        markdown, revision = await _db_thread(lambda: _read_markdown(story, job.payload["file_path"]))
        if revision != job.payload["revision"]:
            return outcome("STATE_CHANGED", "排队后剧情文件已变化，请重新查询。", status="failed")
        nodes = await _render(bot, job.payload["name"], markdown, checkpoint)
        await checkpoint("saving_story_media")
        root = await presentations.stage_forward(job.job_id, scope(bot, event), nodes)
        async def admit():
            nonlocal started
            if not await store.start_effect(job.job_id, "sending_story_media"):
                return False
            started = True
            return True
        attempt = await presentations.send(root, bot, event, before_send=admit)
        if attempt.get("denied"):
            raise StoryStopped()
        delivery = attempt["delivery"]
        if delivery == "unknown" and attempt["claimed"]:
            # Preserve the original fallback limit; replay later uses only the
            # remaining cached parts, including after process restart.
            parts = await presentations.pending(job.job_id, scope(bot, event), limit=3)
            for part in parts:
                if part["kind"] != "forward_part" or part["artifact_key"] not in {"main:part:000001", "main:part:000002"}:
                    continue
                receipt = await presentations.send(part, bot, event, allow_unknown=True, before_send=admit)
                if receipt.get("denied"):
                    break
                sent += receipt["delivery"] == "sent"
            delivery = "partial" if sent else "unknown"
        output = outcome("OK" if delivery == "sent" else "MEDIA_DELIVERY_INCOMPLETE",
            "剧情已发送。" if delivery == "sent" else "剧情已生成，媒体未全部送达；可补发原任务缓存，无需重新生成。",
            status="ok" if delivery == "sent" else "partial" if delivery == "partial" else "unknown")
        output["data"] = {"name": job.payload["name"], "artifact_delivery": delivery, "media_cached": True}
        output["side_effect_state"] = "committed" if delivery == "sent" else "unknown"
        return output
    except StoryStopped:
        return outcome("JOB_CANCELLED", "已停止后续剧情处理；查询任务不会重新生成。", status="cancelled")
    except asyncio.CancelledError:
        raise
    except Exception:
        output = outcome("MEDIA_DELIVERY_UNKNOWN" if started else "RENDER_FAILED",
            "媒体投递结果需要核对，可补发已有缓存。" if started else "剧情读取或渲染失败，请稍后重试。",
            status="unknown" if started else "failed")
        output["side_effect_state"] = "unknown" if started else "none"
        return output


async def search(bot, event, query: str, *, origin="agent"):
    from .. import story

    index, matches = await asyncio.to_thread(_load_matches, story, query)
    if not index.get("mapping"):
        return outcome("STORY_INDEX_EMPTY", "尚未建立剧情索引，请先由管理员更新剧情。", status="unavailable")
    if not matches:
        return outcome("NOT_FOUND", "本地未找到匹配的剧情索引记录，请检查名称。", status="needs_input")
    exact = [item for item in matches if item[0].casefold() == query.casefold().strip()]
    if len(matches) > 1 and not exact:
        return outcome("AMBIGUOUS_TARGET", "匹配到多个结果，请提供更精确的角色名。",
                       status="needs_input",
                       data={"candidates": [{"name": n} for n, _ in matches[:8]]})
    name, path = (exact or matches)[0]
    if _safe_local_path(story, path) is None:
        return outcome("INVALID_DATA", "剧情索引包含越权路径，已拒绝读取。", status="unavailable")

    return await submit_selected(bot, event, name, path, origin=origin)


async def categories(category: str = ""):
    from .. import story

    index = await asyncio.to_thread(story.load_index)
    raw = index.get("categories", {}) if isinstance(index, dict) else {}
    if not isinstance(raw, dict) or not raw:
        return outcome("STORY_INDEX_EMPTY", "尚未建立剧情索引，请先由管理员更新剧情。", status="unavailable")
    category = category.strip()
    if not category:
        return outcome(data={"categories": [{"name": str(name), "count": len(items) if isinstance(items, list) else 0}
                                             for name, items in raw.items()]})
    found = next(((str(name), items) for name, items in raw.items() if category.casefold() in str(name).casefold()), None)
    if not found:
        return outcome("NOT_FOUND", "未找到相关剧情分类，请检查名称。", status="needs_input")
    name, items = found
    names = [str(item.get("name", "")) for item in items if isinstance(item, dict)]
    return outcome(data={"category": name, "stories": names[:100], "truncated": len(names) > 100})


async def random_story(bot, event, *, origin="agent"):
    from .. import story
    from .sync import existing_source
    existing = await existing_source(bot, event, "story.random", f"story.random:{event.message_id}")
    if existing is not None:
        return existing

    index = await asyncio.to_thread(story.load_index)
    mapping = index.get("mapping", {}) if isinstance(index, dict) else {}
    if not mapping:
        return outcome("STORY_INDEX_EMPTY", "尚未建立剧情索引，请先由管理员更新剧情。", status="unavailable")
    import random
    name = random.choice(list(mapping))
    path = mapping[name]
    if _safe_local_path(story, str(path)) is None:
        return outcome("INVALID_DATA", "剧情索引包含越权路径，已拒绝读取。", status="unavailable")
    return await submit_selected(bot, event, str(name), str(path), capability="story.random", origin=origin)
