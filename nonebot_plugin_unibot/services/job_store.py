"""Small persistent FIFO job store used by native providers.

The store owns task state and deduplication, while providers own the actual
business operation.  It deliberately does not retry unknown side effects.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4


async def _db_thread(function):
    """Cancellation must not abandon a thread still holding a SQLite connection."""
    work = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if work.done() and not work.cancelled():
            work.exception()  # observe any DB error while preserving cancellation
        raise


async def async_call(function, *args, **kwargs):
    return await _db_thread(lambda: function(*args, **kwargs))


TERMINAL = {"succeeded", "failed", "partial", "cancelled", "interrupted", "unknown"}


@dataclass(frozen=True)
class Job:
    job_id: str
    provider: str
    capability_id: str
    operation_id: str
    bot_id: str
    context_type: str
    context_id: str
    actor_id: str
    source_message_id: str
    status: str
    current_step: str
    response_owner: str
    created_at: float
    started_at: float | None
    updated_at: float
    finished_at: float | None
    payload: dict[str, Any]
    result: dict[str, Any]
    public_error_code: str | None
    retryable: bool
    side_effect_state: str
    delivery_state: str
    notification_state: str
    expression_state: str = "pending"
    expression_text: str = ""


def _fingerprint(provider, capability_id, operation_id, context_id, actor_id, bot_id, context_type):
    raw = "\x1f".join((provider, capability_id, operation_id, bot_id, context_type, context_id, actor_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, path: str | Path, *, max_pending=64, max_per_actor=4):
        self.path = str(path)
        self._lock = asyncio.Lock()
        self._ready = False
        self.max_pending = max_pending
        self.max_per_actor = max_per_actor

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def _connection(self):
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_sync(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
              capability_id TEXT NOT NULL, operation_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL, bot_id TEXT NOT NULL,
              context_type TEXT NOT NULL, context_id TEXT NOT NULL,
              actor_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
              status TEXT NOT NULL, current_step TEXT NOT NULL DEFAULT 'queued',
              response_owner TEXT NOT NULL DEFAULT 'agent',
              created_at REAL NOT NULL, started_at REAL, updated_at REAL NOT NULL,
              finished_at REAL, payload TEXT NOT NULL, result TEXT NOT NULL,
              public_error_code TEXT, retryable INTEGER NOT NULL DEFAULT 0,
              side_effect_state TEXT NOT NULL DEFAULT 'none',
              delivery_state TEXT NOT NULL DEFAULT 'not_sent',
              notification_state TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS jobs_fifo ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS jobs_context ON jobs(context_id, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_dedupe
              ON jobs(fingerprint) WHERE status IN ('queued','running','cancel_requested');
            """)
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DROP INDEX IF EXISTS jobs_active_dedupe")
                for row in db.execute("SELECT * FROM jobs").fetchall():
                    fingerprint = _fingerprint(row["provider"], row["capability_id"], row["operation_id"],
                                               row["context_id"], row["actor_id"], row["bot_id"], row["context_type"])
                    db.execute("UPDATE jobs SET fingerprint=? WHERE job_id=?", (fingerprint, row["job_id"]))
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_dedupe ON jobs(fingerprint) WHERE status IN ('queued','running','cancel_requested')")
                db.execute("PRAGMA user_version=2")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            if "expression_state" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN expression_state TEXT NOT NULL DEFAULT 'pending'")
                db.execute("ALTER TABLE jobs ADD COLUMN expression_text TEXT NOT NULL DEFAULT ''")
            db.execute("PRAGMA user_version=3")

    async def init(self):
        if not self._ready:
            async with self._lock:
                if not self._ready:
                    await _db_thread(self._init_sync)
                    self._ready = True

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        return Job(job_id=row["job_id"], provider=row["provider"], capability_id=row["capability_id"],
                   operation_id=row["operation_id"], bot_id=row["bot_id"], context_type=row["context_type"],
                   context_id=row["context_id"], actor_id=row["actor_id"],
                   source_message_id=row["source_message_id"], status=row["status"],
                   current_step=row["current_step"], response_owner=row["response_owner"],
                   created_at=row["created_at"], started_at=row["started_at"], updated_at=row["updated_at"],
                   finished_at=row["finished_at"], payload=json.loads(row["payload"]),
                   result=json.loads(row["result"]), public_error_code=row["public_error_code"],
                   retryable=bool(row["retryable"]), side_effect_state=row["side_effect_state"],
                   delivery_state=row["delivery_state"], notification_state=row["notification_state"],
                   expression_state=row["expression_state"], expression_text=row["expression_text"])

    async def submit(self, *, provider: str, capability_id: str, operation_id: str,
                     bot_id: str, context_type: str, context_id: str, actor_id: str,
                     source_message_id: str, payload: dict[str, Any] | None = None,
                     response_owner: str = "agent") -> tuple[Job, bool]:
        await self.init()
        now = time.time()
        fingerprint = _fingerprint(provider, capability_id, operation_id, context_id, actor_id, bot_id, context_type)
        job_id = uuid4().hex
        def op():
            with self._connection() as db:
                row = db.execute("""SELECT * FROM jobs WHERE fingerprint=? AND
                    (status IN ('queued','running','cancel_requested','unknown') OR source_message_id=?)
                    ORDER BY created_at DESC LIMIT 1""", (fingerprint, source_message_id)).fetchone()
                if row:
                    return self._row(row), False
                pending = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchone()[0]
                own = db.execute("""SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancel_requested')
                    AND bot_id=? AND context_type=? AND context_id=? AND actor_id=?""",
                    (bot_id, context_type, context_id, actor_id)).fetchone()[0]
                if pending >= self.max_pending or own >= self.max_per_actor:
                    raise QueueFull("QUEUE_FULL")
                db.execute("""INSERT INTO jobs
                    (job_id,provider,capability_id,operation_id,fingerprint,bot_id,context_type,context_id,
                     actor_id,source_message_id,status,current_step,response_owner,created_at,updated_at,payload,result)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, provider, capability_id, operation_id, fingerprint, bot_id, context_type,
                     context_id, actor_id, source_message_id, "queued", "queued", response_owner, now, now,
                     json.dumps(payload or {}, ensure_ascii=False), "{}"))
                return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()), True
        async with self._lock:
            return await _db_thread(op)

    async def begin_inline(self, *, provider, operation_id, source_message_id, bot_id, context_type,
                           context_id, actor_id, payload, response_owner="inline", initial_result=None):
        """Record already-generated media sent by the current native handler."""
        await self.init()
        now, job_id = time.time(), uuid4().hex
        fingerprint = _fingerprint(provider, "media.inline", operation_id, context_id, actor_id, bot_id, context_type)
        def op():
            with self._connection() as db:
                old = db.execute("SELECT * FROM jobs WHERE fingerprint=? AND source_message_id=? LIMIT 1",
                                 (fingerprint, source_message_id)).fetchone()
                if old:
                    return self._row(old), False
                db.execute("""INSERT INTO jobs (job_id,provider,capability_id,operation_id,fingerprint,bot_id,
                    context_type,context_id,actor_id,source_message_id,status,current_step,response_owner,
                    created_at,started_at,updated_at,payload,result,notification_state)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id,provider,"media.inline",operation_id,fingerprint,bot_id,context_type,context_id,actor_id,
                     source_message_id,"running","caching_media",response_owner,now,now,now,json.dumps(payload,ensure_ascii=False),
                     json.dumps(initial_result or {},ensure_ascii=False),"not_required"))
                return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()), True
        async with self._lock:
            return await _db_thread(op)

    async def find_submission(self, *, provider, capability_id, operation_id, source_message_id,
                              bot_id, context_type, context_id, actor_id):
        await self.init()
        fingerprint = _fingerprint(provider, capability_id, operation_id, context_id, actor_id, bot_id, context_type)
        def op():
            db = self._db()
            try:
                return self._row(db.execute("""SELECT * FROM jobs WHERE fingerprint=? AND source_message_id=?
                    ORDER BY created_at DESC LIMIT 1""", (fingerprint, source_message_id)).fetchone())
            finally:
                db.close()
        return await _db_thread(op)

    async def get(self, job_id: str) -> Job | None:
        await self.init()
        def op():
            db = self._db()
            try:
                return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
            finally:
                db.close()
        return await _db_thread(op)

    async def list(self, *, context_id: str, actor_id: str | None = None,
                   bot_id=None, context_type=None, limit: int = 20) -> list[Job]:
        await self.init()
        limit = max(1, min(int(limit), 100))
        def op():
            db = self._db()
            clauses, values = ["context_id=?"], [context_id]
            for column, value in (("actor_id", actor_id), ("bot_id", bot_id), ("context_type", context_type)):
                if value is not None:
                    clauses.append(column + "=?"); values.append(value)
            try:
                rows = db.execute("SELECT * FROM jobs WHERE " + " AND ".join(clauses) +
                                  " ORDER BY created_at DESC LIMIT ?", [*values, limit]).fetchall()
            finally:
                db.close()
            return [self._row(row) for row in rows]
        return await _db_thread(op)

    async def claim_next(self, provider=None) -> Job | None:
        await self.init()
        now = time.time()
        async with self._lock:
            def op():
                with self._connection() as db:
                    row = db.execute("SELECT * FROM jobs WHERE status='queued'" +
                                     (" AND provider=?" if provider else "") + " ORDER BY rowid LIMIT 1",
                                     (provider,) if provider else ()).fetchone()
                    if row is None:
                        return None
                    changed = db.execute("UPDATE jobs SET status='running', current_step='running', started_at=?, updated_at=? WHERE job_id=? AND status='queued'",
                                         (now, now, row["job_id"])).rowcount
                    if not changed:
                        return None
                    return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())
            return await _db_thread(op)

    async def update(self, job_id: str, *, status: str | None = None, current_step: str | None = None,
                     result: dict[str, Any] | None = None, public_error_code: str | None = None,
                     retryable: bool | None = None, side_effect_state: str | None = None,
                     delivery_state: str | None = None, notification_state: str | None = None) -> Job | None:
        await self.init()
        now = time.time()
        fields, values = ["updated_at=?"], [now]
        for key, value in (("status", status), ("current_step", current_step), ("public_error_code", public_error_code),
                           ("side_effect_state", side_effect_state), ("delivery_state", delivery_state),
                           ("notification_state", notification_state)):
            if value is not None:
                fields.append(key + "=?"); values.append(value)
        if result is not None:
            fields.append("result=?"); values.append(json.dumps(result, ensure_ascii=False))
        if retryable is not None:
            fields.append("retryable=?"); values.append(int(retryable))
        if status in TERMINAL:
            fields.append("finished_at=?"); values.append(now)
        values.append(job_id)
        async with self._lock:
            def op():
                with self._connection() as db:
                    db.execute("UPDATE jobs SET " + ",".join(fields) + " WHERE job_id=?", values)
                    return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
            return await _db_thread(op)

    async def start_effect(self, job_id: str, current_step: str) -> bool:
        """Cancel and platform-attempt admission share one transaction boundary."""
        await self.init()
        def op():
            with self._connection() as db:
                return db.execute("""UPDATE jobs SET current_step=?,side_effect_state='preparing',updated_at=?
                    WHERE job_id=? AND status='running'""", (current_step, time.time(), job_id)).rowcount == 1
        async with self._lock:
            return await _db_thread(op)

    async def cancel(self, job_id: str, *, actor_id: str, allow_other: bool = False,
                     bot_id=None, context_type=None, context_id=None) -> Job | None:
        await self.init()
        def op():
            with self._connection() as db:
                job = self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
                if job is None or (not allow_other and job.actor_id != str(actor_id)):
                    return None
                if any(value is not None and str(getattr(job, key)) != str(value) for key, value in
                       (("bot_id", bot_id), ("context_type", context_type), ("context_id", context_id))):
                    return None
                if job.status in ("queued", "running"):
                    status = "cancelled" if job.status == "queued" else "cancel_requested"
                    db.execute("UPDATE jobs SET status=?,current_step=?,updated_at=?,finished_at=? WHERE job_id=?",
                               (status, status, time.time(), time.time() if status == "cancelled" else None, job_id))
                return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
        async with self._lock:
            return await _db_thread(op)

    async def mark_interrupted(self):
        await self.init()
        now = time.time()
        async with self._lock:
            def op():
                with self._connection() as db:
                    db.execute("""UPDATE jobs SET status=CASE WHEN side_effect_state='none' THEN 'interrupted' ELSE 'unknown' END,
                        current_step='interrupted',public_error_code='JOB_INTERRUPTED',updated_at=?,finished_at=?
                        WHERE status IN ('queued','running','cancel_requested')""", (now, now))
                    db.execute("UPDATE jobs SET notification_state='unknown',delivery_state='unknown' WHERE notification_state='sending'")
            await _db_thread(op)

    async def claim_notification(self, job_id):
        await self.init()
        def op():
            with self._connection() as db:
                return db.execute("UPDATE jobs SET notification_state='sending' WHERE job_id=? AND notification_state='pending' AND status IN ('succeeded','failed','partial','cancelled','interrupted','unknown')",
                                  (job_id,)).rowcount == 1
        async with self._lock:
            return await _db_thread(op)

    async def pending_notifications(self, provider=None, limit=20):
        await self.init()
        def op():
            db = self._db()
            try:
                rows = db.execute("SELECT * FROM jobs WHERE notification_state='pending' AND status IN ('succeeded','failed','partial','cancelled','interrupted','unknown')" +
                                  (" AND provider=?" if provider else "") + " ORDER BY created_at LIMIT ?",
                                  (provider, limit) if provider else (limit,)).fetchall()
                return [self._row(row) for row in rows]
            finally:
                db.close()
        return await _db_thread(op)

    async def claim_expression(self, job_id):
        await self.init()
        def op():
            with self._connection() as db:
                return db.execute("UPDATE jobs SET expression_state='attempted' WHERE job_id=? AND expression_state='pending'",
                                  (job_id,)).rowcount == 1
        return await _db_thread(op)

    async def save_expression(self, job_id, text):
        await self.init()
        def op():
            with self._connection() as db:
                db.execute("UPDATE jobs SET expression_state='done',expression_text=? WHERE job_id=?", (text, job_id))
        await _db_thread(op)

    async def queue_position(self, job_id):
        await self.init()
        def op():
            db = self._db()
            try:
                row = db.execute("SELECT rowid,* FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row is None or row["status"] != "queued":
                    return None
                return db.execute("SELECT count(*) FROM jobs WHERE provider=? AND status='queued' AND rowid<=?",
                                  (row["provider"], row["rowid"])).fetchone()[0]
            finally:
                db.close()
        return await _db_thread(op)


class QueueFull(RuntimeError):
    pass


async def run_one(store: JobStore, handler: Callable[[Job], Awaitable[dict[str, Any]]], *, provider=None, job=None) -> Job | None:
    job = job or await store.claim_next(provider)
    if job is None:
        return None
    try:
        result = await handler(job)
    except asyncio.CancelledError:
        await store.update(job.job_id, status="unknown", current_step="unknown", public_error_code="RESULT_UNKNOWN",
                           side_effect_state="unknown", notification_state="pending")
        raise
    except Exception:
        latest = await store.get(job.job_id)
        state = "unknown" if latest and latest.side_effect_state != "none" else "failed"
        await store.update(job.job_id, status=state, current_step=state, public_error_code="RESULT_UNKNOWN" if state == "unknown" else "INTERNAL_ERROR",
                           result={"status": "unavailable"}, notification_state="pending")
    else:
        job_status = str(result.get("status", "succeeded"))
        if job_status == "ok":
            job_status = "succeeded"
        if job_status not in TERMINAL:
            job_status = "failed"
        await store.update(job.job_id, status=job_status,
                           current_step=str(result.get("current_step", "finished")), result=result,
                           public_error_code=result.get("code"), side_effect_state=str(result.get("side_effect_state", "none")),
                           delivery_state=str(result.get("delivery_state", "not_sent")), notification_state="pending")
    return await store.get(job.job_id)


async def notify_once(store: JobStore, job_id: str, callback: Callable[[Job], Awaitable[None]]) -> bool:
    """Deliver one terminal notification; unknown delivery is never retried automatically."""
    job = await store.get(job_id)
    if job is None or job.status not in TERMINAL or job.notification_state != "pending":
        return False
    if not await store.claim_notification(job_id):
        return False
    try:
        receipt = await callback(job)
        if isinstance(receipt, dict) and receipt.get("silent"):
            await store.update(job_id, notification_state="suppressed")
            return True
        if isinstance(receipt, dict) and receipt.get("deferred"):
            await store.update(job_id, notification_state="pending", delivery_state="not_sent")
            return False
    except BaseException as exc:
        await store.update(job_id, notification_state="unknown", delivery_state="unknown")
        if isinstance(exc, asyncio.CancelledError):
            raise
        return False
    await store.update(job_id, notification_state="sent", delivery_state="sent")
    return True
