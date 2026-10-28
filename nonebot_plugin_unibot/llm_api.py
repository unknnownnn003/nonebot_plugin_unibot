"""Versioned optional Agent API. Importing this module does not load llmchat."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

API_VERSION = 1
CAPABILITY_VERSION = "3.1.0"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class BestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["b", "b30"] = "b"
    version: str = Field("2026", max_length=16)


def describe_capabilities():
    return [dict(id="song.search", name="nb_unibot_song", description="中二节奏查歌、歌曲详情与歧义候选图。",
                 parameters=SearchArgs.model_json_schema(), effect="none", keywords=["中二", "是什么歌", "查歌", "歌曲", "曲目"]),
            dict(id="player.best", name="nb_unibot_best", description="查询当前发起人的原始成绩图；b国服B30+N20，b30本地日服定数B30。",
                 parameters=BestArgs.model_json_schema(), effect="none", keywords=["查分", "成绩", "b30", "b图", "rating", "最近打得"])]


async def invoke_capability(name, arguments, context):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from .services.results import outcome
    event, bot = context["event"], context["bot"]
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "无法核验当前请求者。", status="denied")
    try:
        if name == "song.search":
            args = SearchArgs.model_validate(arguments)
            from .services.song import search
            output = await search(args.query)
        elif name == "player.best":
            args = BestArgs.model_validate(arguments)
            from .services.player import best
            output = await best(str(event.user_id), args.mode, args.version)
        else:
            return outcome("CAPABILITY_DISABLED", "能力未注册。", status="unavailable")
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请按能力参数说明提供查询条件。", status="needs_input")
    output.pop("fallback_text", None)
    return output


def set_job_notifier(callback_or_none):
    global _job_notifier
    _job_notifier = callback_or_none
