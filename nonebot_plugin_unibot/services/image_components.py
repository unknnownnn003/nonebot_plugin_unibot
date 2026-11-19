"""Shared Pillow drawing primitives and game assets for the 4A images."""
import asyncio
from bisect import bisect_right
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
from threading import local
import unicodedata

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont, ImageOps
import regex

from . import resources

RESAMPLE = Image.Resampling.LANCZOS
LEVELS = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA", "WORLD’S END")
DIFFICULTY_ASSETS = ("basic", "advanced", "expert", "master", "ultima", "we")
DIFFICULTY_COLORS = ("#00a983", "#f48717", "#ed3331", "#8b18c2", "#282a34", "#d5ad33")
RATING_BREAKS = (4, 7, 10, 12, 13.25, 14.50, 15.25, 16, 17)
RATING_INKS = ("#257574", "#2167a4", "#ac5b22", "#637586", "#9e6d00",
               "#7b6793", "#716189", "#934b8c", "#b04469", "#8230a6")
BADGES = {
    "clear": {"failed": "FAILED", "clear": "CLEAR", "hard": "HARD", "brave": "BRAVE",
              "absolute": "ABSOLUTE", "catastrophy": "CATASTROPHY"},
    "combo": {"fullcombo": "FULL COMBO", "alljustice": "ALL JUSTICE",
              "alljusticecritical": "ALL JUSTICE CRITICAL"},
    "chain": {"fullchain": "FULL CHAIN", "fullchain2": "FULL CHAIN"},
}
_fonts = local()


@lru_cache(maxsize=1)
def font_faces():
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    roots = (windows,) if os.name == "nt" else (Path("/usr/share/fonts"), Path("/usr/local/share/fonts"))
    names = ("bahnschrift.ttf", "msyhbd.ttc", "msgothic.ttc", "simsun.ttc") if os.name == "nt" else (
        "DejaVuSans.ttf", "SourceHanSansSC-Bold.otf", "NotoSansCJK-Bold.ttc",
        "NotoSansCJK-Regular.ttc", "SourceHanSansJP-Regular.otf", "wqy-microhei.ttc")
    faces = []
    for name in names:
        path = next((path for root in roots for path in sorted(root.rglob(name))), None)
        if path is None:
            continue
        indices = (2, 0) if name.startswith("NotoSansCJK") else (0,)
        for index in indices:
            try:
                with TTFont(path, fontNumber=index, lazy=True) as font:
                    faces.append((str(path), index, frozenset(font.getBestCmap() or ())))
            except (OSError, TTLibError):
                continue
    return tuple(faces)


def _load_font(path, index, size):
    return ImageFont.truetype(path, size, index=index) if path else ImageFont.load_default(size=size)


def _font(path, index, size):
    if not hasattr(_fonts, "get"):
        _fonts.get = lru_cache(maxsize=128)(_load_font)
    return _fonts.get(path, index, size)


@lru_cache(maxsize=2048)
def _face(cluster, cjk_first=False):
    codes = {ord(char) for char in cluster if unicodedata.category(char) != "Cf"
             and char not in "\ufe0e\ufe0f"}
    faces = font_faces()
    if cjk_first:
        faces = sorted(faces, key=lambda face: ord("中") not in face[2])
    for path, index, coverage in faces:
        if codes <= coverage:
            return path, index, cluster
    return None, 0, cluster if cluster.isascii() else "?"


def _line(value, size, cjk_first=False):
    spans = []
    for cluster in regex.findall(r"\X", value):
        path, index, glyph = _face(cluster, cjk_first)
        font = _font(path, index, size)
        if spans and spans[-1][0] is font:
            spans[-1][1] += glyph
        else:
            spans.append([font, glyph])
    x, parts, bounds = 0.0, [], []
    for font, glyphs in spans:
        left, top, right, bottom = font.getbbox(glyphs, anchor="ls")
        offset = round(x)
        parts.append((offset, font, glyphs))
        bounds.append((offset + left, top, offset + right, bottom))
        x += font.getlength(glyphs)
    if not bounds:
        return parts, (0, 0, 0, 0)
    return parts, (min(b[0] for b in bounds), min(b[1] for b in bounds),
                   max(b[2] for b in bounds), max(b[3] for b in bounds))


