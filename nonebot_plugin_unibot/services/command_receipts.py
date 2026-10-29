"""Persistent ownership of native command steps, after NoneBot preprocessing.

The original Dependent params and parameterless got()/receive() hooks remain
intact. This guard never replays a handler or alters Bot/Matcher methods.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from contextvars import ContextVar

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from .job_store import _db_thread

_active_call = ContextVar("native_command_call", default=None)


def identity(bot, event):
    group = getattr(event, "group_id", None)
    if str(bot.self_id) != str(event.self_id):
        raise ValueError("command Bot/Event mismatch")
    return json.dumps([str(bot.self_id), "group" if group is not None else "private",
        str(group if group is not None else event.user_id), str(event.user_id)], separators=(",", ":")), str(event.message_id)


class CommandReceipts:
    def __init__(self, path):
        self.path = Path(path)
        self._ready = False
        self._lock = asyncio.Lock()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
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
        if self._ready:
            return
        async with self._lock:
            if not self._ready:
                def op():
                    with self.connection() as db:
                        db.execute("""CREATE TABLE IF NOT EXISTS native_commands (
                            receipt_id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_message_id TEXT NOT NULL,
                            route TEXT NOT NULL, claimed REAL NOT NULL, state TEXT NOT NULL DEFAULT 'claimed')""")
                        columns = {row[1] for row in db.execute("PRAGMA table_info(native_commands)")}
                        if "state" not in columns:
                            db.execute("ALTER TABLE native_commands ADD COLUMN state TEXT NOT NULL DEFAULT 'claimed'")
                        db.execute("CREATE INDEX IF NOT EXISTS native_command_source ON native_commands(scope,source_message_id)")
                await _db_thread(op)
                self._ready = True

    async def claim(self, bot, event, route):
        await self.init()
        context, source = identity(bot, event)
        receipt = hashlib.sha256(json.dumps([context, source, route]).encode()).hexdigest()
        def op():
            with self.connection() as db:
                return db.execute("INSERT OR IGNORE INTO native_commands (receipt_id,scope,source_message_id,route,claimed) VALUES (?,?,?,?,?)",
                                  (receipt, context, source, route, time.time())).rowcount == 1
        return await _db_thread(op)

    async def state(self, bot, event, route, value=None):
        await self.init()
        context, source = identity(bot, event)
        receipt = hashlib.sha256(json.dumps([context, source, route]).encode()).hexdigest()
        def op():
            with self.connection() as db:
                if value is not None:
                    db.execute("UPDATE native_commands SET state=? WHERE receipt_id=? AND state='claimed'", (value, receipt))
                row = db.execute("SELECT state FROM native_commands WHERE receipt_id=?", (receipt,)).fetchone()
                return row[0] if row else None
        return await _db_thread(op)

    async def owns(self, bot, event):
        await self.init()
        context, source = identity(bot, event)
        def op():
            with self.connection() as db:
                return db.execute("SELECT 1 FROM native_commands WHERE scope=? AND source_message_id=? LIMIT 1",
                                  (context, source)).fetchone() is not None
        return await _db_thread(op)


_receipts = None


async def source_owned(bot, event):
    # Leaf API imports in isolation have no native commands installed. A real
    # loaded plugin configures this store before it can receive command events.
    return await _receipts.owns(bot, event) if _receipts is not None else False


def install(modules, *, receipts=None):
    """Add a public Depends hook before every native Command handler step."""
    global _receipts
    from nonebot.dependencies import Dependent
    from nonebot.exception import FinishedException, PausedException, RejectedException
    from nonebot.matcher import Matcher
    from nonebot.params import Depends
    from nonebot.rule import CommandRule, ShellCommandRule, RegexRule, FullmatchRule, StartswithRule, EndswithRule
    if receipts is None:
        from nonebot_plugin_localstore import get_plugin_data_file
        receipts = CommandReceipts(get_plugin_data_file("native_commands.sqlite3"))
    _receipts = receipts
    installed = []

    class DuplicateCommand(Exception):
        pass

    class ReceiptDependent(Dependent):
        async def __call__(self, **kwargs):
            frame = {"created": False}
            token = _active_call.set(frame)
            state = "unknown"
            try:
                result = await super().__call__(**kwargs)
            except DuplicateCommand:
                if getattr(kwargs.get("matcher"), "temp", False):
                    # Reject without a prompt recreates the waiting got session.
                    # Finishing here would destroy it on a duplicate command.
                    raise RejectedException from None
                raise FinishedException from None
            except (RejectedException, PausedException):
                state = "waiting"
                raise
            except FinishedException:
                state = "handled"
                raise
            else:
                state = "handled"
                return result
            finally:
                try:
                    if frame["created"]:
                        await receipts.state(kwargs["bot"], kwargs["event"], self.receipt_route, state)
                finally:
                    _active_call.reset(token)

    def dependency(route):
        async def enter(bot: Bot, event: MessageEvent):
            created = await receipts.claim(bot, event, route)
            event.__dict__.setdefault("_native_capability_results", {})["__native_command__"] = dict(
                status="unknown", code="ALREADY_HANDLED", delivery="unknown", response_owner="command")
            if not created:
                # The receipt proves ownership, not successful business work.
                # An interrupted/unknown native action must not auto-execute.
                raise DuplicateCommand
            _active_call.get()["created"] = True
        return enter

    for module in modules:
        for name, matcher in vars(module).items():
            if not isinstance(matcher, type) or not issubclass(matcher, Matcher):
                continue
            if matcher.module_name != module.__name__ or getattr(matcher, "_v31_receipts_installed", False):
                continue
            named_command = (module.__name__.rsplit(".", 1)[-1], name) in {
                ("custom_dish", "add_dish"), ("monkey_emoji", "number_only_matcher"), ("draw_hub", "draw_router")}
            if not named_command and not any(isinstance(check.call, (CommandRule, ShellCommandRule, FullmatchRule, StartswithRule, EndswithRule))
                    or (matcher.block and isinstance(check.call, RegexRule)) for check in matcher.rule.checkers):
                continue
            for index, original in enumerate(matcher.handlers):
                route = f"{module.__name__.rsplit('.', 1)[-1]}:{name}:{index}"
                guard = Dependent.parse_parameterless((Depends(dependency(route), use_cache=False),), matcher.HANDLER_PARAM_TYPES)
                wrapped = ReceiptDependent(call=original.call, params=original.params,
                    parameterless=guard + original.parameterless)
                wrapped.receipt_route = route
                matcher.handlers[index] = wrapped
            matcher._v31_receipts_installed = True
            installed.append((module.__name__, name, len(matcher.handlers)))
    return installed
