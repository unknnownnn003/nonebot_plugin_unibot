"""Render real UniBot PNGs offline using public song metadata and synthetic scores."""
import argparse
from dataclasses import replace
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
from random import Random
import shutil
import sys
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import nonebot
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".tools" / "preview-ui-final"
YADI_IDS = (317, 618, 619, 670, 749, 753, 804, 881, 887, 893, 971, 972, 999,
            1086, 1100, 1107, 2034, 2038, 2121, 2135, 2161, 2190, 2206, 2210, 2229,
            2241, 2279, 2338, 2339, 2400, 2410, 2456, 2530, 2554, 2582, 2597, 2648, 2696, 2729)
TITLE = "星を追いかけて ～ 夜航列车 / Beyond the Horizon ♫ × Ω ～"
LONG_TITLE = (TITLE + "　果てしない夜空を越えて ") * 5 + " Étoile"
PROFILES = {"基础": (9.5, 977_000), "进阶": (12.5, 997_500), "高阶": (14.8, 1_007_100)}


def recommendation_fixture():
    from nonebot_plugin_unibot.services.song_model import Catalog
    from nonebot_plugin_unibot.services.player_data import Snapshot

    songs, snapshots = [], {}
    for group, (name, (constant, score)) in enumerate(PROFILES.items()):
        scores = []
        for i in range(12):
            sid = 1000 + group * 100 + i
            value = round(constant + .1 * (i % 5 - 2), 1)
            if group == 2:
                value = {10: 15.2, 11: 15.1}.get(i, value)
            title = LONG_TITLE if group == 2 and i == 10 else f"{TITLE} · {i + 1:02}"
            diff = 2 if group == 0 else 3 + (i % 4 == 3)
            display = f"{int(value)}" + ("+" if round(value * 10) % 10 >= 5 else "")
            songs.append(dict(id=sid, title=title,
                              difficulties=[dict(difficulty=diff, level=display, level_value=value)]))
            if group == 2 and i == 11:
                songs[-1]["difficulties"].append(dict(difficulty=2, level="14", level_value=14.3))
                scores.append(dict(id=sid, level_index=2, score=1_007_500, rating=16.3,
                                   over_power=84.4, rank="sss", full_combo="none"))
            if i < 8 or (group == 2 and i == 10):
                points = 1_009_240 if i == 10 else score + (i - 3) * (250 if group < 2 else 100)
                rank = next(rank for cutoff, rank in ((1_009_000, "sssp"), (1_007_500, "sss"), (1_005_000, "ssp"),
                            (1_000_000, "ss"), (990_000, "sp"), (975_000, "s"), (0, "aaa"))
                            if points >= cutoff)
                op = round((value + 3) * 5 - (5.5 - group * 1.5) + i * .12, 2)
                scores.append(dict(id=sid, level_index=diff, score=points, rating=value + group * .75,
                                   over_power=op, rank=rank, full_combo="none"))
        snapshots[name] = Snapshot({"name": name, "rating": constant + group * .75}, scores, "demo", 0)
    return Catalog({"songs": songs}), snapshots


