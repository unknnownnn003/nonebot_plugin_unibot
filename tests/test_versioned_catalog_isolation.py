"""A real isolated UniBot host across Versioned Catalog failures and rollbacks."""
import asyncio
from contextlib import ExitStack, closing
import hashlib
from itertools import count
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "nonebot_plugin_unibot"
OLD_DATE = "2025-07-15"


def stage_versioned_release(root, suffix, constant):
    """Build a colliding JP identity under the actual validated release format."""
    from build_history_catalog import (
        SCHEMA, artifact_digest, format_json, publish_snapshots, validate_package,
    )
    from history_import import CatalogImport, json_text, offset, sha

    inputs = {"fixture": suffix}
    release = sha(json_text(inputs).encode("utf-8"))
    folder = root / "releases" / release
    folder.mkdir(parents=True)
    database = folder / "catalog.sqlite"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
        conn.execute("INSERT INTO package VALUES (?,?,?,?)",
                     (release, "test", json_text(inputs), sha(SCHEMA.read_bytes())))
        conn.execute("INSERT INTO source VALUES ('fixture','https://example.test/','fixture')")
        conn.execute("INSERT INTO source_artifact VALUES (?,?,?,?,?,?,?,?)",
                     ("fixture", "fixture", "https://example.test/jp", "0" * 64, 0, OLD_DATE, "fixture", "test"))
        importer = CatalogImport(conn, {"fixture": {"observed_on": OLD_DATE}})
        importer.release("JP", "VERSE", "2024-12-12", "2025-07-16", OLD_DATE,
                         "Asia/Tokyo", "history", "fixture", "version")
        importer.points.append(("JP", "VERSE", OLD_DATE, "version_end"))
        name = "Same title"
        song = importer.song(("fixture", suffix), name, "fixture", "10")
        importer.record_name(song, "JP fixture alias", "fixture", "fixture", "10.alias")
        revision = importer.chart(song, "MAS", "fixture")
        importer.external("fixture", "JP", "10", "MAS", song, "10.MAS", revision)
        end = offset(OLD_DATE)
        state = [(OLD_DATE, end, "playable", "B", "point", 10)]
        importer.states("fixture", "10", "JP", song, name, state)
        importer.states("fixture", "10.MAS", "JP", song, name, state, revision=revision)
        importer.constant("fixture", "10.MAS", "JP", song, revision, str(constant / 10),
                          constant, start=OLD_DATE, end=end)
        importer.materialize_intervals()
        coverage = publish_snapshots(importer, ())
    (folder / "coverage.json").write_bytes(format_json(coverage))
    checksums = {name: artifact_digest(folder / name) for name in ("catalog.sqlite", "coverage.json")}
    (folder / "manifest.json").write_bytes(format_json({
        "release_id": release, "inputs": inputs, "sha256": checksums,
    }))
    validate_package(folder)
    return release


def activate(root, release):
    from build_history_catalog import main

    with patch.object(sys, "argv", ["build_history_catalog.py", "--output", str(root), "--activate", release]):
        main()


