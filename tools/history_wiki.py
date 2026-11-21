"""Import version-qualified JP WikiWiki facts into existing history slots."""
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from history_calendar import JP_RELEASES
from history_import import decimal_tenths, identifier, json_file, offset
from history_jp import HISTORY_BASIS


HISTORICAL_EDITIONS = tuple(row[0] for row in JP_RELEASES if row[0] != "MATE")
VERSION_NAMES = {
    **{name.upper().replace(" PLUS", "+"): name for name in HISTORICAL_EDITIONS},
    "無印": "CHUNITHM", "初代": "CHUNITHM", "PARADISE×": "PARADISE LOST",
    "PARADISE X": "PARADISE LOST", "X-VRS": "X-VERSE", "X-VRS-X": "X-VERSE-X",
}
HISTORY_RECORD = re.compile(r"譜面定数(?:&color\([^)]*\)\{)?(BAS|ADV|EXP|MAS|ULT)(?:\};)?\s*〖([^〗]*)〗", re.I)
EXACT_RANGE = re.compile(r"(\d{1,2}\.\d)\s*\(([^()]+)\)")


def parse_history_lines(lines):
    """Read explicit edition intervals from a song's history section."""
    records = []
    for line in lines:
        index, sep, text = line.partition(": ")
        if not sep or not re.fullmatch(r"L\d+", index) or text.lstrip().startswith("//"):
            continue
        for difficulty, values in HISTORY_RECORD.findall(text):
            for value, interval in EXACT_RANGE.findall(unicodedata.normalize("NFKC", values)):
                names = re.split(r"[~〜～]", interval.upper(), maxsplit=1)
                versions = [VERSION_NAMES.get(name.strip().replace(" PLUS", "+")) for name in names if name.strip()]
                if not versions or any(version is None for version in versions):
                    continue
                first = versions[0]
                last = versions[-1] if names[-1].strip() else HISTORICAL_EDITIONS[-1]
                if len(names) == 2 and not names[0].strip():
                    continue
                if HISTORICAL_EDITIONS.index(first) > HISTORICAL_EDITIONS.index(last):
                    continue
                records.append({"difficulty": difficulty.upper(), "first": first, "last": last,
                                "tenths": decimal_tenths(value), "locator": f"定数変更履歴 / {index}"})
    return records


def wiki_path(url):
    parts = urlsplit(url)
    path = unquote(parts.path)
    if (parts.scheme, parts.netloc) != ("https", "wikiwiki.jp") or not path.startswith("/chunithmwiki/"):
        raise ValueError("Expected a CHUNITHM WikiWiki source")
    return path


def import_wiki(db, raw, plan):
    editions = {version: (start, end) for start, end, _, version in db.editions.values()}
    for artifact in plan:
        source = db.artifacts[artifact]
        page = json_file(raw[artifact])
        path = wiki_path(source["url"])
        if wiki_path(page["url"]) != path:
            raise ValueError(f"Wiki observation URL mismatch: {artifact}")
        song, upstream = db.baseline[page["key"]]
        if page["title"] != upstream["title"] or wiki_path(upstream["wiki_url"]) != wiki_path(page["song_url"]):
            raise ValueError(f"Wiki song identity mismatch: {artifact}")
        title = upstream["title"]
        if path != wiki_path(page["song_url"]) and page["type"] != "edition_list":
            raise ValueError(f"Wiki song page does not match the pinned slot: {artifact}")
        for index, row in enumerate(page["records"], 1):
            difficulty = row["difficulty"]
            if difficulty not in ("BAS", "ADV", "EXP", "MAS", "ULT"):
                raise ValueError(f"Unknown difficulty: {artifact}")
            chart = identifier("chart", song, difficulty, "")
            revision = identifier("revision", chart, HISTORY_BASIS, None)
            first, last = row["first"], row["last"]
            start, end = editions[first][0], editions[last][1]
            if revision not in db.revisions or end is None or not start < end:
                raise ValueError(f"Invalid historical slot or version range: {artifact}")
            number = row["tenths"]
            if type(number) is not int or not 1 <= number <= 200:
                raise ValueError(f"Invalid Wiki exact constant: {artifact}")
            db.constant(artifact, f"{row['locator']} / #{index}", "JP", song, revision, row, number,
                        start=start, end=end, quality="A", kind="exact", grain="version",
                        value_kind="wiki_verified", priority=90)
        for version, difficulty in page.get("review_gaps", ()):
            if version not in editions:
                raise ValueError(f"Invalid Wiki review edition: {artifact}")
            chart = identifier("chart", song, difficulty, "")
            revision = identifier("revision", chart, HISTORY_BASIS, None)
            if revision not in db.revisions:
                raise ValueError(f"Invalid Wiki review slot: {artifact}")
            db.constant_reviews.add((version, revision))
        if "ultima_added_on" in page:
            # Dated chart additions are evidence separate from exact constants.
            from history_availability import ReleaseBatch, apply_release_batch

            day = page["ultima_added_on"]
            horizon = offset(source["observed_on"])
            apply_release_batch(db, ReleaseBatch(artifact, day, page["availability_locator"],
                                                 (page["key"],), quality="B"), horizon)
        if "removed_on" in page:
            day = page["removed_on"]
            db.record_name(song, title, "sdvx", artifact, page["availability_locator"])
            db.states(artifact, page["availability_locator"], "JP", song, title,
                      [(day, offset(source["observed_on"]), "removed", "B", "day", 50)])
