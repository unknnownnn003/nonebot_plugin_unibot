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
            and not chart.attributes.get("disabled", False)
            and released(song.attributes, now) and released(chart.attributes, now))


def current_version(catalog):
    version = catalog.metadata.get("rating_version")
    versions = {v.get("version") for v in catalog.metadata.get("versions", []) if isinstance(v, dict)}
    if type(version) is not int or version not in versions:
        raise ValueError("Missing authoritative CN rating version")
    return version


RATING_CAP_SCORE = 1_009_000


def chart_rating(constant, score):
    """Unrounded goal Rating from the public LXNS settlement calculator."""
    # Lxns-Network/maimai-prober-frontend@fffe514, src/utils/chunithm/rating.ts
    constant = Decimal(str(constant))
    if score >= RATING_CAP_SCORE:
        value = constant + Decimal("2.15")
    elif score >= 1_007_500:
        value = constant + 2 + Decimal(score - 1_007_500) / 10_000
    elif score >= 1_005_000:
        value = constant + Decimal("1.5") + Decimal(score - 1_005_000) / 5_000
    elif score >= 1_000_000:
        value = constant + 1 + Decimal(score - 1_000_000) / 10_000
    elif score >= 975_000:
        value = constant + Decimal(score - 975_000) / 25_000
    elif score >= 925_000:
        value = constant - 3 + Decimal((score - 925_000) * 3) / 50_000
    elif score >= 900_000:
        value = constant - 5 + Decimal(score - 900_000) / 12_500
    elif score >= 800_000:
        value = (constant - 5) * Decimal(score - 700_000) / 200_000
    else:
        value = (constant - 5) * Decimal(max(0, score - 500_000)) / 600_000
    return max(Decimal(0), value)


def b50_partition(song_version, version, valid_versions):
    if type(song_version) is not int or song_version not in valid_versions or song_version > version:
        return None
    return "new" if song_version == version else "old"


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
        partition = b50_partition(row["song_version"], version, valid_versions)
        if partition is None:
            unknown += 1
        elif partition == "new":
            new.append(row)
        else:
            old.append(row)
    return old[:30], new[:20], unknown, version
