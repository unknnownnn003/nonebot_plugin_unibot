"""Controlled attachment import, bounded on every source and shared by /chuupdate."""
from __future__ import annotations
import asyncio
import base64
from pathlib import Path
from uuid import uuid4
import aiofiles
import httpx
from .results import outcome
from .storage import commit_scores, PartialWriteError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _segment_data(segment):
    if isinstance(segment, dict):
        return segment.get("type"), segment.get("data", {}) or {}
    return getattr(segment, "type", None), getattr(segment, "data", {}) or {}


async def import_file(bot, event, user_id: str):
    from .. import get_player_score as s
    attachment = next((data for kind, data in map(_segment_data, event.get_message()) if kind == "file"), None)
    if not attachment:
        return outcome("NEED_ATTACHMENT", "请在当前请求中附加成绩文件。", status="needs_input")
    source_info = dict(attachment)
    if not source_info.get("url") and source_info.get("file_id"):
        try:
            info = await bot.call_api("get_file", file_id=source_info["file_id"])
            info = info.get("data", info) if isinstance(info, dict) else {}
            source_info.update(info)
        except Exception:
            return outcome("NEED_ATTACHMENT", "无法读取当前文件附件。", status="needs_input")
    root = Path(s.SCORE_DIR).resolve()
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    temp = root / f"{user_id}_{uuid4().hex}.upload"
    async def copy_stream(chunks):
        size = 0
        async with aiofiles.open(temp, "wb") as output:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("UPLOAD_TOO_LARGE")
                await output.write(chunk)
    try:
        encoded = source_info.get("base64")
        local = str(source_info.get("file") or "")
        url = str(source_info.get("url") or "")
        if encoded:
            if len(str(encoded)) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
                raise ValueError("UPLOAD_TOO_LARGE")
            raw = await asyncio.to_thread(base64.b64decode, str(encoded), validate=True)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError("UPLOAD_TOO_LARGE")
            async with aiofiles.open(temp, "wb") as output:
                await output.write(raw)
        elif local and await asyncio.to_thread(Path(local).is_file):
            async def local_chunks():
                async with aiofiles.open(local, "rb") as source:
                    while chunk := await source.read(65536):
                        yield chunk
            await copy_stream(local_chunks())
        else:
            if url.startswith("//"):
                url = "http:" + url
            if not url.startswith(("http://", "https://")):
                return outcome("NEED_ATTACHMENT", "附件没有可读取的文件内容。", status="needs_input")
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, timeout=30) as response:
                    response.raise_for_status()
                    await copy_stream(response.aiter_bytes(65536))
        ok, hint, rows, profile = await asyncio.to_thread(s._parse_file, str(temp))
        if not ok:
            return outcome("INVALID_DATA", "解析失败，请使用落雪CSV或Rin JSON文件。", status="needs_input")
        total = await asyncio.to_thread(commit_scores, str(user_id), rows, profile)
        result = outcome(data={"user_id": str(user_id), "items": len(rows), "total": total},
                         hint=f"保存成功\n{hint}\n当前总谱面数: {total}")
        result.update(effect="local_write", side_effect_state="committed")
        return result
    except PartialWriteError:
        result = outcome("PARTIAL_WRITE", "成绩已保存，但玩家资料保存失败，请管理员核对。", status="partial")
        result.update(effect="local_write", side_effect_state="committed")
        return result
    except ValueError:
        return outcome("INVALID_ARGUMENT", "文件不合法或超过10MB限制。", status="needs_input")
    except (OSError, httpx.HTTPError):
        return outcome("UPSTREAM_UNAVAILABLE", "读取或保存成绩文件失败。", status="unavailable")
    finally:
        try:
            await asyncio.to_thread(temp.unlink, missing_ok=True)
        except OSError:
            pass
