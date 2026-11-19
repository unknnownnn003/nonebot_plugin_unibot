"""Resource I/O regressions using isolated LocalStore paths and a mock CDN."""
import asyncio
import importlib
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image


PACKAGE = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot"
BROKEN = b"\x89PNG\r\n\x1a\nnot-a-decodable-image"


def png(color, size=(8, 8)):
    with Image.new("RGBA", size, color) as image, BytesIO() as stream:
        image.save(stream, "PNG")
        return stream.getvalue()


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = SimpleNamespace(DATA_DIR=self.root, JACKET_DIR=self.root / "jacket",
                                     SONGLIST_PATH=self.root / "songlist.json", ASSETS_DIR=self.root / "assets")
        pkg = ModuleType("resource_fixture")
        pkg.__path__ = [str(PACKAGE)]
        self.enterContext(patch.dict(sys.modules, {
            pkg.__name__: pkg, "resource_fixture.services.paths": self.paths,
        }))
        self.resources = importlib.import_module("resource_fixture.services.resources")
        self.tasks = importlib.import_module("resource_fixture.services.resource_tasks")
        self.maintenance = importlib.import_module("resource_fixture.services.maintenance")
        self.images = self.resources.public_images
        self.enterContext(patch.object(self.resources, "REQUEST_INTERVAL", 0))
        self.enabled = True
        self.status = "running"
        self.enterContext(patch.object(self.maintenance, "native_superuser", return_value=True))
        self.enterContext(patch.object(self.maintenance.group_policy, "enabled", side_effect=lambda *args: self.enabled))
        self.context = self.maintenance.RunContext(None, None, SimpleNamespace(job_id="test"),
            SimpleNamespace(get=AsyncMock(side_effect=lambda _: SimpleNamespace(status=self.status)), update=AsyncMock()))
        self.requests = []
        self.red = png("red")
        self.blue = png("blue")

    def cdn(self, send):
        async def handler(request):
            self.assertEqual(request.url.host, "assets2.lxns.net")
            self.assertNotIn("authorization", request.headers)
            self.requests.append(request.url.path)
            response = send(request.url.path.rsplit("/", 1)[1])
            return await response if asyncio.iscoroutine(response) else response

        factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return patch.object(self.resources.http_safety, "client", factory)

    def catalog(self, songs):
        self.paths.SONGLIST_PATH.write_text(json.dumps({"songs": songs}), encoding="utf-8")

    def store(self, kind, identity, content):
        path = self.resources.asset_path(kind, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    async def test_large_sync_can_resume_and_repairs_corrupt_files(self):
        self.catalog([dict(id=i, title=str(i), difficulties=[]) for i in range(1, 86)])
        self.store("jacket", 1, self.red)
        bad = self.store("jacket", 2, self.red[:33] + self.red[-12:])  # IHDR and IEND, no image data.

        self.interrupted = False

        def response(name):
            if name == "7.png" and not self.interrupted:
                self.interrupted = True
                self.status = "cancel_requested"
            return httpx.Response(200, content=self.blue)

        with self.cdn(response):
            with self.assertRaises(self.maintenance.Stopped):
                await self.tasks.refresh_jackets(self.context)
            self.assertEqual(bad.read_bytes(), self.blue)
            self.assertFalse(self.resources.asset_path("jacket", 7).exists())
            self.status = "running"
            self.requests.clear()
            output = await self.tasks.refresh_jackets(self.context)

        self.assertEqual(len(self.requests), 79)
        self.assertEqual(self.requests[0], "/chunithm/jacket/7.png")
        data = output["data"]
        self.assertEqual((data["total"], data["existing"], data["success"], data["missing"]), (85, 6, 79, 0))

        self.paths.JACKET_DIR.rename(self.root / "previous")
        with self.cdn(lambda _: httpx.Response(200, content=self.red)):
            self.requests.clear()
            output = await self.tasks.refresh_jackets(self.context)
        self.assertEqual(len(self.requests), 85)
        self.assertEqual(output["data"]["success"], 85)

    async def test_force_refresh_retains_existing_on_404_invalid_png_and_timeout(self):
        self.catalog([dict(id=i, title=str(i), difficulties=[]) for i in range(1, 5)])
        paths = [self.store("jacket", i, self.red) for i in range(1, 5)]

        def response(name):
            if name == "3.png":
                raise httpx.ReadTimeout("fixture")
            if name == "4.png":
                return httpx.Response(200, content=self.blue)
            if name == "2.png":
                return httpx.Response(404)
            return httpx.Response(200, content=BROKEN)

        with self.cdn(response):
            output = await self.tasks.refresh_jackets(self.context, full_refresh=True)
        self.assertEqual(output["status"], "partial")
        self.assertEqual([p.read_bytes() for p in paths], [self.red] * 3 + [self.blue])
        data = output["data"]
        self.assertEqual((data["existing"], data["failed"], data["not_found"], data["missing"]), (4, 3, 1, 0))
        self.assertFalse(list(self.paths.JACKET_DIR.glob("*.tmp")))

    async def test_429_retries_and_accurately_counts_deferred_missing(self):
        self.catalog([dict(id=i, title=str(i), difficulties=[]) for i in range(1, 5)])
        self.store("jacket", 4, self.red)

        def response(name):
            if len(self.requests) == 2:
                return httpx.Response(200, content=self.blue)
            return httpx.Response(429, headers={"Retry-After": "0"})

        with self.cdn(response):
            output = await self.tasks.refresh_jackets(self.context)
        self.assertEqual([p.rsplit("/", 1)[1] for p in self.requests], ["1.png", "1.png"] + ["2.png"] * 3)
        data = output["data"]
        self.assertEqual((output["code"], data["failed"], data["rate_limited"], data["deferred"], data["missing"]),
                         ("PARTIAL_MAINTENANCE", 1, 1, 1, 2))

    async def test_same_resource_download_is_shared_across_callers(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def response(_):
            entered.set()
            await release.wait()
            return httpx.Response(200, content=self.red)

        with self.cdn(response):
            owner = asyncio.create_task(self.images.get("jacket", 3))
            await entered.wait()
            waiter = asyncio.create_task(self.images.get("jacket", "03", force=True, context=self.context))
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(owner, waiter)
            cached = await self.images.get("jacket", 3)
        self.assertEqual([asset.content for asset in results] + [cached.content], [self.red] * 3)
        self.assertEqual(len(self.requests), 1)

    async def test_on_demand_budget_does_not_block_cached_jackets(self):
        self.store("jacket", 2, self.blue)

        async def response(_):
            await asyncio.Event().wait()

        with self.cdn(response), patch.object(self.resources, "DEMAND_WAIT_SECONDS", 0.1):
            results = await asyncio.wait_for(asyncio.gather(*(self.images.get("jacket", i) for i in range(1, 5))), 2)
        self.assertEqual([asset.content for asset in results], [None, self.blue, None, None])
        self.assertTrue(self.requests)
        self.assertNotIn("/chunithm/jacket/2.png", self.requests)
        self.assertFalse(self.images.failures)  # A later demand can warm the remaining jackets.

    async def test_maintenance_retries_rate_limit_from_concurrent_query(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def response(_):
            if len(self.requests) == 1:
                entered.set()
                await release.wait()
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, content=self.blue)

        with self.cdn(response):
            owner = asyncio.create_task(self.images.get("jacket", 3))
            await entered.wait()
            admin = asyncio.create_task(self.images.get("jacket", 3, force=True, context=self.context))
            await asyncio.sleep(0)
            release.set()
            self.assertEqual((await owner).status, "rate_limited")
            self.assertEqual((await admin).content, self.blue)
        self.assertEqual(len(self.requests), 2)

    async def test_waiter_survives_owner_cancellation_and_asset_path_is_reused(self):
        entered = asyncio.Event()

        async def response(_):
            if len(self.requests) == 1:
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, content=self.red)

        with self.cdn(response):
            owner = asyncio.create_task(self.images.get("jacket", 3))
            await entered.wait()
            waiter = asyncio.create_task(self.images.get("jacket", 3))
            await asyncio.sleep(0)
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner
            self.assertEqual((await waiter).content, self.red)
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.images.inflight)

    async def test_group_policy_revocation_blocks_next_request(self):
        self.catalog([dict(id=i, title=str(i), difficulties=[]) for i in range(1, 4)])

        def response(_):
            self.enabled = False
            return httpx.Response(200, content=self.red)

        with self.cdn(response):
            with self.assertRaises(self.maintenance.Stopped) as stopped:
                await self.tasks.refresh_jackets(self.context)
        self.assertEqual(stopped.exception.code, "FEATURE_DISABLED")
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.paths.JACKET_DIR.exists())

    async def test_chart_jacket_ids_include_ultima_and_all_explicit_we_sources(self):
        def chart(difficulty, **attrs):
            return dict(difficulty=difficulty, level_value=14, **attrs)

        self.catalog([dict(id=10, title="ULTIMA", origin_id=999, difficulties=[chart(4)]),
                      dict(id=8001, title="WE", difficulties=[chart(5, origin_id=10), chart(5, origin_id=11), chart(5)])])
        with self.cdn(lambda _: httpx.Response(200, content=self.red)):
            output = await self.tasks.refresh_jackets(self.context)
        self.assertEqual(output["data"]["total"], 3)
        self.assertEqual(self.requests, [f"/chunithm/jacket/{i}.png" for i in (10, 11, 8001)])

    async def test_player_assets_are_keyed_by_type_and_equipped_id(self):
        player = {"character": {"id": 1}, "map_icon": {"id": 1},
                  "name_plate": {"id": 1}, "trophy": {"id": 1}}
        self.store("icon", 1, self.blue)
        self.store("portrait", 1, self.blue)  # Too small to display as a portrait.
        with self.cdn(lambda name: httpx.Response(200, content=self.red) if name == "1.png"
                      else httpx.Response(404)):
            self.assertEqual(await self.resources.player_asset(player, "character"), self.red)
            self.assertEqual(await self.resources.player_asset(player, "icon"), self.blue)
            self.assertEqual(await self.resources.player_asset(player, "plate"), self.red)
            self.assertEqual(await self.resources.player_asset(player, "trophy"), self.red)
            self.assertIsNone(await self.resources.player_asset(player, "portrait"))
            self.assertIsNone(await self.resources.player_asset(dict(player, character={"id": 2}), "character"))
            self.assertIsNone(await self.resources.player_asset(dict(player, character={"id": 2}), "character"))
            self.assertIsNone(await self.resources.player_asset(dict(player, character={"id": "../2"}), "character"))
            self.assertEqual(await self.resources.player_asset(player, "character"), self.red)
        self.assertEqual(self.requests, ["/chunithm/character/1.png", "/chunithm/plate/1.png",
                                         "/chunithm/trophy/1.png", "/chunithm/character/2.png"])

    async def test_collection_maintenance_and_on_demand_use_the_same_asset_path(self):
        collection = SimpleNamespace(DATA_DIR=self.root, COLLECTIONS={"icon": "icons"},
                                     API_BASE_URL=self.resources.CDN, load_versions=lambda: [24000])

        def response(name):
            if name == "list":
                return httpx.Response(200, json={"icons": [{"id": 1}, {"id": 2}]})
            return httpx.Response(200, content=self.red if name == "1.png" else BROKEN)

        with patch.dict(sys.modules, {"resource_fixture.collection_update": collection}), self.cdn(response):
            output = await self.tasks.refresh_collections(self.context)
            self.assertEqual(await self.resources.player_asset({"map_icon": {"id": 1}}, "icon"), self.red)
            self.assertIsNone(await self.resources.player_asset({"map_icon": {"id": 2}}, "icon"))
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(output["status"], "partial")
        self.assertEqual((output["data"]["icon"]["success"], output["data"]["icon"]["failed"]), (1, 1))
        self.assertEqual(json.loads((self.root / "icon.json").read_text(encoding="utf-8")),
                         {"icons": [{"id": 1}, {"id": 2}]})
        self.assertFalse(self.resources.asset_path("icon", 2).exists())

    def test_static_asset_works_with_only_the_deployed_assets_directory(self):
        path = self.paths.ASSETS_DIR / "ui" / "rank" / "aaa.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.red)
        with self.resources.load_static_asset("rank", "aaa") as image:
            self.assertEqual((image.mode, image.size, image.getpixel((0, 0))), ("RGBA", (8, 8), (255, 0, 0, 255)))
        self.assertIsNone(self.resources.load_static_asset("../rank", "aaa"))
        self.assertIsNone(self.resources.load_static_asset("rank", "missing"))