def run_host(mode):
    import httpx
    import nonebot
    from cryptography.fernet import Fernet
    from nonebot.adapters.onebot.v11 import Bot, Message
    from nonebot.message import handle_event
    from test_player_data import event, personal
    from test_song_model import fixture

    root = Path.cwd()
    target = root / "data" / PACKAGE
    versioned = target / "historical_catalog"
    if mode in {"corrupt", "incompatible"}:
        broken = versioned / "releases" / ("a" * 64)
        broken.mkdir(parents=True)
        (versioned / "CURRENT").write_text("a" * 64, encoding="ascii")
        if mode == "corrupt":
            (broken / "catalog.sqlite").write_bytes(b"invalid SQLite")
        else:
            with closing(sqlite3.connect(broken / "catalog.sqlite")) as conn:
                conn.execute("PRAGMA user_version=999")

    nonebot.init(driver="~none", command_start={"/"}, localstore_data_dir=str(root / "data"),
                 localstore_cache_dir=str(root / "cache"), localstore_config_dir=str(root / "config"))
    assert nonebot.load_plugin(PACKAGE) is not None
    from nonebot_plugin_unibot import best_rating, song as native_song
    from nonebot_plugin_unibot.services import legacy_rating, oauth_runtime, paths, player, player_data, song
    from nonebot_plugin_unibot.services import song_images, sync
    from nonebot_plugin_unibot.services.history import HistoryCatalog
    from nonebot_plugin_unibot.services.oauth import OAuthService, Settings
    from nonebot_plugin_unibot.services.oauth_store import OAuthStore
    from nonebot_plugin_unibot.services.song_model import Catalog, read_catalog

    assert paths.HISTORY_DIR == versioned
    cn = fixture()
    cn["rating_version"] = 24000
    cn["versions"].append({"version": 15500, "title": "CHUNITHM PARADISE LOST"})
    cn["songs"][0]["difficulties"][1]["release_date"] = "2020-01-01"
    cn["songs"][0]["aliases"] = ["CN fixture alias"]
    cn["songs"][1]["version"] = 24000
    cn["songs"][1]["difficulties"][0]["version"] = 24000
    cn["songs"].append({
        "id": 1086, "title": "祈 -我ら神祖と共に歩む者なり-", "version": 15500,
        "artist": "光吉猛修 VS 穴山大輔 VS Kai VS 水野健治 VS 大国奏音",
        "difficulties": [{
            "difficulty": 3, "version": 15500, "level": "15+", "level_value": 15.7,
            "note_designer": '譜面ボーイズからの祈り -Pray for "JUSTICE"-',
            "notes": {"total": 4444, "tap": 1916, "hold": 432, "slide": 829, "air": 1131, "flick": 136},
        }],
    })
    paths.SONGLIST_PATH.write_text(json.dumps(cn), encoding="utf-8")
    paths.JACKET_DIR.mkdir()
    (paths.JACKET_DIR / "10.png").write_bytes(b"fixture CN jacket")
    paths.CACHE_DIR.mkdir(exist_ok=True)
    cache_file = paths.CACHE_DIR / "public-cache"
    cache_file.write_bytes(b"disposable CN cache fixture")

    def cn_hashes():
        files = [paths.SONGLIST_PATH, paths.JACKET_DIR / "10.png", cache_file, legacy_rating.HISTORY_PATH]
        return [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]

    async def search_regression():
        result = await song.search("CN fixture alias")
        assert result["code"] == "OK", result
        assert result["data"]["id"] == 10
        assert result["data"]["title"] == "Same title"
        assert result["data"]["related_song_ids"] == [8001, 8002]
        assert [(r["song_id"], r["difficulty"], r["level_value"]) for r in result["data"]["charts"]] == [
            (10, 3, 14.2), (10, 4, 15.1), (8001, 5, 0), (8002, 5, 0),
        ]
        assert (await song.aliases("10"))["data"]["aliases"] == ["CN fixture alias"]
        assert not native_song.find_song_matches("JP fixture alias", exact_only=True)
        assert (await song.detail(10))["data"] == result["data"]
        catalog = Catalog(read_catalog(paths.SONGLIST_PATH))
        assert set(catalog.songs) == {10, 11, 1086, 8001, 8002, 8003}
        return result

    settings = Settings("fixture", "secret", "https://example.test/oauth/lxns/callback",
                        Fernet.generate_key().decode())
    store = OAuthStore(paths.OAUTH_DIR, settings.encryption_key)
    oauth = OAuthService(settings, store)
    oauth_runtime._service = oauth
    now = time.time()
    store.begin("1:7", "fixture-grant", "fixture-state", {}, now)
    request, _ = store.claim("fixture-state", now)
    issuer = "https://maimai.lxns.net"
    store.finish(request, {
        "access_token": "fixture-token", "refresh_token": "fixture-refresh",
        "expires_at": now + 900, "subject": "fixture-subject", "issuer": issuer,
        "config": settings.fingerprint, "confirm_hash": hashlib.sha256(b"proof").hexdigest(),
        "confirm_expires": now + 300,
    }, store.subject_key("1", issuer, "fixture-subject"), now)
    store.confirm("1:7", "proof", now)

    person, scores = personal()
    scores.append(dict(scores[0], id=1086, rating=18.0))
    scores.append(dict(scores[0], id=11, rating=14.1))
    api_calls = []
    endpoints = {"/api/v0/user/chunithm/player": person, "/api/v0/user/chunithm/player/scores": scores}

    async def provider(request):
        assert request.method == "GET" and request.url.host == "maimai.lxns.net"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        api_calls.append(request.url.path)
        return httpx.Response(200, json={"success": True, "code": 200, "data": endpoints[request.url.path]})

    reader = player_data.PlayerData(oauth, transport=httpx.MockTransport(provider), clock=lambda: 100)
    player_data._reader = reader
    sent = []
    image_sent = asyncio.Event()

    class BotFixture(Bot):
        async def call_api(self, api, **kwargs):
            message = Message(kwargs.get("message", ""))
            sent.extend(message)
            if any(segment.type == "image" for segment in message):
                image_sent.set()
            return {"message_id": len(sent)}

    bot = BotFixture(SimpleNamespace(config=nonebot.get_driver().config, get_name=lambda: "OneBot V11"), "1")
    requester = event()
    message_ids = count(200)

    async def native_regression():
        for text in ("/查歌 CN fixture alias", "CN fixture alias是什么歌",
                     "/chu成绩 CN fixture alias", "/b30", "/b50", "/旧框"):
            native_event = requester.model_copy(update={
                "message_id": next(message_ids), "message": Message(text),
                "original_message": Message(text), "raw_message": text,
            })
            image_count = sum(segment.type == "image" for segment in sent)
            image_sent.clear()
            await handle_event(bot, native_event)
            await asyncio.wait_for(image_sent.wait(), timeout=5)
            assert sum(segment.type == "image" for segment in sent) == image_count + 1, (text, sent)

    async def player_regression():
        results = {}
        for capability, arguments in (
            ("player.best", {}), ("player.b50", {}), ("player.legacy_rating", {}),
            ("player.song_scores", {"query": "CN fixture alias"}),
        ):
            result = await player.query(bot, requester, capability, arguments)
            assert result["code"] == "OK", result
            data = result["data"]
            data.pop("cache_hit")
            results[capability] = data
        assert results["player.best"]["b30_average"] == 2.16
        assert results["player.b50"]["rating_estimate"] == 1.296
        assert results["player.b50"]["best30_count"] == 3
        assert results["player.b50"]["new20_count"] == 1
        legacy = results["player.legacy_rating"]
        assert len(legacy_rating.load_history()) == 3048 and legacy["chart_count"] == 1
        assert (legacy["version"], legacy["b30_average"], legacy["repeat_best"], legacy["top_ten"]) == (
            "PARADISE LOST", "0.5367", "4.4275", "0.8050",
        )
        rows = results["player.song_scores"]["scores"]
        assert [(r["id"], r["level_index"]) for r in rows] == [(10, 3), (10, 4), (8001, 5)]
        assert rows[0]["rating"] == 16.35 and rows[0]["future_score"] == {"retained": True}
        return results

    def history_unavailable():
        try:
            HistoryCatalog()
        except (sqlite3.Error, ValueError):
            return
        raise AssertionError("invalid Versioned Catalog was opened")

    async def exercise():
        before = cn_hashes()
        history_unavailable()
        baseline = await search_regression()
        snapshot = await reader.get(bot, requester, "player.best")
        assert snapshot.player == person and snapshot.scores == scores and not snapshot.cache_hit
        player_before = await player_regression()
        await native_regression()
        if mode == "cycle":
            first, second = (stage_versioned_release(versioned, suffix, value)
                             for suffix, value in [("old", 123), ("new", 138)])
            pinned = None
            for release, value in [(first, 123), (second, 138), (first, 123)]:
                activate(versioned, release)
                active = HistoryCatalog()
                chart, = active.get_snapshot("JP", "VERSE")["charts"]
                assert active.release_id == release and chart["constant_tenths"] == value
                assert chart["song_id"] != "10"
                assert active.find_charts("JP fixture alias")[0]["song_id"] == chart["song_id"]
                if pinned is None:
                    pinned = active
                assert pinned.release_id == first
                assert (await search_regression()) == baseline
                assert (await player_regression()) == player_before
                await native_regression()
                cache = await reader.get(bot, requester, "player.best")
                assert cache.cache_hit and cache.binding == snapshot.binding
                assert cache.player == snapshot.player and cache.scores == snapshot.scores
                assert await oauth.binding_matches(bot.self_id, requester.user_id, snapshot.binding)
                denied = await player.query(bot, event(8), "player.best", {})
                assert denied["code"] == "NOT_AUTHORIZED"
        assert api_calls == list(endpoints)
        assert cn_hashes() == before
        await sync.stop_workers()

    with ExitStack() as mocks:
        for module, name in ((native_song, "download_jacket"), (player, "player_images"),
                             (best_rating, "prepare_jackets")):
            mocks.enter_context(patch.object(module, name, AsyncMock(return_value={})))
        for module, name in ((native_song, "render_song_image"), (song_images, "render_song_scores"),
                             (best_rating, "draw_b30_image"), (best_rating, "draw_b50_image"),
                             (best_rating, "draw_legacy_rating_image")):
            mocks.enter_context(patch.object(module, name, return_value=b"fixture rendered bytes"))
        asyncio.run(exercise())


class VersionedCatalogIsolationTests(unittest.TestCase):
    def assert_host(self, mode):
        with TemporaryDirectory() as temp:
            shutil.copytree(ROOT / PACKAGE, Path(temp) / PACKAGE,
                            ignore=shutil.ignore_patterns("data", "__pycache__"))
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("LOCALSTORE_", "LXNS_OAUTH_"))}
            env.update(ENVIRONMENT="catalog-isolation-test", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join(str(ROOT / name) for name in ("tests", "tools")))
            script = f"from test_versioned_catalog_isolation import run_host; run_host({mode!r})"
            result = subprocess.run([sys.executable, "-c", script], cwd=temp, env=env,
                                    capture_output=True, encoding="utf-8", timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_switch_and_rollback_leave_cn_and_oauth_unchanged(self):
        self.assert_host("cycle")

    def test_startup_and_native_search_survive_corrupt_and_incompatible_releases(self):
        for mode in ("corrupt", "incompatible"):
            with self.subTest(mode=mode):
                self.assert_host(mode)
