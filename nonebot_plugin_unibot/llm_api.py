"""Versioned optional Agent API. Importing this module does not load llmchat."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal
import inspect

_job_notifier = None

API_VERSION = 1
CAPABILITY_VERSION = "3.1.1"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class SongIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    song_id: str = Field(min_length=1, max_length=40)


class JobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1, max_length=64)


class BestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["b", "b30"] = "b"
    version: str = Field("2026", max_length=16)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BindArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    friend_code: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")


class ManualScoreArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)
    level_index: int = Field(3, ge=0, le=5)
    score: int = Field(ge=0, le=1010000)
    combo: str = Field("", max_length=24)


class GameStartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    difficulty: Literal["bas", "adv", "exp", "mas", "ult"] = "mas"


class GameGuessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


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
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["猜曲", "猜歌", "游戏状态"]),
            dict(id="game.hint", name="nb_unibot_game_hint", description="为当前群猜曲游戏消耗一次提示并返回原提示结果；需要确认。",
                 parameters=EmptyArgs.model_json_schema(), effect="write", keywords=["猜曲提示", "提示"]),
            dict(id="game.give_up", name="nb_unibot_game_give_up", description="结束当前群猜曲游戏并公布答案；需要确认。",
                 parameters=EmptyArgs.model_json_schema(), effect="write", keywords=["放弃猜曲", "放弃游戏"]),
            dict(id="game.start", name="nb_unibot_game_start", description="开始当前群猜曲游戏；需要确认。",
                 parameters=GameStartArgs.model_json_schema(), effect="write", keywords=["开始猜曲", "猜曲游戏"]),
            dict(id="game.guess", name="nb_unibot_game_guess", description="在当前群猜曲状态中提交当前用户答案；需要确认。",
                 parameters=GameGuessArgs.model_json_schema(), effect="write", keywords=["猜歌", "猜曲答案"]),
            dict(id="help.read", name="nb_unibot_help", description="发送原 UniBot 指令帮助图片，不包含模型管理权限。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["帮助", "chuhelp", "命令列表"]),
            dict(id="player.bind", name="nb_unibot_bind", description="绑定当前真实发起人的落雪好友码；需要用户确认后写入。",
                 parameters=BindArgs.model_json_schema(), effect="write", keywords=["绑定", "bind", "好友码"]),
            dict(id="score.submit", name="nb_unibot_score_submit", description="给当前真实发起人保存一条手动成绩；需要用户确认后写入。",
                 parameters=ManualScoreArgs.model_json_schema(), effect="write", keywords=["传分", "手动成绩", "保存成绩"]),
            dict(id="score.sync", name="nb_unibot_score_sync", description="排队同步当前真实发起人的落雪成绩；需要用户确认后提交任务。",
                 parameters=EmptyArgs.model_json_schema(), effect="write", long_task=True, keywords=["同步成绩", "lxupdate", "全量同步"]),
            dict(id="score.import_file", name="nb_unibot_score_import", description="导入当前消息附带的成绩文件；需要用户确认后写入。",
                 parameters=EmptyArgs.model_json_schema(), effect="write", keywords=["上传成绩", "chuupdate", "成绩文件"]),
            dict(id="job.list", name="nb_unibot_jobs", description="读取当前Bot/会话中自己的最近任务；不会重新提交任务。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["任务", "进度", "排队", "同步怎么样"]),
            dict(id="job.get", name="nb_unibot_job", description="按任务ID获取当前会话中自己任务的新鲜状态。",
                 parameters=JobArgs.model_json_schema(), effect="none", keywords=["任务状态", "任务详情"]),
            dict(id="job.cancel", name="nb_unibot_cancel_job", description="请求取消当前会话中自己的任务；提交中只能请求取消，不能提前宣称已取消。",
                 parameters=JobArgs.model_json_schema(), effect="write", keywords=["取消任务", "取消同步", "停止同步"])]


async def prepare_capability(name, arguments, context):
    """Resolve write targets before the user sees a concrete program preview."""
    import asyncio
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from .services.results import outcome
    event, bot = context["event"], context["bot"]
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(event.self_id) != str(bot.self_id):
        return outcome("PERMISSION_DENIED", "无法核验当前请求者。", status="denied")
    models = {"player.bind": BindArgs, "score.submit": ManualScoreArgs, "score.sync": EmptyArgs,
              "score.import_file": EmptyArgs, "game.start": GameStartArgs, "game.guess": GameGuessArgs,
              "game.hint": EmptyArgs, "game.give_up": EmptyArgs, "job.cancel": JobArgs}
    try:
        normalized = models[name].model_validate(arguments).model_dump() if name in models else dict(arguments)
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请核对操作参数。", status="needs_input")
    constraints = {}
    if name == "score.submit":
        from .song import find_song_matches, pick_song_match
        song, ambiguous = pick_song_match(await asyncio.to_thread(find_song_matches, normalized["query"], 8))
        if ambiguous:
            return outcome("AMBIGUOUS_TARGET", "请先用明确歌曲ID指定传分目标。", status="needs_input",
                           data={"candidates": [{"id": m["song"].get("id"), "title": m["song"].get("title")} for m in ambiguous[:8]]})
        if not song:
            return outcome("NOT_FOUND", "未找到匹配的曲目。", status="needs_input")
        normalized["query"] = str(song["id"])
        constraints["song_title"] = str(song.get("title", ""))
    if name in {"game.guess", "game.hint", "game.give_up"}:
        if not isinstance(event, GroupMessageEvent):
            return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
        from .services.game import binding
        token = await binding(event.group_id)
        if token is None:
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。", status="needs_input")
        constraints["game_round_id"] = token
    return dict(status="ok", code="OK", arguments=normalized, constraints=constraints)


async def invoke_capability(name, arguments, context):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from .services.results import outcome
    event, bot = context["event"], context["bot"]
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "无法核验当前请求者。", status="denied")
    if name in {"player.bind", "score.submit", "score.sync", "score.import_file", "game.hint", "game.give_up", "game.start", "game.guess", "job.cancel"} and not context.get("confirmed", False):
        return outcome("AUTHORIZATION_REQUIRED", "需要当前用户核对操作预览并确认。", status="denied")
    if name in {"game.guess", "game.hint", "game.give_up"} and not context.get("constraints", {}).get("game_round_id"):
        return outcome("STATE_CHANGED", "操作没有绑定有效猜曲局，请重新预览。", status="needs_input")
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
        elif name == "game.hint":
            if not isinstance(event, GroupMessageEvent):
                return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
            EmptyArgs.model_validate(arguments)
            from .services.game import hint
            output = await hint(event.group_id, expected_round=context["constraints"]["game_round_id"])
        elif name == "game.give_up":
            if not isinstance(event, GroupMessageEvent):
                return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
            EmptyArgs.model_validate(arguments)
            from .services.game import give_up
            output = await give_up(event.group_id, expected_round=context["constraints"]["game_round_id"])
        elif name == "game.start":
            if not isinstance(event, GroupMessageEvent):
                return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
            args = GameStartArgs.model_validate(arguments)
            from .services.game import start
            output = await start(event.group_id, args.difficulty, bot_id=str(bot.self_id))
        elif name == "game.guess":
            if not isinstance(event, GroupMessageEvent):
                return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
            args = GameGuessArgs.model_validate(arguments)
            from .services.game import guess
            output = await guess(event.group_id, args.query, str(event.user_id),
                                 guesser_name=event.sender.card or event.sender.nickname or str(event.user_id),
                                 expected_round=context["constraints"]["game_round_id"])
        elif name == "help.read":
            EmptyArgs.model_validate(arguments)
            from .services.help import read
            output = await read()
        elif name == "player.bind":
            args = BindArgs.model_validate(arguments)
            from .services.scores import bind
            output = await bind(str(event.user_id), args.friend_code)
        elif name == "score.submit":
            args = ManualScoreArgs.model_validate(arguments)
            from .services.scores import submit_manual
            output = await submit_manual(str(event.user_id), args.query, args.level_index, args.score, args.combo)
        elif name == "score.sync":
            EmptyArgs.model_validate(arguments)
            from .services.sync import submit
            output = await submit(bot, event, str(event.user_id))
        elif name in {"job.list", "job.get", "job.cancel"}:
            args = EmptyArgs.model_validate(arguments) if name == "job.list" else JobArgs.model_validate(arguments)
            from .services.sync import query
            output = await query(bot, event, name.split(".")[1], getattr(args, "job_id", None))
        elif name == "score.import_file":
            EmptyArgs.model_validate(arguments)
            from .services.uploads import import_file
            output = await import_file(bot, event, str(event.user_id))
        else:
            return outcome("CAPABILITY_DISABLED", "能力未注册。", status="unavailable")
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请按能力参数说明提供查询条件。", status="needs_input")
    output.pop("fallback_text", None)
    return output


def set_job_notifier(callback_or_none):
    global _job_notifier
    _job_notifier = callback_or_none


async def notify_job(job):
    callback = _job_notifier
    if callback is None:
        return False
    value = callback(job)
    if inspect.isawaitable(value):
        value = await value
    return value


async def claim_job_expression(job_id):
    from .services.sync import _store
    return await _store.claim_expression(job_id) if _store is not None else False


async def save_job_expression(job_id, text):
    from .services.sync import _store
    if _store is not None:
        await _store.save_expression(job_id, text)