def measure(value, size):
    _, (left, _, right, _) = _line(value, size)
    return right - left


def wrap(value, size, width):
    """Wrap Latin words where possible and split long lines at grapheme boundaries."""
    rows, line = [], ""
    for word in regex.findall(r"\s+|[\p{Latin}\p{N}]+|\X", str(value).strip()):
        if line and measure(line + word, size) > width:
            rows.append(line.rstrip())
            line = ""
        if not line and word.isspace():
            continue
        for cluster in regex.findall(r"\X", word):
            if line and measure(line + cluster, size) > width:
                rows.append(line.rstrip())
                line = ""
            line += cluster
    return rows + [line.rstrip()]


def label(canvas, box, value, size, color, align="left", fit=False, *, cjk_first=False):
    x, y, right, bottom = box
    if right <= x or bottom <= y:
        return
    value = " ".join(str(value).split())
    clusters = regex.findall(r"\X", value)
    while True:
        parts, (l, t, r, b) = _line(value, size, cjk_first)
        if r - l <= right - x and b - t <= bottom - y:
            break
        if not fit and r - l > right - x and clusters:
            clusters.pop()
            value = "".join(clusters) + "..."
        elif size > 1:
            size -= 1
        else:
            return
    left = {"left": x, "right": right - (r - l), "center": x + (right - x - r + l) // 2}[align] - l
    baseline = round((y + bottom - t - b) / 2)
    draw = ImageDraw.Draw(canvas)
    for offset, font, glyphs in parts:
        draw.text((left + offset, baseline), glyphs, font=font, anchor="ls", fill=color)


def rounded(canvas, box, radius, color, outline=None):
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius=radius, fill=color, outline=outline)


def contain(canvas, source, box, *, trim=False):
    if source is None:
        return False
    if trim:
        bounds = source.getbbox()
        if not bounds:
            return False
        source = source.crop(bounds)
    x, y, right, bottom = box
    width, height = right - x, bottom - y
    scale = min(width / source.width, height / source.height)
    picture = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), RESAMPLE)
    at = x + (width - picture.width) // 2, y + (height - picture.height) // 2
    if canvas.mode == "RGBA" and picture.mode == "RGBA":
        canvas.alpha_composite(picture, at)
    else:
        canvas.paste(picture, at, picture if picture.mode == "RGBA" else None)
    return True


def cover(image, size):
    return ImageOps.fit(image.convert("RGB"), (size, size), method=RESAMPLE)


def png(canvas):
    with BytesIO() as buffer:
        canvas.save(buffer, format="PNG", compress_level=4)
        return buffer.getvalue()


@lru_cache(maxsize=512)
def ui(group, name):
    # Callers only read or copy these shared images.
    return resources.load_static_asset(group, name)


@lru_cache(maxsize=1)
def _spacing():
    try:
        path = resources.paths.ASSETS_DIR / "ui" / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))["spacing"]
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def digits(group, style, value):
    patches = []
    for char in value:
        key = {".": "dot", ",": "comma"}.get(char, char)
        glyph = ui(group, f"{style}-{key}")
        if glyph is None:
            return None
        patches.append((char, glyph))
    if not patches:
        return None
    spacing = _spacing()
    gap = spacing.get(f"{group}/{style}", 0) if isinstance(spacing, dict) else 0
    gap = gap if isinstance(gap, int) and -32 <= gap <= 32 else 0
    positions, x = [], 0
    for char, glyph in patches:
        positions.append((x, glyph))
        x += max(1, glyph.width + (gap if char != "," else 0))
    image = Image.new("RGBA", (max(x + glyph.width for x, glyph in positions),
                                max(glyph.height for _, glyph in positions)))
    for x, glyph in positions:
        image.alpha_composite(glyph, (x, image.height - glyph.height))
    return image


