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
from .attachments import read_file, AttachmentError
from .maintenance import observed_io

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _segment_data(segment):
    if isinstance(segment, dict):
        return segment.get("type"), segment.get("data", {}) or {}
    return getattr(segment, "type", None), getattr(segment, "data", {}) or {}


async def import_file(bot, event, user_id: str):
    from .. import get_player_score as s
    from .paths import UPLOAD_DIR
    root = UPLOAD_DIR
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    temp = root / f"{user_id}_{uuid4().hex}.upload"
    try:
        raw = await read_file(bot, event, MAX_UPLOAD_BYTES)
        await observed_io(temp.write_bytes, raw)
        ok, hint, rows, profile = await observed_io(s._parse_file, str(temp))
        if not ok:
            return outcome("INVALID_DATA", "解析失败，请使用落雪CSV或Rin JSON文件。", status="needs_input")
        total = await observed_io(commit_scores, str(user_id), rows, profile)
        result = outcome(data={"user_id": str(user_id), "items": len(rows), "total": total},
                         hint=f"保存成功\n{hint}\n当前总谱面数: {total}")
        result.update(effect="local_write", side_effect_state="committed")
        return result
    except PartialWriteError:
        result = outcome("PARTIAL_WRITE", "成绩已保存，但玩家资料保存失败，请管理员核对。", status="partial")
        result.update(effect="local_write", side_effect_state="committed")
        return result
    except AttachmentError as exc:
        return outcome(exc.code, "文件不合法或超过10MB限制。" if exc.code == "INVALID_ARGUMENT" else exc.hint, status="needs_input")
    except ValueError:
        return outcome("INVALID_ARGUMENT", "文件不合法或超过10MB限制。", status="needs_input")
    except (OSError, httpx.HTTPError):
        return outcome("UPSTREAM_UNAVAILABLE", "读取或保存成绩文件失败。", status="unavailable")
    finally:
        try:
            await observed_io(lambda: temp.unlink(missing_ok=True))
        except OSError:
            pass
