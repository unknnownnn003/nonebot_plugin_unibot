"""Native chulist command and exact catalog filters."""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from .services.chart_progress import eligible_charts, master_ultima, summarize
from .services.image_components import player_images
from .services.job_store import async_render
from .services.progress_images import draw_chulist_image, paginate
from .services.resources import prefetch_jackets
from .services.results import outcome, image

chulist_cmd = on_command("chulist", priority=5, block=True)

GENRES = {
    "其他游戏": ("variety", "其他", "其它"),
    "东方project": ("touhou", "东方", "车万", "touhou project"),
    "原创": ("original", "ori", "chunithm original"),
    "音击舞萌": ("gekimai", "音击", "舞萌", "gekishu & maimai"),
    "流行 & 动漫": ("pops_anime", "流行", "动漫", "pops", "anime",
                  "pops & anime", "pops&anime", "pops&animes", "流行动漫", "流行&动漫"),
    "niconico": ("nico",),
}
GENRE_ALIASES = {alias: name for name, aliases in GENRES.items() for alias in (name, *aliases)}
USAGE = "例如 /chulist 14+ sun 车万、/chulist 14.9 或 /chulist 14+ --nocat --page 2。"


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def version_aliases(title):
    title = normalize(title)
    short = title.removeprefix("chunithm ")
    if title == "chunithm":
        short = "origin"
    elif title == "chunithm plus":
        short = "origin plus"
    aliases = {title, short, short.replace(" ", "")}
    if short.endswith(" plus"):
        aliases.add(short.removesuffix(" plus") + "+")
    if short == "paradise lost":
        aliases.add("paradiser lost")
    return aliases


@dataclass(frozen=True)
class Filters:
    label: str
    page: int
    grouped: bool
    level: str | None = None
    constant: Decimal | None = None
    version: frozenset[int] | None = None
    genre: str | None = None

    def matches(self, row):
        version = row.chart.attributes.get("version", row.song.attributes.get("version"))
        genre = normalize(row.song.attributes.get("genre") or "")
        return (
            (self.level is None or row.level == self.level)
            and (self.constant is None or row.constant == self.constant)
            and (self.version is None or version in self.version)
            and (self.genre is None or GENRE_ALIASES.get(genre, genre) == self.genre)
        )


def parse_filters(query, catalog):
    versions = {}
    for row in catalog.metadata.get("versions", []):
        for alias in version_aliases(row["title"]):
            versions.setdefault(alias, set()).add(row["version"])
    genres = dict(GENRE_ALIASES)
    for row in catalog.metadata.get("genres", []):
        name = normalize(row["genre"])
        genres.setdefault(name, name)
    phrases = sorted(((alias.split(), alias) for alias in versions.keys() | genres.keys()),
                     key=lambda phrase: (-len(phrase[0]), phrase[1]))
    tokens = re.sub(r"[,|]", " ", normalize(query)).split()
    conditions, options, label = {}, set(), []
    page = 1
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in {"--page", "--nocat"}:
            if token in options:
                raise ValueError(f"重复选项 {token}。")
            options.add(token)
            if token == "--page":
                if index == len(tokens) or not re.fullmatch(r"[0-9]{1,8}", tokens[index]) or int(tokens[index]) == 0:
                    raise ValueError("请在 --page 后填写正整数页码。")
                page = int(tokens[index])
                index += 1
            continue
        if re.fullmatch(r"[0-9]{1,2}\+?", token):
            kind, value = "level", token
        elif re.fullmatch(r"[0-9]+\.[0-9]+", token):
            kind, value = "constant", Decimal(token)
        else:
            start = index - 1
            match = next(((parts, alias) for parts, alias in phrases
                          if parts == tokens[start:start + len(parts)]), None)
            if match is None:
                raise ValueError(f"无法识别的筛选参数「{token[:32]}」。")
            parts, alias = match
            if alias in versions and alias in genres:
                raise ValueError(f"筛选参数「{alias}」同时对应版本和分类。")
            kind, value = ("version", frozenset(versions[alias])) if alias in versions else ("genre", genres[alias])
            token = alias
            index = start + len(parts)
        if kind in conditions and conditions[kind] != value:
            raise ValueError("筛选条件冲突，同一类型只能指定一个值。")
        if kind not in conditions:
            conditions[kind] = value
            label.append(token)
    if not conditions:
        raise ValueError("请输入显示等级、定数、版本或分类。")
    return Filters("  ·  ".join(label), page, "--nocat" not in options, **conditions)


