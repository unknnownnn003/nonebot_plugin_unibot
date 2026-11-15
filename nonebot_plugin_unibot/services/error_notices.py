"""Explicitly marked group error notices expire ten seconds after sending.

Mirrored in the two native providers so each remains independently loadable.
All copies share one runtime on the OneBot Bot class, including the ContextVar.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import json
import re

_ATTRIBUTE = "_three_plugin_error_notice_runtime_v1"
_runtime = None
DELAY_SECONDS = 10.0
ERROR_STATES = {"error", "failed", "unavailable", "denied", "partial", "unknown", "interrupted"}
INPUT_NOT_ERRORS = {"AUTHORIZATION_REQUIRED", "CONFIRMATION_REQUIRED", "VOICE_AMBIGUOUS", "AMBIGUOUS_TARGET", "NICKNAME_AMBIGUOUS", "AMBIGUOUS_NICKNAME", "MULTIPLE_MATCHES", "NEED_SELECTION"}
_ERROR_TEXT = re.compile(r"❌|失败|错误|报错|异常|超时|无法|无效|不正确|不合法|未找到|没有找到|不存在|未检测|未配置|尚未配置|不可用|未开启|未启用|没有权限|权限不足|不允许|不支持|请先|参数有误|输入有误|请输入|用法[：:]|不能超过|不能为空|找不到|未识别|请提供|缺少|额度不足|余额不足|次数不足|没有可用|暂时没有|未能|没有查询到|未查询到|尚未绑定|未绑定|不在.*范围|过于频繁|较频繁|不符合|不满足")


def is_error_result(result):
    if getattr(result, "_error_notice", False):
        return True
    if not isinstance(result, dict):
        return False
    status, code = result.get("status"), result.get("code")
    if not isinstance(status, str):
        return False
    return status in ERROR_STATES or (status == "needs_input" and (not isinstance(code, str) or code not in INPUT_NOT_ERRORS))


class _ErrorText(str):
    _error_notice = True

    def __add__(self, other):
        return _ErrorText(super().__add__(other))

    def __radd__(self, other):
        return _ErrorText(other + str(self))


def error_text(message):
    return _ErrorText(message)


def is_error_text(message):
    """Only for audited legacy report call sites, never an outbound-text hook."""
    if not isinstance(message, str):
        extract = getattr(message, "extract_plain_text", None)
        message = extract() if callable(extract) else ""
    return bool(_ERROR_TEXT.search(message))


class _Runtime:
    def __init__(self, delay):
        self.marked = ContextVar("three_plugin_error_notice", default=False)
        self.delay = delay
        self.pending = {}
        self.closing = False
        self.installed = False

    async def retract(self, bot, message_id, key):
        try:
            await asyncio.sleep(self.delay)
            await asyncio.wait_for(bot.delete_msg(message_id=message_id), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retraction failure must not produce another error notice or retry.
            from nonebot.log import logger
            logger.debug("群报错消息自动撤回未完成，已停止本次撤回。")
        finally:
            self.pending.pop(key, None)

    async def called(self, bot, exception, api, data, result):
        if self.closing or not self.marked.get() or exception is not None:
            return
        if api not in {"send_group_msg", "send_msg", "send_group_forward_msg"}:
            return
        if not isinstance(data, dict) or not data.get("group_id") or data.get("message_type") == "private":
            return
        if not isinstance(result, dict):
            return
        raw_id = result.get("message_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
            return
        try:
            message_id = int(raw_id)
        except (KeyError, ValueError, TypeError):
            return
        if not message_id:
            return
        key = (str(bot.self_id), message_id)
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(self.retract(bot, message_id, key), name="group-error-retract")

    async def close(self):
        self.closing = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()


# Leaf tests/imports can mark errors without initializing a NoneBot driver.
# Each plugin entry explicitly installs the shared OneBot hook at load time.
_runtime = _Runtime(DELAY_SECONDS)


def install(*, bot_class=None, driver=None):
    global _runtime
    if bot_class is None:
        from nonebot.adapters.onebot.v11 import Bot
        bot_class = Bot
    if driver is None:
        from nonebot import get_driver
        driver = get_driver()
    runtime = getattr(bot_class, _ATTRIBUTE, None)
    if runtime is None:
        runtime = _Runtime(DELAY_SECONDS)
        setattr(bot_class, _ATTRIBUTE, runtime)
        bot_class.on_called_api(runtime.called)
        driver.on_shutdown(runtime.close)
    _runtime = runtime
    runtime.installed = True
    return runtime


async def error_call(call, *args, **kwargs):
    runtime = _runtime
    if not runtime.installed:
        # Optional providers can be imported under an additional package alias.
        # Find the already installed runtime without initializing a driver here.
        from nonebot.adapters.onebot.v11 import Bot
        runtime = getattr(Bot, _ATTRIBUTE, runtime)
    token = runtime.marked.set(True)
    try:
        return await call(*args, **kwargs)
    finally:
        runtime.marked.reset(token)


async def result_call(result, call, *args, **kwargs):
    if is_error_result(result):
        return await error_call(call, *args, **kwargs)
    return await call(*args, **kwargs)


async def legacy_call(call, *args, **kwargs):
    # These arguments are program-generated status text at audited call sites.
    message = kwargs.get("message")
    if message is None:
        message = args[1] if getattr(call, "__name__", "") == "send" and len(args) > 1 else args[0] if args else ""
    if is_error_text(message):
        return await error_call(call, *args, **kwargs)
    return await call(*args, **kwargs)


async def notice_call(call, *args, **kwargs):
    """Send a typed error/status string without interpreting any user text."""
    message = kwargs.get("message", args[0] if args else "")
    if is_error_result(message):
        return await error_call(call, *args, **kwargs)
    return await call(*args, **kwargs)


def track_tool_result(session, name, arguments, result):
    errors = getattr(session, "error_notices", None)
    if errors is None:
        errors = session.error_notices = {}
    try:
        values = json.loads(arguments) if isinstance(arguments, str) else arguments
        if isinstance(values, dict):
            values = {key: value for key, value in values.items() if key != "_after_result"}
        canonical = json.dumps(values, sort_keys=True, ensure_ascii=False)
    except (ValueError, TypeError):
        canonical = str(arguments)
    key = name + ":" + hashlib.sha256(canonical.encode()).hexdigest()
    if is_error_result(result):
        errors[key] = True
    elif result.get("status") in {"ok", "succeeded", "running", "queued"}:
        errors.pop(key, None)


def restore_tool_errors(session, messages):
    """Rebuild only this owned request's trusted tool receipts after a restart."""
    session.error_notices = {}
    calls = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                if call.get("id") and function.get("name"):
                    calls[call["id"]] = (function["name"], function.get("arguments", "{}"))
        elif message.get("role") == "tool" and message.get("tool_call_id") in calls:
            try:
                output = json.loads(message.get("content", "{}"))
            except (ValueError, TypeError):
                continue
            if isinstance(output, dict):
                name, arguments = calls[message["tool_call_id"]]
                track_tool_result(session, name, arguments, output)
