"""4A profile and chulist layouts rendered from the same chart progress."""
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from itertools import groupby

from PIL import Image, ImageColor, ImageDraw, ImageOps

from . import resources
from .chart_progress import ChartProgress, summarize
from .image_components import RESAMPLE, brand, contain, cover, label, playerbar, png, rounded, score_digits, wrap
from .job_store import check_render_cancelled

INK, MUTED = "#182d43", "#3f5568"
BG, PAPER, LINE, TEAL = "#eaf4f4", "#ffffff", "#c7dce0", "#087970"
BADGES = {
    "Clear": ("#c4cbd0", "#33404a"),
    "SSS": ("#e4b24e", "#694415"),
    "SSS+": ("#fff1be", "#593305"),
    "FC": ("#83d3de", "#164653"),
    "AJ": ("#f5c052", "#62400d"),
    "AJC": ("#ead9f5", "#382647"),
}
ACHIEVEMENTS = (("FC", "fc"), ("AJ", "aj"), ("AJC", "ajc"))
RANK_BG = {"SSS": ("#f2d18a", "#c39743"), "SSS+": ("#fff2c9", "#d9bb68")}
DIFFICULTY = {3: ("MAS", "#863dc0"), 4: ("ULT", "#292439")}
LOW_RANKS = ((800000, "BBB"), (700000, "BB"), (600000, "B"), (500000, "C"), (0, "D"))

WIDTH, COLS = 1440, 10
CARD_WIDTH, CARD_GAP, STRIDE = 132, 8, 207
HEADER, GROUP_HEIGHT, FOOTER = 430, 139, 56
# The 190-card 4A image fits at full scale. Also bound pixel count when
# many small constant groups add more whitespace and section headers.
PAGE_CARDS, MAX_HEIGHT = 200, 6500


@dataclass(frozen=True)
class PageGroup:
    constant: Decimal | None
    rows: tuple[ChartProgress, ...]
    stats: dict


@lru_cache(maxsize=512)
def _title(value):
    return wrap(value, 18, CARD_WIDTH - 14)


def _stride(rows):
    return STRIDE + 22 * (max(len(_title(row.song.attributes["title"])) for row in rows) - 1)


def paginate(rows, *, grouped):
    """Pack consecutive rows while bounding both card count and canvas height."""
    pages, groups = [], []
    height, count = HEADER + FOOTER, 0
    for constant, bucket in groupby(rows, key=lambda row: row.constant if grouped else None):
        bucket = list(bucket)
        stats = summarize(bucket)
        offset = 0
        while offset < len(bucket):
            take, cards_height = 0, 0
            while offset + take < len(bucket) and count + take < PAGE_CARDS:
                start = offset + take
                batch = bucket[start:start + min(COLS, PAGE_CARDS - count - take)]
                stride = _stride(batch)
                if height + GROUP_HEIGHT + cards_height + stride > MAX_HEIGHT and (groups or take):
                    break
                take += len(batch)
                cards_height += stride
            if take == 0:
                pages.append(groups)
                groups, height, count = [], HEADER + FOOTER, 0
                continue
            groups.append(PageGroup(constant, tuple(bucket[offset:offset + take]), stats))
            height += GROUP_HEIGHT + cards_height
            count += take
            offset += take
    if groups:
        pages.append(groups)
    return pages


def fmt(value):
    return "—" if value is None else f"{value:,.2f}"


def percent(value):
    return "—" if value is None else f"{value:.2f}%"


def op_pair(actual, maximum):
    return f"{fmt(actual)} / {fmt(maximum)}  (-{fmt(max(0, maximum - actual))})"


@lru_cache(maxsize=32)
def rainbow(width, height):
    stops = [ImageColor.getrgb(color) for color in
             ("#faaec6", "#ffd392", "#f3e89d", "#a9e8cd", "#b8d9fa", "#d9bbed")]
    tile = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(tile)
    for x in range(width):
        position = x * (len(stops) - 1) / max(1, width - 1)
        index = min(int(position), len(stops) - 2)
        offset = position - index
        draw.line((x, 0, x, height), fill=tuple(round(a + (b - a) * offset)
                  for a, b in zip(stops[index], stops[index + 1])))
    return tile


