"""Audit direct CN-to-JP ID coverage using a public LXNS catalog."""
import argparse
from collections import Counter
import json
from pathlib import Path

import import_paradise_lost  # Offline service bootstrap
from _unibot_history.jp_history import JpHistory
from _unibot_history.jp_rating import inventory, match_charts, partition
from _unibot_history.song_model import Catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--cn-catalog", type=Path, required=True)
    args = parser.parse_args()
    history = JpHistory(args.database)
    catalog = Catalog(json.loads(args.cn_catalog.read_text(encoding="utf-8-sig")))
    keys = {(song.id, chart.difficulty) for song in catalog.songs.values()
            for chart in song.charts if chart.difficulty < 5}
    print("| 版本 | JP 可计分谱面 | 同 ID/同难度匹配 | BEST30 池 | NEW20 池 | 待分区 | 冲突 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for edition in history.editions:
        charts = inventory(history, edition)
        slots = match_charts(charts)
        matches = [slots[key][0] for key in keys & slots.keys() if len(slots[key]) == 1]
        conflicts = sum(len(slots[key]) > 1 for key in keys & slots.keys())
        b50 = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
        pools = Counter(partition(edition, chart) for chart in matches) if b50 else {}
        values = (edition["version"], len(charts), len(matches), pools.get("old", 0) if b50 else "—",
                  pools.get("new", 0) if b50 else "—", pools.get(None, 0) if b50 else "—", conflicts)
        print("| " + " | ".join(map(str, values)) + " |")


if __name__ == "__main__":
    main()
