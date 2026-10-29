"""Only incoming OneBot file references can select upload sources."""
import asyncio
import base64
from pathlib import Path

import aiofiles
import httpx
from .maintenance import observed_io
from . import http_safety


class AttachmentError(ValueError):
    def __init__(self, code, hint):
        self.code, self.hint = code, hint


def file_reference(event):
    for segment in event.get_message():
        kind = segment.get("type") if isinstance(segment, dict) else getattr(segment, "type", None)
        data = segment.get("data", {}) if isinstance(segment, dict) else getattr(segment, "data", {})
        if kind == "file" and isinstance(data, dict):
            return dict(data)


async def read_file(bot, event, limit, *, prefer_get_file=False, check=None):
    async def checkpoint():
        if check is not None:
            await check()
    await checkpoint()
    source = file_reference(event)
    if not source:
        raise AttachmentError("NEED_ATTACHMENT", "请在当前请求中附加文件。")
    if source.get("file_id") and (prefer_get_file or not source.get("url")):
        await checkpoint()
        try:
            value = await bot.call_api("get_file", file_id=source["file_id"])
            value = value.get("data", value) if isinstance(value, dict) else {}
            if isinstance(value, dict):
                source.update({key: value for key, value in value.items() if value not in (None, "")})
        except Exception:
            if not source.get("url"):
                raise AttachmentError("NEED_ATTACHMENT", "无法读取当前文件附件。")
    await checkpoint()
    encoded = source.get("base64")
    if encoded:
        if len(str(encoded)) > ((limit + 2) // 3) * 4:
            raise AttachmentError("INVALID_ARGUMENT", "文件超过上传大小限制。")
        try:
            content = await observed_io(lambda: base64.b64decode(str(encoded), validate=True))
        except ValueError:
            raise AttachmentError("INVALID_ARGUMENT", "附件编码不合法。")
        if len(content) > limit:
            raise AttachmentError("INVALID_ARGUMENT", "文件超过上传大小限制。")
        return content
    local = str(source.get("file") or "")
    content = bytearray()
    if local and await observed_io(Path(local).is_file):
        await checkpoint()
        def read_local():
            with open(local, "rb") as file:
                while chunk := file.read(65536):
                    content.extend(chunk)
                    if len(content) > limit:
                        raise AttachmentError("INVALID_ARGUMENT", "文件超过上传大小限制。")
            return bytes(content)
        return await observed_io(read_local)
    url = str(source.get("url") or "")
    if url.startswith("//"):
        url = "http:" + url
    if not url.startswith(("http://", "https://")):
        raise AttachmentError("NEED_ATTACHMENT", "附件没有可读取的文件内容。")
    await checkpoint()
    async with http_safety.client() as client:
        async with client.stream("GET", url, timeout=30) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(65536):
                content.extend(chunk)
                if len(content) > limit:
                    raise AttachmentError("INVALID_ARGUMENT", "文件超过上传大小限制。")
    return bytes(content)
