"""Synthetic provenance, source replacement and conservative mapping tests."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/services/song_aliases.py"
spec = importlib.util.spec_from_file_location("fixture_aliases", path)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AliasTests(unittest.TestCase):
    def test_snapshot_replacement_and_local_ownership(self):
        songs = [{"id": 1, "title": "空♪"}, {"id": 2, "title": "Air"}]
        data = {"songs": songs}
        sources = {"lxns": [["1", "ＡＢ", "雅迪"]], "local": [],
                   "marisabot": [["空♪", "ab", "ab", "旧"], ["Air", "雅迪"]]}
        a.publish(data, sources)
        entries, pending = a.project(songs, sources)
        self.assertEqual(entries["1"]["ab"]["sources"], ["lxns", "marisabot"])
        self.assertFalse(pending)
        self.assertTrue(a.edit_local(data, "1", "ab", False))
        sources["marisabot"] = [["空♪", "追加"]]
        a.publish(data, sources)
        self.assertEqual(songs[0]["aliases"], ["ＡＢ", "雅迪", "追加"])
        self.assertEqual(songs[1]["aliases"], [])
        sources["lxns"] = []
        a.publish(data, sources)
        self.assertEqual(songs[0]["aliases"], ["ab", "追加"])
        self.assertTrue(a.edit_local(data, "1", "ＡＢ", True))
        self.assertEqual(songs[0]["aliases"], ["追加"])

    def test_unmapped_quotes_and_duplicate_titles(self):
        rows = a.parse_tsv('"""STAR""T"\t"空\t虚"\n重名\t★\n国服外\t✿'.encode())
        songs = [{"id": i, "title": title} for i, title in enumerate(['"STAR"T', "重名", "重名"], 1)]
        entries, pending = a.project(songs, {"marisabot": rows})
        self.assertEqual(entries["1"]["空 虚"]["alias"], "空\t虚")
        self.assertEqual([p[3] for p in pending], [["2", "3"], []])
        self.assertEqual([p[2] for p in pending], [["★"], ["✿"]])
        with self.assertRaises(ValueError):
            a.parse_tsv(b"")
