"""Audit public JP inventory and mapping evidence without requesting player scores."""
import argparse
from collections import Counter
from pathlib import Path

import import_paradise_lost  # Offline service bootstrap
from _unibot_history.jp_history import JpHistory
from _unibot_history.jp_rating import MappingIndex, inventory, partition, read_mappings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--mappings", type=Path)
    args = parser.parse_args()
    history = JpHistory(args.database)
    approved = read_mappings(args.mappings) if args.mappings else []
    print("| 版本 | 截点 | 曲目身份 | 统计谱面 | 相容审核证据 | 候选 | 冲突 | 未知 | NEW20 池 | 待分区 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for edition in history.editions:
        name = edition["version"]
        charts = inventory(history, edition)
        index = MappingIndex(history, edition, charts, approved)
        keys = {}
        for key, identities in index.candidates.items():
            fingerprints = {row.cn_fingerprint for row in index.approved.get(key, ())}
            state, chart = index.resolve(key, fingerprints)
            for identity in identities:
                keys.setdefault(identity, set()).add(state if chart is None or chart["chart_identity"] == identity else "candidate")
        coverage = Counter(next((state for state in ("verified", "conflict", "candidate") if state in states), "unknown")
                           for states in (keys.get(row["chart_identity"], ()) for row in charts))
        modern = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
        pools = Counter(partition(edition, row) for row in charts)
        songs = len({row["identity"] for row in history.songs(name)})
        values = (name, edition["end_on"], songs, len(charts), coverage["verified"], coverage["candidate"],
                  coverage["conflict"], coverage["unknown"], pools["new"] if modern else "—", pools[None] if modern else "—")
        print("| " + " | ".join(map(str, values)) + " |")


if __name__ == "__main__":
    main()
