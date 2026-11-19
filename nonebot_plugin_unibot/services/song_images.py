"""4A song information and single-song result layouts."""
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFilter

from .image_components import (
    DIFFICULTY_ASSETS, DIFFICULTY_COLORS, LEVELS, RESAMPLE, badge, brand, contain, cover,
    label, measure, playerbar, png, rounded, score_digits, ui, wrap,
)
from .resources import decode_png

WIDTH = 960
INK = "#182d43"
MUTED = "#3f5568"
BG = "#eaf4f4"
PAPER = "#ffffff"
LINE = "#c7dce0"
TEAL = "#087970"
NOTE_TYPES = (("TAP", "tap", "#c8686f"), ("HOLD", "hold", "#d69c32"),
              ("SLIDE", "slide", "#599aca"), ("AIR", "air", "#69aa75"), ("FLICK", "flick", "#ae7abd"))


def _version(canvas, box, song, version):
    if not contain(canvas, ui("version", str(version)), box, trim=True):
        name = next((row.get("title") for row in (song.get("_versions") or [])
                     if isinstance(row, dict) and row.get("version") == version), None)
        label(canvas, box, name or version or "—", 25, INK, "center", fit=True)


def _header(song, jacket, kind, *, compact=False):
    for height in range(253, 508, 62):
        left = height + 13
        available = WIDTH - 32 - left
        titles = wrap(song.get("title") or "—", 52, available - 6)
        artists = wrap(song.get("artist") or "—", 27, available)
        if 64 + 62 * len(titles) + 33 * len(artists) + 80 <= height:
            break
    # Move overflow below the cover. Reserve space for the artist and ID row.
    main_titles = max(1, (height - 64 - 33 * len(artists) - 80) // 62)
    main_artists = max(1, (height - 64 - 62 * min(len(titles), main_titles) - 80) // 33)
    overflow = [(line, 62, 52, INK) for line in titles[main_titles:]]
    overflow += [(line, 33, 27, MUTED) for line in artists[main_artists:]]
    more = sum(line_height for _, line_height, _, _ in overflow)
    metadata_height = 67 if compact else 108
    canvas = Image.new("RGB", (WIDTH, height + more + metadata_height), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, height + more - 1), fill="#fff5d9")
    draw.polygon(((640, 0), (WIDTH, 0), (WIDTH, height + more - 1), (505, height + more - 1)), fill="#dbefec")
    side = height - 51
    rounded(canvas, (21, 19, side + 37, side + 35), 15, PAPER, LINE)
    if jacket is None:
        label(canvas, (29, 27, 29 + side, 27 + side), "缺曲绘", 25, MUTED, "center")
    else:
        canvas.paste(cover(jacket, side), (29, 27))
    label(canvas, (left, 15, WIDTH - 28, 55), kind, 28, TEAL)
    brand(canvas, WIDTH - 174, 9, INK)

    def title_line(x, y, line):
        mask = Image.new("L", (WIDTH - 28 - x, 62))
        label(mask, (2, 0, mask.width - 2, 62), line, 52, 255, fit=True)
        canvas.paste(INK, (x, y, x + mask.width, y + 62), mask.filter(ImageFilter.MaxFilter(3)))

    y = 58
    for line in titles[:main_titles]:
        title_line(left, y, line)
        y += 62
    for line in artists[:main_artists]:
        label(canvas, (left, y, WIDTH - 28, y + 33), line, 27, MUTED, fit=True)
        y += 33
    cell = (WIDTH - 28 - left - 20) // 3
    for index, value in enumerate((song.get("genre") or "—", f"BPM  {song.get('bpm') or '—'}", f"ID  {song['id']}")):
        x = left + index * (cell + 10)
        box = (x, height - 68, x + cell, height - 27)
        rounded(canvas, box, 11, PAPER)
        label(canvas, box, value, 23, INK, "center", fit=True)
    y = height
    for line, line_height, size, color in overflow:
        if size == 52:
            title_line(28, y, line)
        else:
            label(canvas, (28, y, WIDTH - 28, y + line_height), line, size, color, fit=True)
        y += line_height
    label(canvas, (28, y, 110, y + metadata_height), "版本", 25, MUTED)
    _version(canvas, (95, y, 306, y + 63) if compact else (96, y + 5, 413, y + 100),
             song, song.get("version"))
    label(canvas, (433, y, WIDTH - 30, y + metadata_height),
          f"国服配信日期：{song.get('release_date') or '—'}", 25, MUTED, "right", fit=True)
    return canvas


def _aliases(values):
    values = list(dict.fromkeys(value.strip() for value in (values or []) if isinstance(value, str) and value.strip()))
    if not values:
        return None
    labels = []
    x, y, row_height = 122, 0, 0
    for value in values:
        lines = wrap(value, 23, WIDTH - 122 - 32 - 26)
        width = max(measure(line, 23) for line in lines) + 26
        height = 34 * len(lines)
        if x + width > WIDTH - 32:
            x, y, row_height = 122, y + row_height + 8, 0
        labels.append((x, y, width, height, lines))
        row_height = max(row_height, height)
        x += width + 9
    canvas = Image.new("RGB", (WIDTH, y + row_height + 14), BG)
    label(canvas, (28, 0, 116, 34), "别名", 23, MUTED)
    for x, y, width, height, lines in labels:
        rounded(canvas, (x, y, x + width, y + height), 10, "#f9fdfc", LINE)
        for i, line in enumerate(lines):
            label(canvas, (x + 10, y + 34 * i, x + width - 10, y + 34 * (i + 1)),
                  line, 23, INK, "center", fit=True)
    return canvas


def _difficulty(canvas, x, y, index, *, compact=False):
    width, height = (181, 44) if compact else (272, 66)
    box = (x, y, x + width, y + height)
    if not contain(canvas, ui("difficulty", DIFFICULTY_ASSETS[index]), box):
        rounded(canvas, box, 4, DIFFICULTY_COLORS[index])
        label(canvas, box, LEVELS[index], 34, PAPER, "center", fit=True)


def _chart_label(chart):
    if chart["difficulty"] == 5:
        stars = chart.get("star")
        return f"{chart.get('kanji') or '—'}  ★{stars}" if type(stars) is int else str(chart.get("kanji") or "—")
    constant = chart.get("level_value")
    return f"{constant:.1f}" if constant is not None else str(chart.get("level") or "—")


def _level(canvas, x, y, chart, *, compact=False):
    we = chart["difficulty"] == 5
    width, height = (70, 73) if compact else (105, 110)
    box = (x, y, x + width, y + height)
    value = _chart_label(chart)
    plus = not we and str(chart.get("level", "")).endswith("+") and not value.endswith("+")
    fallback = value + ("+" if plus else "")
    board = ui("level", "we-board" if we else "standard-board")
    if board is None:
        rounded(canvas, box, 5, "#edf1f6" if we else "#254354")
        label(canvas, box, fallback, 32, INK if we else PAPER, "center", fit=True)
        return
    board = board.copy()
    body = (4, 24, 72, 82)
    if we:
        stars = chart.get("star")
        if type(stars) is int and 1 <= stars <= 10:
            if not contain(board, ui("level", f"stars-{stars}"), (2, 2, 74, 22), trim=True):
                label(board, (2, 2, 74, 22), f"★{stars}", 15, INK, "center")
        label(board, body, chart.get("kanji") or "—", 50, INK, "center", fit=True)
    else:
        major, dot, minor = value.partition(".")
        glyphs = [ui("level", digit) for digit in major + minor] if dot else []
        if glyphs and all(glyphs):
            glyphs = [glyph.crop(glyph.getbbox()) for glyph in glyphs]
            run = Image.new("RGBA", (max(160, sum(g.width + 1 for g in glyphs) + 19), 72))
            cursor = 0
            for glyph in glyphs[:-1]:
                run.alpha_composite(glyph, (cursor, 56 - glyph.height))
                cursor += glyph.width + 1
            fraction = glyphs[-1]
            fraction.thumbnail((15, 17), RESAMPLE)
            ImageDraw.Draw(run).rectangle((cursor, 53, cursor + 2, 55), fill="#faf4db")
            run.alpha_composite(fraction, (cursor + 4, 56 - fraction.height))
            if plus:
                if not contain(run, ui("level", "plus"), (cursor - 1, 13, cursor + 13, 27), trim=True):
                    label(run, (cursor - 1, 13, cursor + 13, 27), "+", 14, PAPER, "center")
            run = run.crop(run.getbbox())
            run.thumbnail((68, 57), RESAMPLE)
            board.alpha_composite(run, (2 + (72 - run.width) // 2, 24 + (59 - run.height) // 2))
        else:
            label(board, body, fallback, 42, PAPER, "center", fit=True)
    contain(canvas, board, box)


def _added(canvas, y, song, chart):
    version, date = chart.get("version"), chart.get("release_date")
    base_date = song.get("release_date")
    late = isinstance(date, str) and isinstance(base_date, str) and date > base_date
    different_version = version is not None and version != song.get("version")
    we = chart["difficulty"] == 5
    if we or (chart["difficulty"] == 4 and (late or different_version)):
        if version is not None and (we or different_version):
            label(canvas, (413, y, 655, y + 30), "追加版本", 22, MUTED, "center")
            _version(canvas, (410, y + 29, 659, y + (96 if date else 116)), song, version)
            if date:
                label(canvas, (413, y + 96, 655, y + 119), date, 20, MUTED, "center")
        elif date:
            label(canvas, (411, y + 9, 666, y + 100), f"追加日期：{date}", 23, MUTED, "center", fit=True)
    if we:
        label(canvas, (680, y + 94, 913, y + 119), f"WE ID  {chart['song_id']}", 19, MUTED, "right")


def _notes(canvas, y, chart):
    notes = chart.get("notes")
    notes = notes if isinstance(notes, dict) else {}
    vals = [notes.get(key) for _, key, _ in NOTE_TYPES]
    x0, width = 221, WIDTH - 221 - 48
    label(canvas, (20, y + 77, 220, y + 134), f"物量 {notes.get('total') if notes.get('total') is not None else '—'}",
          24, INK, "center", fit=True)
    rounded(canvas, (x0, y + 82, x0 + width, y + 99), 3, "#e3e9ed")
    if all(type(val) is int and val >= 0 for val in vals) and (total := sum(vals)):
        start = x0
        count = 0
        for val, (_, _, color) in zip(vals, NOTE_TYPES):
            count += val
            end = x0 + round(width * count / total)
            if end > start:
                ImageDraw.Draw(canvas).rectangle((start, y + 82, end - 1, y + 99), fill=color)
            start = end
    for j, ((name, _, color), val) in enumerate(zip(NOTE_TYPES, vals)):
        x = x0 + j * (width + 9) // 5
        rounded(canvas, (x, y + 113, x + 9, y + 122), 3, color)
        label(canvas, (x + 14, y + 101, x + (width + 9) // 5 - 4, y + 136),
              f"{name} {val if val is not None else '—'}", 19, INK, fit=True)


def render_song_image(song, jacket):
    top = _header(song, jacket, "曲目信息")
    tags = _aliases(song.get("aliases"))
    y = top.height + (tags.height if tags else 0)
    layouts = []
    for chart in song["difficulties"]:
        left = 680 if chart["difficulty"] in (4, 5) else 420
        lines = wrap(chart.get("note_designer") or "—", 25, 909 - left)
        footer = 42 if chart["difficulty"] == 5 else 12
        layouts.append((chart, left, lines, max(122, 34 * len(lines) + footer)))
    canvas = Image.new("RGB", (WIDTH, y + sum(height + 87 for _, _, _, height in layouts) + 13), BG)
    canvas.paste(top, (0, 0))
    if tags:
        canvas.paste(tags, (0, top.height))
    for chart, left, lines, height in layouts:
        rounded(canvas, (19, y, 941, y + height + 77), 17, PAPER, LINE)
        _difficulty(canvas, 32, y + 26, chart["difficulty"])
        _level(canvas, 296, y + 5, chart)
        _added(canvas, y, song, chart)
        designer_height = height - 32 if chart["difficulty"] == 5 else height
        name_y = y + (designer_height - len(lines) * 34) // 2
        for j, line in enumerate(lines):
            label(canvas, (left, name_y + j * 34, 913, name_y + (j + 1) * 34),
                  line, 25, INK, "right", fit=True)
        _notes(canvas, y + height - 72, chart)
        y += height + 87
    return png(canvas)


def _op(actual, maximum, *, detail=False):
    if actual is None:
        return "—"
    if maximum is None:
        return f"{actual:,.2f}"
    value = f"{actual:,.2f} / {maximum:,.2f}"
    if detail:
        value += f"  (-{max(0, maximum - actual):,.2f})   {100 * actual / maximum:.2f}%"
    return value


def render_song_scores(song, jacket, player, images, rows, song_op, maxima):
    banner = _header(song, jacket, "单曲成绩", compact=True)
    tags = _aliases(song.get("aliases"))
    summary_height = 78 if song_op is not None else 0
    start = 162 + banner.height + summary_height + (tags.height if tags else 0)
    canvas = Image.new("RGB", (WIDTH, start + max(1, len(rows)) * 202 + 12), BG)
    contain(canvas, playerbar(player, images), (23, 2, 465, 159))
    character_box = (790, 0, 940, 162)
    portrait = decode_png(images.get("portrait"), minimum_side=512)
    drawn = False
    if portrait is not None:
        with portrait:
            drawn = contain(canvas, portrait, character_box, trim=True)
    if not drawn:
        contain(canvas, decode_png(images.get("character")), character_box, trim=True)
    canvas.paste(banner, (0, 162))
    y = 162 + banner.height
    if song_op is not None:
        actual, maximum = song_op
        rounded(canvas, (19, y, 941, y + 68), 13, PAPER, LINE)
        label(canvas, (35, y, 183, y + 66), "曲目 OP", 27, INK)
        label(canvas, (184, y, 511, y + 66), _op(actual, maximum), 27, INK, fit=True)
        if actual is not None:
            label(canvas, (514, y, 916, y + 66), f"{100 * actual / maximum:.2f}%", 24, TEAL, "right")
        y += summary_height
    if tags:
        canvas.paste(tags, (0, y))
    if not rows:
        rounded(canvas, (19, start, 941, start + 180), 17, PAPER, LINE)
        label(canvas, (32, start, 927, start + 180), "所选难度暂无成绩", 33, MUTED, "center")
    for i, row in enumerate(rows):
        y = start + i * 202
        rounded(canvas, (19, y, 941, y + 191), 17, PAPER, LINE)
        y += 6
        index, charts = row["level_index"], row["charts"]
        _difficulty(canvas, 32, y + 14, index, compact=True)
        if len(charts) == 1:
            _level(canvas, 208, y, charts[0], compact=True)
        else:
            right = 765 if row.get("full_chain") else 920
            label(canvas, (290, y + 5, right, y + 64), " / ".join(map(_chart_label, charts)), 30, INK, fit=True)
        score_digits(canvas, (34, y + 81, 430, y + 135), row["score"])
        badges = (("rank", row.get("rank")), ("clear", row.get("clear")), ("combo", row.get("full_combo")))
        if any(value for _, value in badges):
            rounded(canvas, (483, y + 66, 924, y + 126), 13, "#eff5f6")
            for col, (kind, value) in enumerate(badges):
                left = 491 + 142 * col
                badge(canvas, (left, y + 77, left + 136, y + 115), kind, value)
        if row.get("full_chain"):
            badge(canvas, (775, y + 25, 911, y + 63), "chain", row["full_chain"])
        if row["standard_rating_applicable"]:
            rating = row.get("rating")
            rating = f"{rating:.2f}" if rating is not None else "—"
            rounded(canvas, (32, y + 142, 264, y + 178), 7, "#eef0f8")
            label(canvas, (42, y + 142, 255, y + 178), f"Rating   {rating}", 23, INK, "center", fit=True)
        if row["standard_op_applicable"]:
            box = (278, y + 142, 920, y + 178)
            rounded(canvas, box, 7, "#e8f4f1")
            maximum = maxima.get((row["id"], index))
            op = row.get("over_power")
            actual = Decimal(str(op)) if op is not None else None
            label(canvas, box, f"OP   {_op(actual, maximum, detail=True)}", 23, INK, "center", fit=True)
    return png(canvas)
