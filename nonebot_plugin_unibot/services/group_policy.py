PLUGIN = "unibot"
"""Read the single policy owner dynamically; never cache a tool's enabled state."""
from contextlib import contextmanager
from functools import wraps
import importlib

def manager():
    try:
        from nonebot import get_plugin
        plugin = get_plugin("nonebot_plugin_unknnownnn")
        return plugin.module.feature_manager if plugin is not None else None
    except (ImportError, AttributeError): return None

def enabled(event, capability=None):
    group = getattr(event, "group_id", None)
    if group is None: return True
    fm = manager()
    if fm is None: return False
    if PLUGIN == "llmchat": return fm.is_plugin_enabled(group, PLUGIN)
    if capability in {"job.list", "job.get", "job.cancel"}: return True
    if capability == "job.replay": return fm.is_plugin_enabled(group, PLUGIN)
    feature = fm.policy.capability_feature(capability or "")
    return feature is not None and fm.is_plugin_feature_enabled(group, PLUGIN, feature)

def guard():
    fm = manager()
    return importlib.import_module(fm.__package__ + ".services.policy_guard") if fm is not None else None

@contextmanager
def scope(event, capability=None):
    group = getattr(event, "group_id", None)
    if group is None: yield; return
    runtime = guard()
    if runtime is None: raise RuntimeError("group_policy_unavailable")
    fm = manager()
    feature = None if PLUGIN == "llmchat" else fm.policy.capability_feature(capability or "")
    with runtime.scope(group, PLUGIN, feature):
        yield

def checked(function):
    @wraps(function)
    async def run(name, arguments, context):
        event = context["event"]
        if not enabled(event, name):
            from .results import outcome
            return outcome("FEATURE_DISABLED", "本群对应功能已关闭。", status="denied")
        if name in {"job.list", "job.get", "job.cancel"}:
            return await function(name, arguments, context)
        with scope(event, name):
            return await function(name, arguments, context)
    return run


def enabled_group(group):
    from types import SimpleNamespace
    return enabled(SimpleNamespace(group_id=group))

def check():
    runtime = guard()
    if runtime is not None: runtime.check()

def is_disabled(error):
    runtime = guard()
    return runtime is not None and isinstance(error, runtime.GroupPolicyDisabled)


from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import Event
_installed = False

def install():
    global _installed
    if _installed: return
    _installed = True
    from nonebot.message import run_preprocessor
    from nonebot.exception import IgnoredException
    root = __package__.rsplit(".services", 1)[0]
    @run_preprocessor
    async def check_native(matcher: Matcher, event: Event):
        if getattr(event, "group_id", None) is None or not (matcher.module_name or "").startswith(root + "."): return
        name = matcher.module_name.rsplit(".", 1)[-1]
        if name == "job_control": return
        fm = manager()
        feature = fm.policy.MODULE_FEATURES.get(name) if fm is not None else None
        if fm is None or feature is None or not fm.is_plugin_feature_enabled(event.group_id, PLUGIN, feature):
            raise IgnoredException("本群对应UniBot功能已关闭")