def pill(canvas, box, key, value=None, size=20):
    left, top, right, bottom = box
    bg, ink = BADGES[key]
    radius = (bottom - top) // 2
    if key == "AJC":
        mask = Image.new("L", (right - left, bottom - top))
        rounded(mask, (0, 0, mask.width - 1, mask.height - 1), radius, 255)
        canvas.paste(rainbow(*mask.size), (left, top), mask)
    else:
        rounded(canvas, box, radius, bg)
    label(canvas, (left + 4, top, right - 4, bottom), key if value is None else f"{key} {value}",
          size, ink, "center", fit=True)


def achievement_track(canvas, box, stats):
    left, top, right, bottom = box
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((left, top, right - 1, bottom - 1), fill="#e4e9ec", outline="#a7bbbf")
    count = stats["chart_count"]
    if not count:
        return
    fc, aj, ajc = (stats[key] for _, key in ACHIEVEMENTS)
    start, total = left + 1, 0
    for amount, color in ((ajc, None), (aj - ajc, BADGES["AJ"][0]),
                          (fc - aj, BADGES["FC"][0]), (count - fc, "#d2dbdf")):
        total += amount
        end = left + 1 + round((right - left - 2) * total / count)
        if end > start:
            if color is None:
                canvas.paste(rainbow(end - start, bottom - top - 2), (start, top + 1))
            else:
                draw.rectangle((start, top + 1, end - 1, bottom - 2), fill=color)
        start = end


def op_track(canvas, box, value):
    x, y, right, bottom = box
    rounded(canvas, box, 3, "#d9e8e9")
    end = x + round((right - x) * min(100, max(0, value or 0)) / 100)
    if end > x:
        rounded(canvas, (x, y, end, bottom), 3, TEAL)


def tally(canvas, x, y, right, stats, *, profile=False):
    for index, (name, key) in enumerate(ACHIEVEMENTS):
        left = x + index * 117
        pill(canvas, (left, y, left + 110, y + 27), name, stats[key])
    played = "Played" if profile else "PLAY"
    label(canvas, (right - 285, y - 2, right, y + 28),
          f"{played}  {stats['played']} / {stats['chart_count']}", 24, MUTED, "right", fit=True)


def extra_tally(canvas, y, stats):
    for index, (name, key) in enumerate((("Clear", "clear"), ("SSS", "sss"), ("SSS+", "sss_plus"))):
        x = 858 + index * 165
        pill(canvas, (x, y, x + 150, y + 36), name, stats[key], 21)


def portrait_asset(images):
    portrait = resources.decode_png(images.get("portrait"), minimum_side=512)
    if portrait is not None:
        bounds = portrait.getbbox()
        if bounds and bounds[3] - bounds[1] >= 400:
            return portrait, bounds
        portrait.close()
    return None, None


def draw_player_info(player, images, official, summary, levels):
    canvas = Image.new("RGB", (840, 1740), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 840, 307), fill="#fff7dc")
    draw.polygon(((487, 0), (840, 0), (840, 307), (319, 307)), fill="#e6f4f5")
    draw.polygon(((722, 0), (840, 0), (840, 307), (554, 307)), fill="#f4e8f6")
    brand(canvas, 672, 12, INK)
    label(canvas, (32, 9, 438, 60), "个人资料", 45, INK)
    contain(canvas, playerbar(player, images), (25, 67, 507, 293))
    portrait, _ = portrait_asset(images)
    if portrait is not None:
        with portrait:
            contain(canvas, portrait, (505, 58, 826, 293), trim=True)

    y = 320
    rounded(canvas, (16, y, 824, y + 136), 18, PAPER, LINE)
    label(canvas, (30, y + 10, 128, y + 62), "总 OP", 30, INK)
    actual, maximum = official["total_op"], official["estimated_catalog_ceiling"]
    value = fmt(actual) if maximum is None else op_pair(actual, maximum)
    label(canvas, (139, y + 8, 636, y + 61), value, 38, INK, fit=True)
    label(canvas, (644, y + 10, 801, y + 62), percent(official["op_percent"]), 33, TEAL, "right", fit=True)
    op_track(canvas, (139, y + 63, 799, y + 68), official["op_percent"])
    if summary is None:
        label(canvas, (139, y + 81, 799, y + 126), "国服曲库不可用 · 暂无谱面统计", 25, MUTED)
    else:
        label(canvas, (28, y + 73, 130, y + 95), "MAS / ULT", 17, MUTED, fit=True)
        achievement_track(canvas, (139, y + 76, 799, y + 95), summary)
        tally(canvas, 139, y + 101, 801, summary, profile=True)

    label(canvas, (29, 466, 812, 521), "MASTER / ULTIMA OP", 40, INK)
    for index, (level, stats) in enumerate(levels.items()):
        y = 533 + index * 99
        rounded(canvas, (16, y, 824, y + 94), 18, PAPER, LINE)
        rounded(canvas, (29, y + 11, 116, y + 83), 15, "#eaf0fb")
        label(canvas, (29, y + 11, 116, y + 83), level, 34, INK, "center")
        if stats is None:
            label(canvas, (139, y + 2, 642, y + 92), "—", 29, MUTED)
            continue
        value = op_pair(stats["total_op"], stats["theoretical_op"]) if stats["chart_count"] else "无符合谱面"
        label(canvas, (139, y + 2, 642, y + 35), value, 29, INK, fit=True)
        label(canvas, (645, y + 2, 801, y + 35), percent(stats["op_percent"]), 28, TEAL, "right", fit=True)
        op_track(canvas, (139, y + 35, 799, y + 39), stats["op_percent"])
        achievement_track(canvas, (139, y + 43, 799, y + 59), stats)
        tally(canvas, 139, y + 63, 801, stats, profile=True)
    return png(canvas)


