"""Verified Japanese launch calendar for the offline Versioned Catalog."""
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from history_import import identifier, js_array, offset


TOKYO = "Asia/Tokyo"
TIMEMACHINE_THROUGH = "2026-10-31"
JP_THROUGH = "2026-11-21"
MATE_OBSERVATION_NOTICE = "https://info-chunithm.sega.jp/13533/"

# The dates are SEGA's rollout dates in Japan. Region-specific launches live in
# history_regions.py. The order also defines the Time Machine generation index.
JP_RELEASES = (
    ("CHUNITHM", "2015-07-16", "https://www.sega.jp/topics/detail/151228_arcade_1/"),
    ("PLUS", "2016-02-04", "https://www.sega.jp/topics/detail/160204_arcade_2/"),
    ("AIR", "2016-08-25", "https://www.sega.jp/topics/detail/160825_arcade_1/"),
    ("AIR PLUS", "2017-02-09", "https://www.sega.jp/arcade/detail/chunithm-air-plus/"),
    ("STAR", "2017-08-24", "https://www.sega.jp/topics/detail/170824_arcade_1/"),
    ("STAR PLUS", "2018-03-08", "https://www.sega.jp/topics/detail/180308_arcade_1/"),
    ("AMAZON", "2018-10-25", "https://www.sega.jp/topics/detail/181025_arcade_3/"),
    ("AMAZON PLUS", "2019-04-11", "https://www.sega.jp/topics/detail/190411_arcade_1/"),
    ("CRYSTAL", "2019-10-24", "https://www.sega.jp/topics/detail/191024_5/"),
    ("CRYSTAL PLUS", "2020-07-16", "https://www.sega.jp/arcade/detail/chunithm-crystal-plus/"),
    ("PARADISE", "2021-01-21", "https://chunithm.sega.jp/news/2021-01-21/"),
    ("PARADISE LOST", "2021-05-13", "https://www.sega.jp/topics/detail/210513_2/"),
    ("NEW", "2021-11-04", "https://www.sega.jp/arcade/detail/chunithm-new/"),
    ("NEW PLUS", "2022-04-14", "https://info-chunithm.sega.jp/1820/"),
    ("SUN", "2022-10-13", "https://info-chunithm.sega.jp/3043/"),
    ("SUN PLUS", "2023-05-11", "https://info-chunithm.sega.jp/4841/"),
    ("LUMINOUS", "2023-12-14", "https://info-chunithm.sega.jp/6208/"),
    ("LUMINOUS PLUS", "2024-06-20", "https://info-chunithm.sega.jp/7346/"),
    ("VERSE", "2024-12-12", "https://info-chunithm.sega.jp/8704/"),
    ("X-VERSE", "2025-07-16", "https://info-chunithm.sega.jp/10025/"),
    ("X-VERSE-X", "2025-12-11", "https://info-chunithm.sega.jp/10941/"),
    ("MATE", "2026-07-02", "https://info-chunithm.sega.jp/12155/"),
)
FIRST_DAY = JP_RELEASES[0][1]


def version_calendar(*, observed_through=JP_THROUGH, today=None):
    # Japan has used UTC+09 throughout the covered years. A fixed offset also
    # makes the offline builder work on Windows without the tzdata package.
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    observed = date.fromisoformat(observed_through)
    if observed.isoformat() != observed_through:
        raise ValueError("JP observation date must use YYYY-MM-DD")
    if not date.fromisoformat(TIMEMACHINE_THROUGH) <= observed <= today:
        raise ValueError("JP observation falls outside the accepted observation window")
    for index, (version, start, source) in enumerate(JP_RELEASES):
        next_release = JP_RELEASES[index + 1] if index + 1 < len(JP_RELEASES) else None
        end_before = next_release[1] if next_release else None
        if end_before is not None and start >= end_before:
            raise ValueError("JP releases must be strictly chronological")
        yield {
            "release_id": identifier("version", "JP", version),
            "region": "JP",
            "version": version,
            "generation": index + 1,
            "timezone": TOKYO,
            "start_on": start,
            "end_on": offset(end_before, -1) if end_before else None,
            "end_before": end_before,
            "observation_on": observed_through if not end_before else None,
            "status": "ended" if end_before else "current",
            "start_source_url": source,
            "next_start_source_url": next_release[2] if next_release else None,
            "observation_context_url": (
                MATE_OBSERVATION_NOTICE if not end_before and observed_through == JP_THROUGH else None
            ),
            "start_evidence": "official",
            "end_evidence": "next_official_launch_minus_one_day" if end_before else None,
        }


def jp_releases(db, data, *, observed_through=JP_THROUGH):
    # The independent launch dates are authoritative. GENS supplies the ordinal
    # of each source timeline node. Its explicit end fields are never used.
    generations = js_array(data, "GENS")
    if [(row["short"], row["start"]) for row in generations] != [
        (version, start) for version, start, _ in JP_RELEASES
    ]:
        raise ValueError("Time Machine GENS disagrees with the verified JP launch calendar")
    rows = list(version_calendar(observed_through=observed_through))
    for row in rows:
        index = row["generation"]
        start, end, version = row["start_on"], row["end_before"], row["version"]
        checkpoint = row["end_on"] if end else TIMEMACHINE_THROUGH
        rules = "old-r10" if index <= 12 else "new-r10" if index <= 18 else "verse-n20" if index == 19 else None
        locator = f"GENS[{index - 1}], verified at {row['start_source_url']}"
        db.release("JP", version, start, end, row["end_on"] or observed_through, TOKYO,
                   "history", "date-jp", locator, rules)
        db.editions[index] = (start, end, checkpoint, version)
        db.points.append(("JP", version, checkpoint, "version_end" if end else "observation"))


def write_version_calendar(path: Path, *, observed_through=JP_THROUGH):
    rows = list(version_calendar(observed_through=observed_through))
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
