"""Shared frames for command and game cards."""
from PIL import Image
from .image_components import brand, contain, label, rounded
from .song_images import BG, INK, LINE, MUTED, PAPER, TEAL


def frame(title, height, width=800):
    canvas = Image.new("RGB", (width, height), BG)
    rounded(canvas, (0, 0, width, 7), 0, TEAL)
    label(canvas, (28, 15, width - 200, 76), title, 36, INK)
    brand(canvas, width - 170, 22, INK)
    return canvas


def artwork(canvas, picture, box):
    rounded(canvas, box, 10, "#dbecea")
    if not contain(canvas, picture, box):
        label(canvas, box, "缺曲绘", 22, MUTED, "center")


def lines(canvas, rows, x, y, right, size, step, color):
    for row in rows:
        label(canvas, (x, y, right, y + step), row, size, color)
        y += step
    return y