@lru_cache(maxsize=1)
def jacket_mask():
    side = CARD_WIDTH - 10
    mask = Image.new("L", (side, side))
    rounded(mask, (0, 0, side - 1, side - 1), 8, 255)
    return mask


def card(canvas, x, y, row, marked):
    from ..best_rating import rank_to_display, score_to_rank

    record = row.record
    score = record["score"] if record is not None else None
    band = ""
    if score is not None:
        band = "SSS+" if score >= 1009000 else "SSS" if score >= 1007500 else ""
    bg, outline = (("#e0e8eb", "#bdcbd0") if score is None else RANK_BG.get(band, (PAPER, LINE)))
    title = _title(row.song.attributes["title"])
    offset = 22 * (len(title) - 1)
    rounded(canvas, (x, y, x + CARD_WIDTH - 1, y + STRIDE + offset - 8), 10, bg, outline)
    cx, cy = x + 5, y + 5
    side = CARD_WIDTH - 10
    right, bottom = cx + side, cy + side
    jacket = resources.local_jacket(row.chart.jacket_id)
    if jacket is None:
        rounded(canvas, (cx, cy, right, bottom), 8, "#d5e1e6")
        label(canvas, (cx, cy + 27, right, bottom - 15), "缺曲绘", 18, MUTED, "center")
    else:
        with jacket:
            tile = cover(jacket, side)
        if score is None:
            tile = Image.blend(ImageOps.grayscale(tile).convert("RGB"),
                               Image.new("RGB", tile.size, "#cad6d8"), .45)
        canvas.paste(tile, (cx, cy), jacket_mask())
    diff, color = DIFFICULTY[row.chart.difficulty]
    rounded(canvas, (cx + 3, cy + 3, cx + 49, cy + 26), 7, color)
    label(canvas, (cx + 4, cy + 3, cx + 48, cy + 26), diff, 16, PAPER, "center")
    if marked:
        rounded(canvas, (right - 51, cy, right, cy + 22), 5, "#963547")
        label(canvas, (right - 50, cy, right, cy + 22), "EXP↑", 16, PAPER, "center", fit=True)
    if row.tier:
        pill(canvas, (right - 44, bottom - 22, right, bottom), ACHIEVEMENTS[row.tier - 1][0], size=16)
    for index, text in enumerate(title):
        top = bottom + 2 + 22 * index
        label(canvas, (cx, top, right, top + 22), text, 18, INK, fit=True)
    bottom += offset
    if score is None:
        label(canvas, (cx, bottom + 24, right, bottom + 51), "未游玩", 18, MUTED)
    else:
        score_digits(canvas, (cx, bottom + 25, right, bottom + 50), score)
        rank = rank_to_display(record.get("rank"))
        if not rank:
            rank = score_to_rank(score) if score >= 900000 else next(
                name for cutoff, name in LOW_RANKS if score >= cutoff)
        box = (cx, bottom - offset - 23, cx + 56, bottom - offset)
        rounded(canvas, box, 6, BADGES[band][0] if band else "#425d72")
        label(canvas, box, rank, 21, "#573400" if band else PAPER, "center", fit=True)
    label(canvas, (cx, bottom + 51, right, y + STRIDE + offset - 10),
          f"OP {row.op:.2f} / {row.maximum:.2f}", 14, MUTED, fit=True)


