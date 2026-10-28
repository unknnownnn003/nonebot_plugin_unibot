"""Versioned optional Agent API. Importing this module does not load llmchat."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

API_VERSION = 1
CAPABILITY_VERSION = "3.1.1"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class SongIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    song_id: str = Field(min_length=1, max_length=40)


class BestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["b", "b30"] = "b"
    version: str = Field("2026", max_length=16)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RecommendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filter: str = Field("", max_length=20, description="原推什么参数：等级如13+，或aj/ajc/fc；空为全部")


class StoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class StoryCategoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str = Field("", max_length=80)


def describe_capabilities():
    return [dict(id="song.search", name="nb_unibot_song", description="中二节奏查歌、歌曲详情与歧义候选图。",
                 parameters=SearchArgs.model_json_schema(), effect="none", keywords=["中二", "是什么歌", "查歌", "歌曲", "曲目"]),
            dict(id="song.detail", name="nb_unibot_song_detail", description="按明确歌曲 ID 查询中二节奏详情并发送原曲目图片。",
                 parameters=SongIdArgs.model_json_schema(), effect="none", keywords=["歌曲详情", "曲目详情", "歌曲ID"]),
            dict(id="player.best", name="nb_unibot_best", description="查询当前发起人的原始成绩图；b国服B30+N20，b30本地日服定数B30。",
                 parameters=BestArgs.model_json_schema(), effect="none", keywords=["查分", "成绩", "b30", "b图", "rating", "最近打得"]),
            dict(id="player.profile", name="nb_unibot_profile", description="当前发起人的原个人信息图，保留落雪与Rin回退。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["个人信息", "玩家信息", "个人资料"]),
            dict(id="player.progress", name="nb_unibot_progress", description="当前发起人的原国服OP进度图（chulist）；query沿用难度/定数/版本/分类条件。",
                 parameters=SearchArgs.model_json_schema(), effect="none", keywords=["进度", "chulist", "完成率"]),
            dict(id="player.recommend", name="nb_unibot_recommend", description="原推什么算法推荐当前发起人可推的谱面；保留原图。",
                 parameters=RecommendArgs.model_json_schema(), effect="none", keywords=["推荐", "推什么", "推分"]),
            dict(id="song.aliases", name="nb_unibot_aliases", description="查询中二歌曲原本地别名；歧义时返回候选。",
                 parameters=SearchArgs.model_json_schema(), effect="none", keywords=["别名", "外号"]),
            dict(id="story.search", name="nb_unibot_story_search", description="按角色名查询本地剧情并发送原剧情图片。",
                 parameters=StoryArgs.model_json_schema(), effect="none", keywords=["剧情", "角色", "故事"]),
            dict(id="story.list", name="nb_unibot_story_list", description="查看本地剧情分类或分类下的角色名。",
                 parameters=StoryCategoryArgs.model_json_schema(), effect="none", keywords=["剧情分类", "剧情列表", "角色列表"]),
            dict(id="story.random", name="nb_unibot_story_random", description="随机选择一篇本地剧情并发送原剧情图片。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["随机剧情", "随便一篇故事"]),
            dict(id="game.status", name="nb_unibot_game_status", description="查询当前群猜曲游戏状态，不泄露答案或图片本地路径。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["猜曲", "猜歌", "游戏状态"])]


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
        elif name == "song.detail":
            args = SongIdArgs.model_validate(arguments)
            from .services.song import detail
            output = await detail(args.song_id)
        elif name == "song.aliases":
            args = SearchArgs.model_validate(arguments)
            from .services.song import aliases
            output = await aliases(args.query)
        elif name == "player.best":
            args = BestArgs.model_validate(arguments)
            from .services.player import best
            output = await best(str(event.user_id), args.mode, args.version)
        elif name == "player.profile":
            EmptyArgs.model_validate(arguments)
            from .services.player import profile
            output = await profile(str(event.user_id))
        elif name == "player.progress":
            args = SearchArgs.model_validate(arguments)
            from .services.player import progress
            output = await progress(str(event.user_id), args.query)
        elif name == "player.recommend":
            args = RecommendArgs.model_validate(arguments)
            from .services.player import recommend
            output = await recommend(str(event.user_id), args.filter)
        elif name == "story.search":
            args = StoryArgs.model_validate(arguments)
            from .services.story import search
            output = await search(bot, event, args.query)
        elif name == "story.list":
            args = StoryCategoryArgs.model_validate(arguments)
            from .services.story import categories
            output = await categories(args.category)
        elif name == "story.random":
            EmptyArgs.model_validate(arguments)
            from .services.story import random_story
            output = await random_story(bot, event)
        elif name == "game.status":
            if not isinstance(event, GroupMessageEvent):
                return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
            EmptyArgs.model_validate(arguments)
            from .services.game import status
            output = await status(event.group_id)
        else:
            return outcome("CAPABILITY_DISABLED", "能力未注册。", status="unavailable")
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请按能力参数说明提供查询条件。", status="needs_input")
    output.pop("fallback_text", None)
    return output


def set_job_notifier(callback_or_none):
    global _job_notifier
    _job_notifier = callback_or_none
