"""Import the pinned PARADISE LOST table and audit its LXNS chart mapping."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
# Import pure implementation without initializing NoneBot or LocalStore.
package = ModuleType("_unibot_history")
package.__path__ = [str(ROOT / "nonebot_plugin_unibot" / "services")]
sys.modules[package.__name__] = package
from _unibot_history import legacy_rating as legacy

SOURCE = f"https://github.com/MAX-eipi/ChunithmMusicDataTable/blob/{legacy.SOURCE_COMMIT}/index.html"
LXNS = "https://maimai.lxns.net/api/v0/chunithm/song/list?version=24000&notes=true"
# Only add historic-title-to-ID renames with independently verified identity.
REVIEWED_RENAMES = {}


class TableParser(HTMLParser):
    columns = ("title", "bas", "adv", "exp", "mas")

    def __init__(self):
        super().__init__()
        self.rows = []
        self.values = []
        self.column = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        for name in (dict(attrs).get("class") or "").split():
            if name.startswith("cell-") and name.removeprefix("cell-") in self.columns:
                self.column = name.removeprefix("cell-")
                self.parts = []

    def handle_data(self, data):
        if self.column:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.column is None or tag != ("div" if self.column == "title" else "li"):
            return
        if self.column != self.columns[len(self.values)]:
            raise ValueError("Unexpected historical table cell order")
        self.values.append("".join(self.parts).strip())
        self.column = None
        if len(self.values) == 5:
            title, *values = self.values
            if not all(re.fullmatch(r"\d{1,2}\.\d", value) for value in values):
                raise ValueError("Invalid historical constant precision")
            self.rows.append(dict(title=title, constants=[int(Decimal(value) * 10) for value in values]))
            self.values = []


def build(html, catalog):
    if hashlib.sha256(html).hexdigest() != legacy.SOURCE_SHA256:
        raise ValueError("Historical HTML differs from the pinned upstream snapshot")
    parser = TableParser()
    parser.feed(html.decode("utf-8"))
    parser.close()
    data = json.loads(catalog)
    songs = data.get("data", data)["songs"]
    if any(not isinstance(chart.get("notes"), dict)
           for song in songs for chart in song["difficulties"] if chart["difficulty"] in range(4)):
        raise ValueError("Use the public catalog with notes=true, as in the production refresh")
    titles, aliases, by_id = defaultdict(list), defaultdict(set), {}
    for song in songs:
        if not any(c["difficulty"] in range(4) for c in song["difficulties"]):
            continue
        sid = song["id"]
        if sid in by_id:
            raise ValueError("Duplicate LXNS song ID")
        by_id[sid] = song
        titles[legacy.normalize_title(song["title"])].append(sid)
        for alias in song.get("aliases", []):
            aliases[legacy.normalize_title(alias)].add(sid)
    candidates = {}
    for row in parser.rows:
        name = legacy.normalize_title(row["title"])
        direct = titles[name]
        reviewed = REVIEWED_RENAMES.get(row["title"])
        if reviewed is not None:
            candidates[name] = reviewed
        elif len(direct) == 1:
            candidates[name] = direct[0]
        elif direct or aliases[name]:
            row.update(status="title_conflict" if direct else "alias_needs_review",
                       candidate_ids=sorted(set(direct) | aliases[name]))
        else:
            row["status"] = "missing_in_cn"
    reuse = Counter(candidates.values())
    for row in parser.rows:
        sid = candidates.get(legacy.normalize_title(row["title"]))
        if sid is None:
            continue
        song = by_id[sid]
        if reuse[sid] > 1:
            row.update(status="title_conflict", candidate_ids=[sid])
            continue
        if type(song.get("version")) is not int or not 10000 <= song["version"] <= legacy.VERSION_ID:
            row.update(status="different_or_new_song", candidate_ids=[sid])
            continue
        identities = [None] * 4
        excluded = {}
        seen = set()
        for chart in song["difficulties"]:
            index = chart["difficulty"]
            if index not in range(4):
                continue
            if index in seen:
                raise ValueError(f"Duplicate LXNS chart {sid}/{index}")
            seen.add(index)
            if type(chart.get("version")) is int and 10000 <= chart["version"] <= legacy.VERSION_ID:
                identities[index] = legacy.chart_identity(song, chart)
            else:
                excluded[str(index)] = "unknown or post-LOST chart version"
        if any(identities):
            row.update(lxns_id=sid, identities=identities)
        else:
            row.update(status="no_eligible_charts", candidate_ids=[sid])
        if excluded:
            row["excluded_charts"] = excluded
    mapped_songs = sum("lxns_id" in row for row in parser.rows)
    mapped_charts = sum(identity is not None for row in parser.rows for identity in row.get("identities", ()))
    counts = Counter(row["status"] for row in parser.rows if "status" in row)
    payload = {
        "schema_version": 1, "version": legacy.VERSION,
        "source": {"url": SOURCE, "commit": legacy.SOURCE_COMMIT, "sha256": legacy.SOURCE_SHA256},
        "lxns_snapshot": {"url": LXNS, "sha256": hashlib.sha256(catalog).hexdigest()},
        "mapping": {"historical_songs": len(parser.rows), "historical_charts": 4 * len(parser.rows),
                    "mapped_songs": mapped_songs, "mapped_charts": mapped_charts, **dict(sorted(counts.items()))},
        "songs": parser.rows,
    }
    legacy.parse_history(payload)
    return payload


def encode(payload):
    header = json.dumps({k: v for k, v in payload.items() if k != "songs"}, ensure_ascii=False, indent=2)
    rows = ",\n".join("    " + json.dumps(row, ensure_ascii=False) for row in payload["songs"])
    return header[:-2] + ',\n  "songs": [\n' + rows + "\n  ]\n}\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, help="Pinned upstream index.html")
    parser.add_argument("--lxns", type=Path, help="LXNS public catalog snapshot (no personal data)")
    parser.add_argument("--check", action="store_true", help="Validate shipped data and compare regenerated data when inputs are provided")
    args = parser.parse_args()
    if bool(args.html) != bool(args.lxns) or not (args.html or args.check):
        parser.error("Supply both --html and --lxns, or use --check")
    if args.html:
        payload = build(args.html.read_bytes(), args.lxns.read_bytes())
        generated = encode(payload)
        if args.check:
            if legacy.HISTORY_PATH.read_text(encoding="utf-8") != generated:
                raise SystemExit("Historical snapshot differs from regenerated data")
        else:
            legacy.HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            legacy.HISTORY_PATH.write_text(generated, encoding="utf-8", newline="\n")
    else:
        payload = json.loads(legacy.HISTORY_PATH.read_text(encoding="utf-8"))
        legacy.parse_history(payload)
    ratings = [legacy.chart_rating(Decimal(c).scaleb(-1), 1_007_500)
               for row in payload["songs"] for c in row["constants"]]
    result = legacy.simulate(ratings)
    expected = (Decimal("15.9700"), Decimal("16.0025"), Decimal("15.9925"))
    actual = result.b30, result.repeat_best, result.top_ten
    if actual != expected or max(ratings) != Decimal("16.1000"):
        raise SystemExit(f"Historical theoretical limits differ: {actual}, B1={max(ratings)}")
    print(json.dumps(payload["mapping"], ensure_ascii=False))
    print(f"B30 {result.b30:.4f}, R10 {max(ratings):.4f}, B1 x 10 {result.repeat_best:.4f}, "
          f"B1-B10 {result.top_ten:.4f}, delta {result.difference:.4f}")


if __name__ == "__main__":
    main()
