"""Controlled attachment import for the original /chuupdate parser."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from .results import outcome


def _segment_data(segment):
    if isinstance(segment, dict):
        return segment.get("type"), segment.get("data", {}) or {}
    return getattr(segment, "type", None), getattr(segment, "data", {}) or {}


async def import_file(bot, event, user_id: str):
    from .. import get_player_score as s

    attachment = None
    for segment in event.get_message():
        kind, data = _segment_data(segment)
        if kind == "file":
            attachment = data
            break
    if not attachment:
        return outcome("NEED_ATTACHMENT", "请在当前请求中附加成绩文件。", status="needs_input")
    file_url = str(attachment.get("url") or "")
    local_file = str(attachment.get("file") or "")
    b64 = attachment.get("base64")
    file_id = attachment.get("file_id")
    if not file_url and file_id:
        try:
            info = await bot.call_api("get_file", file_id=file_id)
            info = info.get("data", info) if isinstance(info, dict) else info
            file_url = str(info.get("url") or "")
            local_file = str(info.get("file") or "")
            b64 = info.get("base64")
        except Exception:
            return outcome("NEED_ATTACHMENT", "无法读取当前文件附件。", status="needs_input")

    root = Path(s.SCORE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f"{user_id}_{uuid4().hex}.upload"
    try:
        if b64:
            raw = base64.b64decode(str(b64), validate=True)
            if len(raw) > 10 * 1024 * 1024:
                return outcome("INVALID_ARGUMENT", "文件超过 10MB 限制。", status="needs_input")
            await asyncio.to_thread(temp.write_bytes, raw)
        elif local_file and Path(local_file).is_file():
            source = Path(local_file).resolve()
            await asyncio.to_thread(temp.write_bytes, source.read_bytes())
        elif file_url.startswith(("http://", "https://")):
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", file_url, timeout=30) as response:
                    if response.status_code != 200:
                        return outcome("UPSTREAM_UNAVAILABLE", "下载成绩文件失败。", status="unavailable")
                    size = 0
                    with open(temp, "wb") as target:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 10 * 1024 * 1024:
                                return outcome("INVALID_ARGUMENT", "文件超过 10MB 限制。", status="needs_input")
                            target.write(chunk)
        else:
            return outcome("NEED_ATTACHMENT", "附件没有可读取的文件内容。", status="needs_input")

        success, message, parsed, player_info = await asyncio.to_thread(s._parse_file, str(temp))
        if not success:
            return outcome("INVALID_DATA", message[:300], status="needs_input")
        target = Path(s.SCORE_DIR) / f"{user_id}.json"
        merged = s._merge_score_items(s._read_score_json(str(target)), parsed)
        merged = s._inject_level_value(merged, s._load_song_meta())
        temp_json = str(target) + ".tmp"
        with open(temp_json, "w", encoding="utf-8") as output:
            json.dump(merged, output, ensure_ascii=False, indent=4)
        os.replace(temp_json, target)
        if player_info:
            info_path = Path(s.SCORE_DIR) / f"{user_id}_info.json"
            info_temp = str(info_path) + ".tmp"
            with open(info_temp, "w", encoding="utf-8") as output:
                json.dump(player_info, output, ensure_ascii=False, indent=4)
            os.replace(info_temp, info_path)
        result = outcome(data={"user_id": str(user_id), "items": len(parsed), "total": len(merged)})
        result["effect"] = "write"
        return result
    finally:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