async def render(public_catalog, jacket_dir, output_dir):
    from nonebot_plugin_unibot import chuguess, help, recommendation, song
    from nonebot_plugin_unibot.services import image_components as ui
    from nonebot_plugin_unibot.services import song as song_service
    from nonebot_plugin_unibot.services.recommendation import select
    from nonebot_plugin_unibot.services.song_model import Catalog

    assert ui.digits("score", "platinum", "1,007,500") is not None
    assert all(ui.ui("level", key) is not None for key in ("standard-board", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "plus"))
    assert all(ui.ui("rank", key) is not None for key in ("ss", "ssp", "sss", "sssp"))
    assert all(ui.ui("combo", key) is not None for key in ("fullcombo", "alljustice", "alljusticecritical"))
    assert all(ui.ui("clear", key) is not None for key in ("clear", "hard", "brave", "absolute", "catastrophy"))
    art = {}
    for sid in YADI_IDS:
        path = jacket_dir / f"{sid}.png"
        if path.is_file():
            with Image.open(path) as im:
                art[sid] = im.convert("RGB")
    if not art:
        raise RuntimeError("Pass an existing public jacket directory with --jackets")
    images = list(art.values())
    cover = images[0]

    catalog, snapshots = recommendation_fixture()
    high = snapshots["高阶"]
    demos = {name: select(snapshot, catalog, "auto", None, rng=Random(19))
             for name, snapshot in snapshots.items()}
    outputs = {"help": help.draw_help()}
    picks = {
        "recommendation_default": demos["高阶"],
        "recommendation_score": select(high, catalog, "score", None, rng=Random(19)),
        "recommendation_op": select(high, catalog, "op", None, rng=Random(19)),
        "recommendation_long_title": select(high, catalog, "score", "15.2", rng=Random(19)),
        "recommendation_unplayed": select(high, catalog, "op", "15.1", rng=Random(19)),
        **{f"recommendation_{mode}": select(high, catalog, mode, None, rng=Random(19))
           for mode in ("fc", "aj", "ajc")},
        "recommendation_beginner": demos["基础"],
        "recommendation_intermediate": demos["进阶"],
    }
    assert len(picks["recommendation_default"]) == len(picks["recommendation_score"]) == len(picks["recommendation_op"]) == 2
    assert len(picks["recommendation_long_title"]) == 1 and picks["recommendation_long_title"][0][1].song.attributes["title"] == LONG_TITLE
    assert len(picks["recommendation_unplayed"]) == 1 and picks["recommendation_unplayed"][0][1].record is None
    rec_art = {sid: images[i % len(images)] for i, sid in enumerate(catalog.songs)}
    for name, selected in picks.items():
        assert selected, name
        outputs[name] = recommendation.render_recommendation_image(selected, rec_art)

    score_card, op_card = picks["recommendation_default"]

    def with_status(card, clear, combo):
        mode, candidate = card
        points = 1_010_000 if combo == "alljusticecritical" else candidate.record["score"]
        rank = "sssp" if combo == "alljusticecritical" else candidate.record["rank"]
        return mode, replace(candidate, record=dict(candidate.record, score=points, rank=rank,
                                                    clear=clear, full_combo=combo))

    for name, cards in {
        "recommendation_clear_fc_aj": [with_status(score_card, "clear", "fullcombo"),
                                       with_status(op_card, "absolute", "alljustice")],
        "recommendation_clear_ajc": [with_status(score_card, "hard", None),
                                     with_status(op_card, "catastrophy", "alljusticecritical")],
        "recommendation_rank_only": [score_card, op_card],
    }.items():
        outputs[name] = recommendation.render_recommendation_image(cards, rec_art)

    ui.ui.cache_clear()
    try:
        with patch.object(ui.resources, "load_static_asset", return_value=None):
            outputs["recommendation_fallback"] = recommendation.render_recommendation_image(
                [with_status(score_card, "clear", "fullcombo"), with_status(op_card, "brave", "alljustice")], rec_art)
    finally:
        ui.ui.cache_clear()


    regular = Catalog({"songs": [{"id": index, "title": title, "aliases": [alias], "difficulties": []}
        for index, title, alias in [(120, TITLE, "星之旅"), (121, LONG_TITLE, "旅星"),
                                    (122, "Signal × Resonance [Re:Mix]", "星への信号")]]})
    raw = json.loads(public_catalog.read_text(encoding="utf-8-sig"))
    yadi = Catalog({"songs": [dict(s, aliases=["雅迪"]) for s in raw.get("data", raw)["songs"] if s["id"] in YADI_IDS]})
    demo_art = dict(zip(regular.songs, images, strict=False))
    with patch.object(song, "download_match_jacket",
                      AsyncMock(side_effect=lambda match: (art | demo_art).get(match["song"]["id"]))):
        found = await song_service.prepare("星", catalog=regular)
        assert found["total"] == 3
        output = await song_service.search("星", prepared=found)
        outputs["song_candidates"] = output["artifacts"][0]["content"]
        yadi_result = []
        for page in range(1, 6):
            query = f"雅迪 --page {page}"
            found = await song_service.prepare(query, catalog=yadi)
            assert (found["total"], found["page"], len(found["ambiguous"])) == (39, page, 8 if page < 5 else 7)
            output = await song_service.search(query, prepared=found)
            outputs[f"yadi_page_{page}"] = output["artifacts"][0]["content"]
            yadi_result.extend(item["id"] for item in output["data"]["candidates"])
        assert set(yadi_result) == set(YADI_IDS) and len(yadi_result) == 39

    crop = (40, 36, 60, 60)
    outputs["guess_start"] = chuguess.make_clue_image(cover.crop((40, 36, 100, 96)), "猜猜这首歌")
    outputs["guess_hint_location"] = chuguess.make_clue_image(chuguess.make_crop_preview(cover, *crop), "截取位置")
    outputs["guess_hint_extra"] = chuguess.make_clue_image(
        chuguess.make_revealed_preview(cover, [crop, (175, 120, 60, 60)]), "额外曲绘区域")
    for state in ("success", "timeout", "giveup"):
        outputs["guess_" + state] = chuguess.make_game_result_image(
            {"title": TITLE}, cover, *crop, "mas", 5, state == "success", "星海観測部", state)
    outputs["guess_long_title_and_nickname"] = chuguess.make_game_result_image(
        {"title": LONG_TITLE}, cover, *crop, "mas", 5, True, "星海観測部の旅人 · Long Nickname " * 6, "success")
    (output_dir / "synthetic-results.json").write_text(json.dumps(
        {name: [c.data(mode) for mode, c in picks] for name, picks in demos.items()},
        ensure_ascii=False, indent=2), encoding="utf8")

    sheet = Image.new("RGB", (1320, ((len(outputs) + 3) // 4) * 420), "#eaf4f4")
    for i, (name, data) in enumerate(outputs.items()):
        with Image.open(BytesIO(data)) as generated:
            assert generated.format == "PNG"
            im = Image.new("RGB", (generated.width, generated.height + 42), "#eaf4f4")
            im.paste(generated, (0, 42))
            ui.label(im, (24, 0, im.width - 24, 42), "离线演示 · 合成成绩与昵称", 25, "#245850")
            im.save(output_dir / (name + ".png"))
            im.thumbnail((318, 375), Image.Resampling.LANCZOS)
            x, y = i % 4 * 330, i // 4 * 420
            sheet.paste(im, (x + (330 - im.width) // 2, y + 33))
            ui.label(sheet, (x + 8, y, x + 322, y + 30), name, 17, "#182d43", "center", fit=True)
    sheet.save(output_dir / "index.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="Cached public LXNS song list JSON")
    parser.add_argument("--jackets", required=True, type=Path, help="Directory of local public song jackets named by ID")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Directory for generated previews")
    args = parser.parse_args()
    catalog_path, jackets_path = args.catalog.resolve(), args.jackets.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output_dir.parent) as temp:
        # The real renderers use the local UI pack, but registration and
        # migration checks run away from the real LocalStore and configuration.
        shutil.copytree(ROOT / "nonebot_plugin_unibot", Path(temp) / "nonebot_plugin_unibot",
                        ignore=shutil.ignore_patterns("data", "__pycache__"))
        sys.path.insert(0, temp)
        os.chdir(temp)
        try:
            nonebot.init(driver="~none", localstore_data_dir=temp + "/data",
                         localstore_cache_dir=temp + "/cache", localstore_config_dir=temp + "/config")
            if nonebot.load_plugin("nonebot_plugin_unibot") is None:
                raise RuntimeError("UniBot failed to load")
            asyncio.run(render(catalog_path, jackets_path, output_dir))
        finally:
            os.chdir(ROOT)
