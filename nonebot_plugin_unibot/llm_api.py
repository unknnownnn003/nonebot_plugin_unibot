"""Versioned optional Agent API. Importing this module does not load llmchat."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal
import inspect
from .services.permissions import native_superuser
from .services import group_policy

_job_notifier = None

API_VERSION = 1
CAPABILITY_VERSION = "4.1.0"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class SongIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    song_id: str = Field(min_length=1, max_length=40)


class JobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1, max_length=64)


class AliasArgs(SongIdArgs):
    alias: str = Field(min_length=1, max_length=120)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MaintenanceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resource: Literal["songlist", "jackets", "collections", "stories"]
    full_refresh: bool = False


class GameStartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    difficulty: Literal["bas", "adv", "exp", "mas", "ult"] = "mas"


class GameGuessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class RecommendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filter: str = Field("", max_length=20, description="原推什么参数：等级如13+，或aj/ajc/fc；空为全部")


class SongScoreArgs(SearchArgs):
    level_index: int | None = Field(None, ge=0, le=5)


class StoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=120)


class StoryCategoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str = Field("", max_length=80)


def _describe_capabilities():
    return [dict(id="maintenance.refresh", name="nb_unibot_maintenance", description="排队更新落雪国服曲库/曲绘/收藏品/剧情资源；仅SUPERUSER，全局写入需确认。",
                 parameters=MaintenanceArgs.model_json_schema(), effect="write", long_task=True,
                 keywords=["更新曲库", "更新songlist", "更新曲绘", "更新收藏品", "更新剧情", "维护资源"]),
            dict(id="song.search", name="nb_unibot_song", description="中二节奏查歌、歌曲详情与歧义候选图。",
                 parameters=SearchArgs.model_json_schema(), effect="none", long_task=True, keywords=["中二", "是什么歌", "查歌", "歌曲", "曲目"]),
            dict(id="song.detail", name="nb_unibot_song_detail", description="按明确歌曲 ID 查询中二节奏详情并发送原曲目图片。",
                 parameters=SongIdArgs.model_json_schema(), effect="none", long_task=True, keywords=["歌曲详情", "曲目详情", "歌曲ID"]),
            dict(id="player.best", name="nb_unibot_best", description="查询本人全版本普通谱面Rating前30的B30单榜均值，不是个人总Rating。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", long_task=True, keywords=["查分", "成绩", "b30", "rating", "最近打得"]),
            dict(id="player.b50", name="nb_unibot_b50", description="查询本人国服B50：旧版本BEST30与当前版本NEW20，按歌曲版本分区，缺位计零；共用OAuth快照。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", long_task=True, keywords=["b50", "best30", "new20", "国服rating"]),
            dict(id="player.profile", name="nb_unibot_profile", description="当前真实发起人的完整落雪 OAuth 个人资料图。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", long_task=True, keywords=["个人信息", "玩家信息", "个人资料"]),
            dict(id="player.song_scores", name="nb_unibot_song_scores", description="查询本人 OAuth 单曲全部已记录难度成绩，支持别名和可选难度；关联 WORLD'S END 保留真实ID，WE不计普通Rating/OP。",
                 parameters=SongScoreArgs.model_json_schema(), effect="none", long_task=True, keywords=["单曲成绩", "chu成绩", "这首成绩"]),
            dict(id="player.progress", name="nb_unibot_progress", description="本人OAuth MASTER/ULTIMA筛选进度；OP按筛选谱面逐张合计，非游戏内总OP，WE不参与；query为难度/定数/版本/分类条件。",
                 parameters=SearchArgs.model_json_schema(), effect="none", long_task=True, keywords=["进度", "chulist", "完成率"]),
            dict(id="player.recommend", name="nb_unibot_recommend", description="原推什么算法推荐当前发起人可推的谱面；保留原图。",
                 parameters=RecommendArgs.model_json_schema(), effect="none", long_task=True, keywords=["推荐", "推什么", "推分"]),
            dict(id="song.aliases", name="nb_unibot_aliases", description="查询中二歌曲原本地别名；歧义时返回候选。",
                 parameters=SearchArgs.model_json_schema(), effect="none", keywords=["别名", "外号"]),
            dict(id="song.alias_add", name="nb_unibot_alias_add", description="给明确歌曲ID添加本地别名；沿用普通用户权限，需要确认。",
                 parameters=AliasArgs.model_json_schema(), effect="write", keywords=["添加别名", "加别名", "外号"]),
            dict(id="song.alias_delete", name="nb_unibot_alias_delete", description="删除明确歌曲ID的本地别名；仅SUPERUSER，需要确认。",
                 parameters=AliasArgs.model_json_schema(), effect="write", keywords=["删除别名", "删别名"]),
            dict(id="story.search", name="nb_unibot_story_search", description="按角色名查询本地剧情，排队生成并发送原剧情图片；进度请查询原任务。",
                 parameters=StoryArgs.model_json_schema(), effect="none", long_task=True, keywords=["剧情", "角色", "故事"]),
            dict(id="story.list", name="nb_unibot_story_list", description="查看本地剧情分类或分类下的角色名。",
                 parameters=StoryCategoryArgs.model_json_schema(), effect="none", keywords=["剧情分类", "剧情列表", "角色列表"]),
            dict(id="story.random", name="nb_unibot_story_random", description="为本次消息固定随机剧情，排队生成原图；重复查询不会重新抽选。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", long_task=True, keywords=["随机剧情", "随便一篇故事"]),
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
                 parameters=EmptyArgs.model_json_schema(), effect="none", long_task=True, keywords=["帮助", "chuhelp", "命令列表"]),


            dict(id="job.list", name="nb_unibot_jobs", description="读取当前Bot/会话中自己的最近任务；不会重新提交任务。",
                 parameters=EmptyArgs.model_json_schema(), effect="none", keywords=["任务", "进度", "排队", "查询结果"]),
            dict(id="job.get", name="nb_unibot_job", description="按任务ID获取当前会话中自己任务的新鲜状态。",
                 parameters=JobArgs.model_json_schema(), effect="none", keywords=["任务状态", "任务详情"]),
            dict(id="job.cancel", name="nb_unibot_cancel_job", description="请求取消当前会话中自己的任务；提交中只能请求取消，不能提前宣称已取消。",
                 parameters=JobArgs.model_json_schema(), effect="write", keywords=["取消任务", "取消查询", "停止查询"]),
            dict(id="job.replay", name="nb_unibot_replay_job", description="提交当前会话本人缓存媒体的补发任务；可查询/取消，不重新生成，需要确认。",
                 parameters=JobArgs.model_json_schema(), effect="platform_write", long_task=True, keywords=["补发中二任务", "补发剧情", "剧情没收到", "重发剧情"])]


def describe_capabilities():
    from .services.native_catalog import NATIVE_CATALOG
    import copy
    return [dict(spec, **copy.deepcopy(NATIVE_CATALOG[spec["id"]])) for spec in _describe_capabilities()]


@group_policy.checked
async def prepare_capability(name, arguments, context):
    """Resolve write targets before the user sees a concrete program preview."""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from .services.results import outcome
    event, bot = context["event"], context["bot"]
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(event.self_id) != str(bot.self_id):
        return outcome("PERMISSION_DENIED", "无法核验当前请求者。", status="denied")
    from .services.player_data import RETIRED, retired
    if name.startswith("oauth."):
        return outcome("NATIVE_ONLY", "OAuth 授权与解绑请由本人使用原生命令，模型不能代理。", status="denied")
    if name == "maintenance.import_chunirec" or (name == "maintenance.refresh" and arguments.get("resource") in {"chunirec", "chunirec_file"}):
        from .services.maintenance import retired_source
        return retired_source()
    from .services.command_receipts import source_owned
    if await source_owned(bot, event):
        return outcome("ALREADY_HANDLED", "原命令已接管这条消息，不再创建重复操作预览。", status="unknown")
    if name in RETIRED:
        return retired()
    models = {"game.start": GameStartArgs, "game.guess": GameGuessArgs,
              "game.hint": EmptyArgs, "game.give_up": EmptyArgs, "job.cancel": JobArgs, "job.replay": JobArgs}
    try:
        normalized = models[name].model_validate(arguments).model_dump() if name in models else dict(arguments)
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请核对操作参数。", status="needs_input")
    constraints = {}
    if name == "job.replay":
        from .services.sync import query
        checked = await query(bot, event, "get", normalized["job_id"])
        if checked["status"] != "ok":
            return checked
        constraints["cached_only"] = True
    if name == "maintenance.refresh":
        try:
            normalized = MaintenanceArgs.model_validate(arguments).model_dump()
        except ValidationError:
            return outcome("INVALID_ARGUMENT", "请指定维护资源。", status="needs_input")
        if not native_superuser(bot, event):
            return outcome("PERMISSION_DENIED", "资源维护仅SUPERUSER可用。", status="denied")
        if normalized["full_refresh"] and normalized["resource"] != "jackets":
            return outcome("INVALID_ARGUMENT", "强制刷新仅适用于曲绘。", status="needs_input")
        constraints["scope"] = "global"
    if name in {"song.alias_add", "song.alias_delete"}:
        try:
            normalized = AliasArgs.model_validate(arguments).model_dump()
        except ValidationError:
            return outcome("INVALID_ARGUMENT", "请提供歌曲ID及别名。", status="needs_input")
        if name == "song.alias_delete" and not native_superuser(bot, event):
            return outcome("PERMISSION_DENIED", "只有SUPERUSER可以删除歌曲别名。", status="denied")
        from .services.song import aliases
        target = await aliases(normalized["song_id"])
        if target["code"] != "OK" or str(target.get("data", {}).get("id")) != normalized["song_id"]:
            return outcome("NOT_FOUND", "请先查歌选择明确歌曲ID。", status="needs_input")
        normalized["alias"] = normalized["alias"].strip()
        if not normalized["alias"]:
            return outcome("INVALID_ARGUMENT", "别名不能为空。", status="needs_input")
        constraints["song_title"] = target["data"].get("title", "")
    if name in {"game.guess", "game.hint", "game.give_up"}:
        if not isinstance(event, GroupMessageEvent):
            return outcome("GROUP_ONLY", "猜曲游戏只在群聊中可用。", status="denied")
        from .services.game import binding
        token = await binding(event.group_id)
        if token is None:
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。", status="needs_input")
        constraints["game_round_id"] = token
    return dict(status="ok", code="OK", arguments=normalized, constraints=constraints)


@group_policy.checked
async def invoke_capability(name, arguments, context):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from .services.results import outcome
    event, bot = context["event"], context["bot"]
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(bot.self_id) != str(event.self_id):
        return outcome("PERMISSION_DENIED", "无法核验当前请求者。", status="denied")
    from .services.player_data import RETIRED, retired
    if name.startswith("oauth."):
        return outcome("NATIVE_ONLY", "OAuth 授权与解绑请由本人使用原生命令，模型不能代理。", status="denied")
    if name == "maintenance.import_chunirec" or (name == "maintenance.refresh" and arguments.get("resource") in {"chunirec", "chunirec_file"}):
        from .services.maintenance import retired_source
        return retired_source()
    from .services.command_receipts import source_owned
    if await source_owned(bot, event):
        return outcome("ALREADY_HANDLED", "这条消息已由原命令接管，不能再次执行业务；请查询原任务或记录。", status="unknown")
    if name in RETIRED:
        return retired()
    if name in {"maintenance.refresh", "song.alias_add", "song.alias_delete", "game.hint", "game.give_up", "game.start", "game.guess", "job.cancel", "job.replay"} and not context.get("confirmed", False):
        return outcome("AUTHORIZATION_REQUIRED", "需要当前用户核对操作预览并确认。", status="denied")
    if name in {"game.guess", "game.hint", "game.give_up"} and not context.get("constraints", {}).get("game_round_id"):
        return outcome("STATE_CHANGED", "操作没有绑定有效猜曲局，请重新预览。", status="needs_input")
    try:
        if name == "maintenance.refresh":
            args = MaintenanceArgs.model_validate(arguments)
            from .services.maintenance import submit
            output = await submit(bot, event, args.resource, args.full_refresh)
        elif name in {"song.alias_add", "song.alias_delete"}:
            args = AliasArgs.model_validate(arguments)
            if name == "song.alias_delete" and not native_superuser(bot, event):
                return outcome("PERMISSION_DENIED", "只有SUPERUSER可以删除歌曲别名。", status="denied")
            from .services.song import change_alias
            output = await change_alias(args.song_id, args.alias, delete=name == "song.alias_delete")
        elif name == "song.search":
            args = SearchArgs.model_validate(arguments)
            from .services.media_jobs import submit
            output = await submit(bot, event, name, args.model_dump())
        elif name == "song.detail":
            args = SongIdArgs.model_validate(arguments)
            from .services.media_jobs import submit
            output = await submit(bot, event, name, args.model_dump())
        elif name == "song.aliases":
            args = SearchArgs.model_validate(arguments)
            from .services.song import aliases
            output = await aliases(args.query)
        elif name in {"player.b50", "player.best", "player.profile", "player.progress", "player.recommend", "player.song_scores"}:
            model = {"player.b50": EmptyArgs, "player.best": EmptyArgs, "player.profile": EmptyArgs, "player.progress": SearchArgs,
                     "player.recommend": RecommendArgs, "player.song_scores": SongScoreArgs}[name]
            args = model.model_validate(arguments)
            from .services.media_jobs import submit
            output = await submit(bot, event, name, args.model_dump())
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
            output = await start(event.group_id, args.difficulty, bot_id=str(bot.self_id), delivery_context=(bot, event, "agent"))
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
            from .services.media_jobs import submit
            output = await submit(bot, event, name, {})
        elif name == "job.replay":
            args = JobArgs.model_validate(arguments)
            from .services.sync import replay
            output = await replay(bot, event, args.job_id, origin="agent")
        elif name in {"job.list", "job.get", "job.cancel"}:
            args = EmptyArgs.model_validate(arguments) if name == "job.list" else JobArgs.model_validate(arguments)
            from .services.sync import query
            output = await query(bot, event, name.split(".")[1], getattr(args, "job_id", None))
        else:
            return outcome("CAPABILITY_DISABLED", "能力未注册。", status="unavailable")
    except ValidationError:
        return outcome("INVALID_ARGUMENT", "请按能力参数说明提供查询条件。", status="needs_input")
    if name in {"game.start", "game.guess", "game.hint", "game.give_up"} and output["status"] == "ok" and output.get("artifacts"):
        from .services.inline_media import deliver_artifacts
        media = await deliver_artifacts(bot, event, output["artifacts"], name, output.get("data"))
        output["artifacts"] = []
        output.setdefault("data", {}).update(media_job_id=media.get("data", {}).get("job_id"),
            artifact_delivery=media.get("data", {}).get("artifact_delivery", "unknown"))
        if output["data"]["artifact_delivery"] != "sent":
            output.update(status="partial", code="MEDIA_DELIVERY_INCOMPLETE",
                          public_hint="猜曲操作已生效，媒体未确认送达；请补发缓存，不要再次消耗提示或记分。")
    output.pop("fallback_text", None)
    if name in {"song.alias_add", "song.alias_delete"} and output["status"] == "ok":
        output["effect"] = "local_write"
    return output


def set_job_notifier(callback_or_none):
    global _job_notifier
    _job_notifier = callback_or_none


async def get_job_materials(job_id, context):
    from .services.sync import query
    from .services.presentation_store import scope
    bot, event = context["bot"], context["event"]
    checked = await query(bot, event, "get", job_id)
    if checked.get("status") != "ok":
        return []
    from .services.sync import _presentations, _store, private_job_allowed
    if not await private_job_allowed(await _store.get(job_id)):
        return []
    images = await _presentations.model_images(job_id, scope(bot, event)) if _presentations else []
    return images if await private_job_allowed(await _store.get(job_id)) else []


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


async def describe_context(context):
    """Current local visibility; invocation still rechecks live authority."""
    from .services.catalog import describe
    return await describe(describe_capabilities(), context)