def rating_frame(value):
    return 1 + bisect_right(RATING_BREAKS, value)


def rating_number(canvas, box, value, places, *, group="player-rating", align="center"):
    if value is None:
        label(canvas, box, "—", 24, "#45546a", align)
        return
    frame = rating_frame(value)
    text = f"{value:.{places}f}"
    if not contain(canvas, digits(group, frame, text), box, trim=True):
        label(canvas, box, text, box[3] - box[1], RATING_INKS[frame - 1], align, fit=True)


def _rating_part(picture, text, size, ink):
    if picture is not None and (bounds := picture.getbbox()):
        return picture.crop(bounds)
    width = measure(text, size) + 4
    height = size + 6
    picture = Image.new("RGBA", (width, height))
    label(picture, (0, 0, width, height), text, size, ink, "center", fit=True)
    return picture.crop(picture.getbbox())


def rating_pair(canvas, box, value, places, *, gap, group="player-rating", centered=False):
    """Lay out the visible title and number as one group."""
    frame = rating_frame(value)
    ink = RATING_INKS[frame - 1]
    text = f"{value:.{places}f}"
    title = _rating_part(ui("rating-title", str(frame)), "RATING", 15, ink)
    number = _rating_part(digits(group, frame, text), text, 21, ink)
    left, top, right, bottom = box
    title_width = title.width * 17 / title.height
    number_width = number.width * 22 / number.height
    scale = min(1, (right - left - gap) / (title_width + number_width), (bottom - top) / 22)
    title_width = round(title_width * scale)
    number_width = round(number_width * scale)
    if centered:
        left += (right - left - title_width - gap - number_width) // 2
    number_left = left + title_width + gap
    contain(canvas, title, (left, top, left + title_width, bottom))
    contain(canvas, number, (number_left, top, number_left + number_width, bottom))


def score_digits(canvas, box, value):
    style = next((name for minimum, name in ((975000, "platinum"), (900000, "gold"),
                  (600000, "silver"), (500000, "bronze")) if value >= minimum), "normal")
    text = f"{value:,}"
    if not contain(canvas, digits("score", style, text), box, trim=True):
        label(canvas, box, text, 56, "#243751", "center", fit=True)


def badge(canvas, box, group, value):
    key = str(value or "").strip().lower().replace("+", "p")
    if group == "rank":
        name = key.upper().replace("P", "+") if key in {
            "d", "c", "b", "bb", "bbb", "a", "aa", "aaa", "s", "sp", "ss", "ssp", "sss", "sssp"} else None
    else:
        name = BADGES[group].get(key)
    if name and not contain(canvas, ui(group, key), box, trim=True):
        color = "#ffe9a4" if group == "chain" and key == "fullchain2" else "#e5ecf5"
        rounded(canvas, box, 5, color)
        label(canvas, (box[0] + 2, box[1], box[2] - 2, box[3]), name, 20, "#182d43", "center", fit=True)


def brand(canvas, x, y, ink):
    contain(canvas, ui("brand", "uni"), (x, y, x + 44, y + 44))
    label(canvas, (x + 47, y, x + 151, y + 44), "UniBot", 24, ink)


async def player_images(player, *, portrait=False):
    kinds = ("plate", "trophy", "character", "portrait") if portrait else ("plate", "trophy", "character")
    trophy = await resources.observed_io(_trophy_metadata, player.get("trophy") or {})
    if isinstance(trophy.get("color"), str) and trophy["color"] != "image":
        kinds = tuple(kind for kind in kinds if kind != "trophy")
    blobs = await asyncio.gather(*(resources.player_asset(player, kind) for kind in kinds))
    return dict(zip(kinds, blobs), trophy_metadata=trophy)