def draw_chulist_image(player, images, query, stats, groups, page, page_count, marked, stopped=None):
    matrices = [[group.rows[i:i + COLS] for i in range(0, len(group.rows), COLS)] for group in groups]
    strides = [[_stride(batch) for batch in matrix] for matrix in matrices]
    height = HEADER + sum(GROUP_HEIGHT + sum(rows) for rows in strides) + FOOTER
    if sum(len(group.rows) for group in groups) > PAGE_CARDS:
        raise ValueError("Oversized chulist page")
    canvas = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, 237), fill="#fff7de")
    draw.polygon(((820, 0), (WIDTH, 0), (WIDTH, 237), (695, 237)), fill="#e2f2ee")
    contain(canvas, playerbar(player, images), (16, 4, 664, 233))
    portrait, bounds = portrait_asset(images)
    if portrait is not None:
        with portrait:
            left, top, right, bottom = bounds
            bust_height = round((bottom - top) * .45)
            if bust_height >= 237:
                middle = (left + right) // 2
                half_width = round(bust_height * 462 / 474)
                with portrait.crop((max(0, middle - half_width), top,
                                    min(portrait.width, middle + half_width), top + bust_height)) as bust:
                    contain(canvas, bust, (978, 0, 1440, 237))
            else:
                contain(canvas, portrait, (1020, 3, 1408, 237), trim=True)
    brand(canvas, 701, 4, INK)
    label(canvas, (701, 53, 1050, 129), "谱面进度", 59, INK)
    label(canvas, (704, 136, 978 if portrait is not None else 1394, 221), query, 50, TEAL, fit=True)

    rounded(canvas, (19, 251, 1421, 411), 21, PAPER, LINE)
    label(canvas, (39, 263, 660, 309), f"OP   {op_pair(stats['total_op'], stats['theoretical_op'])}",
          39, INK, fit=True)
    label(canvas, (663, 266, 830, 307), percent(stats["op_percent"]), 36, TEAL, "right", fit=True)
    achievement_track(canvas, (40, 329, 832, 356), stats)
    tally(canvas, 40, 368, 832, stats)
    label(canvas, (862, 256, 1050, 296), f"ALL  {stats['chart_count']}", 29, INK)
    extra_tally(canvas, 314, stats)

    total = stats["chart_count"]
    y = HEADER
    for group, matrix, heights in zip(groups, matrices, strides):
        stats = group.stats
        rounded(canvas, (19, y, 1421, y + 115), 16, PAPER, LINE)
        rounded(canvas, (29, y + 10, 150, y + 70), 13, "#eadff3")
        value = "全部" if group.constant is None else format(group.constant, "f")
        if group.constant is not None and group.constant == group.constant.to_integral_value():
            value = f"{group.constant:.1f}"
        label(canvas, (33, y + 11, 146, y + 70), value, 35, "#563b71", "center", fit=True)
        label(canvas, (176, y + 6, 673, y + 40), f"OP   {op_pair(stats['total_op'], stats['theoretical_op'])}",
              26, INK, fit=True)
        label(canvas, (680, y + 6, 815, y + 40), percent(stats["op_percent"]), 26, TEAL, "right", fit=True)
        achievement_track(canvas, (176, y + 50, 815, y + 71), stats)
        tally(canvas, 176, y + 78, 815, stats)
        extra_tally(canvas, y + 34, stats)
        top = y + 127
        for batch, stride in zip(matrix, heights):
            for index, row in enumerate(batch):
                check_render_cancelled(stopped)
                card(canvas, 24 + index * (CARD_WIDTH + CARD_GAP), top,
                     row, (row.song.id, row.chart.difficulty) in marked)
            top += stride
        y += GROUP_HEIGHT + sum(heights)
    count = sum(len(group.rows) for group in groups)
    label(canvas, (26, y, WIDTH - 26, y + 45),
          f"第 {page} / {page_count} 页     本页 {count} 张     共匹配 {total} 张",
          26, MUTED, "right", fit=True)
    check_render_cancelled(stopped)
    return png(canvas)
