"""Read-only story queries shared by the original commands and Agent API."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .results import outcome


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


async def _present(bot, event, name: str, file_path: str):
    """Render and deliver one story using the original image/forward semantics."""
    from .. import story

    path = _safe_local_path(story, file_path)
    if path is None or not path.is_file():
        return {"delivery": "not_sent", "code": "STORY_NOT_CACHED"}
    try:
        markdown = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except Exception:
        return {"delivery": "unknown", "code": "STORY_READ_FAILED"}

    if not story.HTMLRENDER_AVAILABLE:
        try:
            await bot.send(event, "未安装 HtmlRender 插件，暂时无法渲染剧情图片。请联系 Bot 管理员安装 Playwright 及 HTMLRender 插件。")
            return {"delivery": "sent"}
        except Exception:
            return {"delivery": "unknown"}

    try:
        markdown = await story.predownload_images(markdown)
        chunks = story.split_markdown(markdown, max_lines=400)
        from nonebot.adapters.onebot.v11 import Message, MessageSegment
        nodes = [MessageSegment.node_custom(user_id=int(bot.self_id), nickname="uni",
                                             content=Message(f"当前角色：{name}"))]
        for index, chunk in enumerate(chunks):
            header = f"\n# {name}\n\n" if index == 0 else ""
            base_style = """<style>
.markdown-body { background-color: #fdf6e3 !important; color: #333333 !important; }
.markdown-body table tr { background-color: #fdf6e3 !important; }
.markdown-body table tr:nth-child(2n) { background-color: #f2e9ce !important; }
</style>"""
            image = await story.md_to_pic(base_style + header + chunk,
                                          width=800, type="jpeg", quality=90,
                                          device_scale_factor=1.5)
            for piece in story.split_image(image, max_height=6000):
                nodes.append(MessageSegment.node_custom(
                    user_id=int(bot.self_id), nickname="uni",
                    content=Message(MessageSegment.image(piece))))

        if getattr(event, "message_type", None) == "group":
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
        return {"delivery": "sent"}
    except Exception:
        # The original command falls back to at most two direct images. Preserve
        # that observable behavior while keeping delivery uncertainty explicit.
        try:
            sent = 0
            for node in nodes[1:]:
                if sent >= 2:
                    break
                content = node["data"]["content"]
                await bot.send(event, content)
                sent += 1
            return {"delivery": "partial" if sent else "unknown"}
        except Exception:
            return {"delivery": "unknown"}


async def search(bot, event, query: str):
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

    async def present(current_bot, current_event):
        return await _present(current_bot, current_event, name, path)

    return outcome(data={"name": name, "scope": "local_story_index"},
                   artifacts=[dict(kind="presenter", present=present)])


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


async def random_story(bot, event):
    from .. import story

    index = await asyncio.to_thread(story.load_index)
    mapping = index.get("mapping", {}) if isinstance(index, dict) else {}
    if not mapping:
        return outcome("STORY_INDEX_EMPTY", "尚未建立剧情索引，请先由管理员更新剧情。", status="unavailable")
    import random
    name = random.choice(list(mapping))
    path = mapping[name]
    if _safe_local_path(story, str(path)) is None:
        return outcome("INVALID_DATA", "剧情索引包含越权路径，已拒绝读取。", status="unavailable")
    async def present(current_bot, current_event):
        return await _present(current_bot, current_event, str(name), str(path))
    return outcome(data={"name": str(name), "scope": "local_story_index"},
                   artifacts=[dict(kind="presenter", present=present)])