def matching_charts(rows, filters):
    selected = [row for row in master_ultima(rows) if filters.matches(row)]
    return sorted(selected, key=lambda row: (
        -row.constant if filters.grouped else 0,
        row.record is None, -(row.record or {}).get("score", 0), row.song.id, row.chart.difficulty,
    ))


def expert_leaders(rows, selected):
    # EXP↑ denotes a strictly greater EXP OP than every other eligible chart
    # of the song. One matching card carries the badge across the entire result.
    by_song = {}
    for row in rows:
        if row.record is not None:
            by_song.setdefault(row.song.id, []).append(row)
    carriers = {}
    for row in selected:
        sid, diff = row.song.id, row.chart.difficulty
        carriers[sid] = min(carriers.get(sid, diff), diff)
    marks = set()
    for sid, diff in carriers.items():
        played = by_song.get(sid, [])
        expert = next((row.op for row in played if row.chart.difficulty == 2), 0)
        if expert > 0 and all(expert > row.op for row in played if row.chart.difficulty != 2):
            marks.add((sid, diff))
    return marks


async def query_progress(snapshot, query_text, catalog):
    try:
        filters = parse_filters(query_text, catalog)
    except ValueError as exc:
        return outcome("INVALID_ARGUMENT", f"{exc} {USAGE}", status="needs_input")
    all_rows = eligible_charts(catalog, snapshot.scores)
    selected = matching_charts(all_rows, filters)
    if not selected:
        return outcome("NOT_FOUND", "未找到同时符合所有筛选条件的已配信 MASTER / ULTIMA 谱面。", status="needs_input")

    stats = summarize(selected)
    pages = paginate(selected, grouped=filters.grouped)
    if filters.page > len(pages):
        return outcome("INVALID_ARGUMENT", f"匹配 {len(selected)} 张谱面，页码范围为 1–{len(pages)}。",
                       status="needs_input")
    groups = pages[filters.page - 1]
    visible = dict.fromkeys(row.chart.jacket_id for group in groups for row in group.rows)
    assets, _ = await asyncio.gather(player_images(snapshot.player, portrait=True), prefetch_jackets(visible))
    try:
        content = await async_render(draw_chulist_image, snapshot.player, assets, filters.label, stats, groups,
                                     filters.page, len(pages), expert_leaders(all_rows, selected))
    except (OSError, ValueError):
        logger.error("OAuth 进度图生成失败，未记录玩家数据。")
        return outcome("RENDER_FAILED", "生成进度图片失败。", status="unavailable")
    return outcome(
        hint="进度统计覆盖所有符合条件的已配信 MASTER / ULTIMA，每张谱面独立计入，未游玩按零计。"
             "筛选 OP 逐谱面累加，官方总 OP 按曲取最高。",
        data=dict(stats, op_aggregation="filtered_chart_sum", constant_source="lx",
                  page=filters.page, page_count=len(pages), page_chart_count=sum(len(g.rows) for g in groups)),
        artifacts=[image(content)],
    )


@chulist_cmd.handle()
async def _(bot: Bot, event: MessageEvent, msg: Message = CommandArg()):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "player.progress", {"query": msg.extract_plain_text().strip()}, origin="command")
    await send_command_result(chulist_cmd, result, finish=True)
