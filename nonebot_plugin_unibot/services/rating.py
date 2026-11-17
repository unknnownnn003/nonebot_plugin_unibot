"""CN chart eligibility and disjoint B30/NEW20 selection on the shared snapshot."""
from datetime import datetime, time, timezone, timedelta
from decimal import Decimal

CN = timezone(timedelta(hours=8))


def released(attributes, now=None):
    value = attributes.get("release_date")
    try:
        date = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return True  # Official contract: missing/invalid dates do not restrict.
    return (now or datetime.now(CN)) >= datetime.combine(date, time(7), CN)


def eligible(song, chart, now=None):
    return (chart.difficulty < 5 and not song.attributes.get("disabled", False)
            and released(song.attributes, now) and released(chart.attributes, now))


def current_version(catalog):
    version = catalog.metadata.get("rating_version")
    versions = {v.get("version") for v in catalog.metadata.get("versions", []) if isinstance(v, dict)}
    if type(version) is not int or version not in versions:
        raise ValueError("Missing authoritative CN rating version")
    return version


def average(items, divisor):
    return float(sum((Decimal(str(row["rating"])) for row in items), Decimal(0)) / divisor)


def select(snapshot, catalog, *, b50=False, now=None):
    from ..best_rating import build_chart_meta, build_item
    meta = build_chart_meta([s.as_dict() for s in catalog.songs.values()])
    allowed = {(str(s.id), c.difficulty) for s in catalog.songs.values() for c in s.charts if eligible(s, c, now)}
    items = [build_item(meta[(str(r["id"]), r["level_index"])], r) for r in snapshot.scores
             if (str(r["id"]), r["level_index"]) in allowed]
    items.sort(key=lambda row: (row["rating"], row["score"], row["id"], row["level_index"]), reverse=True)
    if not b50:
        return items[:30], [], 0, None
    version = current_version(catalog)
    # Song version decides new/old membership. Added ULTIMA keeps its own chart
    # version for display but can never turn its old parent song into a new song.
    old, new, unknown = [], [], 0
    valid_versions = {v.get("version") for v in catalog.metadata["versions"] if isinstance(v, dict)}
    for row in items:
        song_version = row["song_version"]
        if type(song_version) is not int or song_version not in valid_versions or song_version > version:
            unknown += 1
        elif song_version == version:
            new.append(row)
        else:
            old.append(row)
    return old[:30], new[:20], unknown, version
