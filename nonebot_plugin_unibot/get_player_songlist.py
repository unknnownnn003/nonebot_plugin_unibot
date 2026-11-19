"""Native player profile and song score commands."""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message
from nonebot.params import CommandArg

from .services.chart_progress import eligible_charts, profile_progress
from .services.image_components import player_images
from .services.job_store import async_call
from .services.progress_images import draw_player_info
from .services.results import outcome, image

get_score = on_command("个人信息", aliases={"分数", "chuinfo"}, priority=5, block=True)
song_scores_cmd = on_command("chu成绩", priority=5, block=True)


async def query_player_profile(snapshot, catalog):
    player = snapshot.player
    rows = eligible_charts(catalog, snapshot.scores) if catalog is not None else None
    official, summary, levels = profile_progress(player, rows)
    assets = await player_images(player, portrait=True)
    content = await async_call(draw_player_info, player, assets, official, summary, levels)
    return outcome(data={"player": player, "official_op": official, "master_ultima": summary,
                         "levels": levels}, artifacts=[image(content)])


@get_score.handle()
async def _(bot: Bot, event: MessageEvent):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "player.profile", {}, origin="command")
    await send_command_result(get_score, result, finish=True)


@song_scores_cmd.handle()
async def _(bot: Bot, event: MessageEvent, msg: Message = CommandArg()):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "player.song_scores", {"query": msg.extract_plain_text().strip()}, origin="command")
    await send_command_result(song_scores_cmd, result, finish=True)