def _trophy_metadata(equipped):
    if equipped.get("id") is None or equipped.get("color"):
        return equipped
    try:
        path = resources.paths.DATA_DIR / "trophy.json"
        rows = json.loads(path.read_text(encoding="utf-8")).get("trophies", [])
        for row in rows:
            if (isinstance(row, dict) and row.get("id") == equipped["id"]
                    and (not equipped.get("name") or equipped["name"] == row.get("name"))):
                return dict(row, **equipped)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return equipped


def playerbar(player, images):
    canvas = Image.new("RGBA", (576, 228))
    if not contain(canvas, resources.decode_png(images.get("plate")), (0, 0, 576, 228)):
        rounded(canvas, (4, 4, 571, 221), 12, "#e4ecef", "#c7dce0")
    if not contain(canvas, ui("playerbar", "base"), (140, 83, 468, 173)):
        rounded(canvas, (140, 83, 468, 173), 7, "#f9fbfc", "#c7dce0")
    possession = player.get("rating_possession")
    if possession in ("copper", "silver", "gold", "platina", "rainbow"):
        contain(canvas, ui("playerbar", f"field-{possession}"), (140, 83, 468, 173))
    trophy = images.get("trophy_metadata") or player.get("trophy") or {}
    trophy_box = (135, 39, 561, 89)
    title = resources.decode_png(images.get("trophy"))
    if not contain(canvas, title, trophy_box) and trophy.get("name"):
        color = trophy.get("color")
        strip = ui("title-board", color) if color in ("normal", "silver", "gold", "platina", "rainbow", "ultima") else None
        if not contain(canvas, strip, trophy_box):
            rounded(canvas, trophy_box, 7, "#f3f6f8", "#c7dce0")
        label(canvas, (150, 40, 546, 88), trophy["name"], 28, "#182d43", "center", fit=True)
    avatar = resources.decode_png(images.get("character"))
    if avatar is not None:
        contain(canvas, ui("playerbar", "avatar-frame"), (465, 84, 553, 172))
        contain(canvas, avatar, (468, 87, 550, 169))
    nickname_left = 229 if player.get("level") is not None else 148
    nickname = player.get("name") or "—"
    mixed_script = bool(regex.search(r"[\p{Han}\p{Hiragana}\p{Katakana}\p{Hangul}]", nickname))
    label(canvas, (nickname_left, 96, 455, 142), nickname, 30, "#1a1e2c", cjk_first=mixed_script)
    reborn = player.get("reborn_count") or 0
    if reborn:
        contain(canvas, ui("playerbar", "star"), (142, 86, 182, 126))
        if not contain(canvas, digits("playerbar", "reborn", str(reborn)), (144, 96, 178, 114)):
            label(canvas, (144, 90, 178, 111), f"★{reborn}", 16, "#49506b", "center", fit=True)
    if player.get("level") is not None:
        if not contain(canvas, ui("playerbar", "level-label"), (143, 104, 183, 140)):
            label(canvas, (145, 107, 171, 138), "Lv.", 17, "#182d43")
        if not contain(canvas, digits("playerbar", "level", str(player["level"])), (168, 100, 228, 136)):
            label(canvas, (171, 100, 226, 136), player["level"], 27, "#182d43", fit=True)
    rating = player.get("rating")
    if rating is not None:
        rating_pair(canvas, (148, 142, 313, 172), rating, 2, gap=18)
    emblem = player.get("class_emblem") or {}
    base_drawn = False
    for key, kind, box in (("base", "ribbon", (317, 140, 415, 172)), ("medal", "medal", (334, 131, 400, 181))):
        slot = emblem.get(key)
        if type(slot) is int and 1 <= slot <= 6:
            if not contain(canvas, ui("class", f"{kind}-{slot}"), box) and not base_drawn:
                label(canvas, box, f"CLASS {slot}", 17, "#182d43", "center", fit=True)
            base_drawn = True
    return canvas.crop(canvas.getbbox())
