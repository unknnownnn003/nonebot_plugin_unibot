"""Durable OneBot presentation plans. Only trusted presenters create plans.

No renderer, business action, random selection or downloader runs during replay.
The provider's single Bot process owns this local store and recovers it at startup.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from weakref import WeakValueDictionary

from .job_store import _db_thread

_replay_locks = WeakValueDictionary()


def scope(bot, event):
    group = getattr(event, "group_id", None)
    return json.dumps([str(bot.self_id), "group" if group is not None else "private",
                       str(group if group is not None else event.user_id), str(event.user_id)], separators=(",", ":"))


def encode(value):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    if isinstance(value, MessageSegment):
        return ["segment", value.type, encode(value.data)]
    if isinstance(value, Message):
        return ["message", [encode(part) for part in value]]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, dict):
        return ["dict", [[key, encode(item)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return ["list", [encode(item) for item in value]]
    if value is None or isinstance(value, (str, int, float, bool)):
        return ["value", value]
    raise ValueError("unsupported presentation value")


def decode(value):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    tag = value[0]
    if tag == "segment":
        return MessageSegment(value[1], decode(value[2]))
    if tag == "message":
        return Message([decode(part) for part in value[1]])
    if tag == "bytes":
        return base64.b64decode(value[1], validate=True)
    if tag == "dict":
        return {key: decode(item) for key, item in value[1]}
    if tag == "list":
        return [decode(item) for item in value[1]]
    if tag == "value":
        return value[1]
    raise ValueError("invalid presentation encoding")


class PresentationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.files = self.path.parent / (self.path.stem + "_files")
        self._ready = False
        self._lock = asyncio.Lock()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    async def init(self):
        async with self._lock:
            if self._ready:
                return
            def op():
                self.files.mkdir(parents=True, exist_ok=True)
                with self.connection() as db:
                    db.execute("""CREATE TABLE IF NOT EXISTS presentations (
                        artifact_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, scope TEXT NOT NULL,
                        artifact_key TEXT NOT NULL, bundle TEXT NOT NULL, kind TEXT NOT NULL,
                        state TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL,
                        token TEXT NOT NULL DEFAULT '', receipt TEXT NOT NULL DEFAULT '{}',
                        updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(operation_id,scope,artifact_key))""")
                    db.execute("""CREATE TABLE IF NOT EXISTS replay_requests (
                        request_key TEXT PRIMARY KEY, result TEXT NOT NULL)""")
            await _db_thread(op)
            self._ready = True

    async def recover(self):
        await self.init()
        def op():
            with self.connection() as db:
                db.execute("UPDATE presentations SET state='unknown',token='',updated=?,revision=revision+1 WHERE state='sending'", (time.time(),))
                db.execute("""UPDATE presentations SET state='unknown',updated=?,revision=revision+1 WHERE state='held' AND EXISTS (
                    SELECT 1 FROM presentations parent WHERE parent.operation_id=presentations.operation_id
                    AND parent.scope=presentations.scope AND parent.bundle=presentations.bundle
                    AND parent.kind='forward' AND parent.state='unknown')""", (time.time(),))
        await _db_thread(op)

    async def stage(self, operation_id, identity, key, content, *, kind="message", bundle="", held=False):
        await self.init()
        if kind not in {"message", "forward", "forward_part"}:
            raise ValueError("invalid presentation kind")
        raw = await _db_thread(lambda: json.dumps(encode(content), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest = hashlib.sha256(raw).hexdigest()
        def op():
            with self.connection() as db:
                row = db.execute("SELECT * FROM presentations WHERE operation_id=? AND scope=? AND artifact_key=?",
                                 (operation_id, identity, key)).fetchone()
                if row:
                    if row["digest"] != digest or row["kind"] != kind or row["bundle"] != bundle:
                        raise ValueError("presentation identity changed")
                    return dict(row)
                artifact_id = uuid4().hex
                path = self.files.resolve() / (artifact_id + ".json")
                temp = path.with_suffix(".tmp")
                try:
                    temp.write_bytes(raw)
                    temp.replace(path)
                    db.execute("""INSERT INTO presentations
                        (artifact_id,operation_id,scope,artifact_key,bundle,kind,state,path,digest,updated)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""", (artifact_id, operation_id, identity, key, bundle, kind,
                            "held" if held else "not_sent", str(path), digest, time.time()))
                    db.commit()
                except BaseException:
                    temp.unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
                    raise
                return dict(db.execute("SELECT * FROM presentations WHERE artifact_id=?", (artifact_id,)).fetchone())
        return await _db_thread(op)

    async def stage_forward(self, operation_id, identity, nodes, *, bundle="main"):
        for index, node in enumerate(nodes):
            await self.stage(operation_id, identity, f"{bundle}:part:{index:06}", node.data["content"],
                             kind="forward_part", bundle=bundle, held=True)
        return await self.stage(operation_id, identity, f"{bundle}:forward", nodes, kind="forward", bundle=bundle)

    async def pending(self, operation_id, identity, *, limit=20):
        await self.init()
        def op():
            with self.connection() as db:
                return [dict(row) for row in db.execute("""SELECT * FROM presentations WHERE operation_id=? AND scope=?
                    AND (state='not_sent' OR (state='unknown' AND kind!='forward')) ORDER BY artifact_key LIMIT ?""",
                    (operation_id, identity, max(1, min(int(limit), 200))))]
        return await _db_thread(op)

    async def exists(self, operation_id, identity):
        await self.init()
        def op():
            with self.connection() as db:
                return db.execute("""SELECT 1 FROM presentations WHERE operation_id=? AND scope=?
                    AND kind IN ('message','forward') LIMIT 1""", (operation_id, identity)).fetchone() is not None
        return await _db_thread(op)

    async def outstanding(self, operation_id, identity):
        """Include in-flight/held content even if it cannot yet be replayed."""
        await self.init()
        def op():
            with self.connection() as db:
                return db.execute("""SELECT 1 FROM presentations WHERE operation_id=? AND scope=?
                    AND state!='sent' AND (kind!='forward' OR state!='unknown') LIMIT 1""",
                    (operation_id, identity)).fetchone() is not None
        return await _db_thread(op)

    async def _finish(self, row, state, receipt=None):
        def op():
            with self.connection() as db:
                changed = db.execute("""UPDATE presentations SET state=?,token='',receipt=?,updated=?,revision=revision+1
                    WHERE artifact_id=? AND state='sending' AND token=?""", (state,
                        json.dumps(receipt or {}, ensure_ascii=False), time.time(), row["artifact_id"], row["token"])).rowcount
                if changed and row.get("kind") == "forward" and state in {"sent", "unknown"}:
                    db.execute("""UPDATE presentations SET state=?,updated=?,revision=revision+1 WHERE operation_id=? AND scope=?
                        AND bundle=? AND kind='forward_part' AND state='held'""",
                        (state, time.time(), row["operation_id"], row["scope"], row["bundle"]))
        await _db_thread(op)

    def _read(self, row):
        path = Path(row["path"]).resolve()
        if path.parent != self.files.resolve():
            raise ValueError("presentation outside its directory")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["digest"]:
            raise ValueError("presentation changed")
        return decode(json.loads(raw))

    async def send(self, row, bot, event, *, allow_unknown=False, before_send=None):
        identity, token = scope(bot, event), uuid4().hex
        if row["scope"] != identity:
            raise ValueError("presentation does not belong to this context")
        def claim():
            with self.connection() as db:
                return db.execute("""UPDATE presentations SET state='sending',token=?,updated=?,revision=revision+1 WHERE artifact_id=?
                    AND scope=? AND revision=? AND (state='not_sent' OR (state='unknown' AND ? AND kind!='forward'))""",
                    (token, time.time(), row["artifact_id"], identity, row["revision"], allow_unknown)).rowcount == 1
        attempt = dict(row, token=token)
        try:
            claimed = await _db_thread(claim)
        except asyncio.CancelledError:
            await self._finish(attempt, "not_sent")
            raise
        if not claimed:
            return {"delivery": "unknown", "claimed": False}
        state, receipt = "not_sent", None
        try:
            content = await _db_thread(lambda: self._read(row))
            if before_send is not None and not await before_send():
                return {"delivery": "not_sent", "claimed": True, "denied": True}
            state = "unknown"
            if row["kind"] == "forward":
                if getattr(event, "group_id", None) is not None:
                    receipt = await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=content)
                else:
                    receipt = await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=content)
            else:
                receipt = await bot.send(event, content)
            state = "sent"
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            await self._finish(attempt, state, receipt)
        return {"delivery": state, "claimed": True, "receipt": receipt}

    async def replay(self, operation_id, bot, event, *, limit=20):
        key = (str(self.path.resolve()), operation_id, scope(bot, event))
        lock = _replay_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _replay_locks[key] = lock
        async with lock:
            return await self._replay(operation_id, bot, event, limit=limit)

    async def _replay(self, operation_id, bot, event, *, limit=20):
        await self.init()
        identity = scope(bot, event)
        request_key = hashlib.sha256(json.dumps([operation_id, identity, str(event.message_id)]).encode()).hexdigest()
        def claim():
            with self.connection() as db:
                return db.execute("INSERT OR IGNORE INTO replay_requests VALUES (?,?)", (request_key,
                    json.dumps({"sent": 0, "remaining": True, "business_reexecuted": False}))).rowcount == 1
        if not await _db_thread(claim):
            return {"sent": 0, "remaining": await self.outstanding(operation_id, identity),
                    "business_reexecuted": False, "deduplicated": True}
        # The real event owns one attempt even if finalization fails or the
        # process stops. A fresh explicit message may retry remaining content.
        rows = await self.pending(operation_id, identity, limit=limit)
        results = [await self.send(row, bot, event, allow_unknown=True) for row in rows]
        output = {"sent": sum(row["delivery"] == "sent" for row in results),
                  "remaining": await self.outstanding(operation_id, identity), "business_reexecuted": False}
        def finish():
            with self.connection() as db:
                db.execute("UPDATE replay_requests SET result=? WHERE request_key=?", (json.dumps(output), request_key))
        await _db_thread(finish)
        return output
