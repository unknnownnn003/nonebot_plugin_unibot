"""Build and publish a private, reproducible SQLite history package."""
import argparse
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile, TemporaryDirectory

# The existing import tool registers the pure services package without loading NoneBot.
from import_paradise_lost import ROOT, legacy
from _unibot_history.history import (
    CURRENT_REVISIONS, RELEASE_ID, SCHEMA_VERSION, coverage, iso_day, snapshot_rows,
)
from history_import import CatalogImport, identifier, json_file, json_text, sha
from history_jp import (
    jp_releases, load_checkpoint_otoge, load_lost, load_timemachine, load_verse_sheet, seed_rules,
)
from history_regions import (
    import_cn, import_intl_current, import_jp_current, import_official, import_otoge_metadata,
    release_observations,
)


TOOL_VERSION = "1.0.0"
TOOL_DIR = Path(__file__).resolve().parent
SCHEMA = TOOL_DIR / "history_schema.sql"
PINS = TOOL_DIR / "history_inputs.json"
IMPLEMENTATION = (
    Path(__file__), TOOL_DIR / "history_import.py", TOOL_DIR / "history_jp.py",
    TOOL_DIR / "history_regions.py", ROOT / "nonebot_plugin_unibot" / "services" / "history.py",
    TOOL_DIR / "import_paradise_lost.py", Path(legacy.__file__),
)


def load_inputs(lock, root):
    config = json_file(lock.read_bytes())
    artifacts = {row["id"]: row for row in config["artifacts"]}
    if len(artifacts) != len(config["artifacts"]):
        raise ValueError("Duplicate source artifact ID")
    raw = {}
    missing = []
    root = root.resolve()
    for name, meta in artifacts.items():
        if not RELEASE_ID.fullmatch(meta["sha256"]):
            raise ValueError(f"Invalid SHA-256 for {name}")
        if meta.get("observed_on") is not None:
            iso_day(meta["observed_on"])
        file = (root / meta["file"]).resolve()
        if not file.is_relative_to(root):
            raise ValueError(f"Artifact path escapes source directory: {name}")
        if file.is_file():
            raw[name] = checked_bytes(meta, file.read_bytes())
        else:
            missing.append((meta, file))
    return config, artifacts, raw, missing


def checked_bytes(meta, data):
    if sha(data) != meta["sha256"]:
        raise ValueError(f"Pinned SHA-256 mismatch for {meta['id']}")
    return data


async def fetch_missing(missing):
    # Offline builds use the standard library only; explicit fetch uses the repo's
    # existing HTTP client dependency and never fetches images or player data.
    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for meta, path in missing:
            if not meta["url"].startswith("https://"):
                raise ValueError(f"Only HTTPS sources are supported: {meta['id']}")
            response = await client.get(meta["url"])
            response.raise_for_status()
            content = checked_bytes(meta, response.content)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, content)


def atomic_write(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def format_json(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def input_manifest(config, artifacts, raw, schema):
    # The golden source is the existing point-in-time compatibility fixture.
    pinned = legacy.HISTORY_PATH.read_bytes()
    raw["legacy-package"] = pinned
    artifacts["legacy-package"] = {
        "id": "legacy-package", "source": "legacy-package",
        "file": "assets/historical/paradise_lost.json", "observed_on": "2021-11-02",
        "url": "https://github.com/unknnownnn003/nonebot_plugin_unibot/blob/974e92a917da06dec75b1522f607d42aec24c9ea/nonebot_plugin_unibot/assets/historical/paradise_lost.json",
        "upstream_version": "974e92a917da06dec75b1522f607d42aec24c9ea", "sha256": sha(pinned),
    }
    return {
        "schema_version": SCHEMA_VERSION, "schema_sha256": sha(schema), "tool_version": TOOL_VERSION,
        "tool_sha256": {path.name: sha(path.read_bytes()) for path in IMPLEMENTATION},
        "source_policies": config["sources"], "dependencies": sorted(config["dependencies"]),
        "artifacts": [dict(meta, byte_count=len(raw[name])) for name, meta in sorted(artifacts.items())],
        "regional_releases": config.get("regional_releases", []),
        "extra_observations": config.get("extra_observations", []),
        "distribution": "local-only",
    }


def write_sources(conn, manifest):
    conn.executemany("INSERT INTO source VALUES (?,?,?)",
                     [(key, *fields) for key, fields in sorted(manifest["source_policies"].items())])
    conn.executemany("INSERT INTO source_dependency VALUES (?,?,?)", manifest["dependencies"])
    conn.executemany("INSERT INTO source_artifact VALUES (?,?,?,?,?,?,?,?)", [
        (a["id"], a["source"], a["url"], a["sha256"], a["byte_count"], a["observed_on"],
         a["upstream_version"], TOOL_VERSION)
        for a in manifest["artifacts"]
    ])


def populate(db, raw, manifest):
    seed_rules(db)
    jp_releases(db, raw["date-jp"])
    release_observations(db)
    load_timemachine(db, raw["timemachine"])
    load_lost(db, raw["max-lost"], raw["legacy-package"])
    for artifact, region in (("oto-jp", "JP"), ("oto-deleted", "JP"), ("oto-intl", "INTL")):
        import_otoge_metadata(db, artifact, raw[artifact], region)
    for artifact, version, day in (
        ("oto-luminousplus", "LUMINOUS PLUS", "2024-12-11"), ("oto-verse", "VERSE", "2025-07-15"),
        ("oto-xverse", "X-VERSE", "2025-12-10"), ("oto-xversex", "X-VERSE-X", "2026-07-01"),
    ):
        load_checkpoint_otoge(db, artifact, raw[artifact], version, day)
    load_verse_sheet(db, raw["verse-sheet"])
    import_official(db, "official-jp", raw["official-jp"], "JP")
    import_official(db, "official-intl", raw["official-intl"], "INTL", observed_snapshot=True)
    import_jp_current(db, raw["jp-current"], raw["jp-we"])
    import_intl_current(db, raw["intl-current"])
    import_cn(db, raw["cn-current"], raw["cn-config"])
    append_observations(db, raw, manifest)
    db.materialize_intervals()


def append_observations(db, raw, manifest):
    for row in manifest["regional_releases"]:
        start = iso_day(row["start_on"])
        updated = db.conn.execute(
            """UPDATE region_release SET end_before=?
               WHERE region=? AND version=? AND end_before IS NULL AND start_on < ?""",
            (start, row["region"], row["previous_version"], start),
        )
        if updated.rowcount != 1:
            raise ValueError("The predecessor must be the sufficient, open regional release")
        db.release(row["region"], row["version"], start, None, None, row["timezone"],
                   "observation", row["artifact"], row["locator"])
    # Keep the original CN bridge and every earlier capture. Extra material
    # contributes a point observation with its own locked evidence and date.
    extra = sorted(manifest["extra_observations"], key=lambda row: (
        db.artifacts[row["catalog"]]["observed_on"], row["catalog"]
    ))
    for row in extra:
        artifact, version = row["catalog"], row["version"]
        day = db.artifacts[artifact]["observed_on"]
        if any(region == row["region"] and day <= observed for region, _, observed, _ in db.points):
            raise ValueError("Extra observations must be newer than the published checkpoints in their region")
        if row["region"] == "JP":
            we = row["companion"]
            import_jp_current(db, raw[artifact], raw[we], artifact=artifact, we_artifact=we, version=version)
        elif row["region"] == "INTL":
            import_intl_current(db, raw[artifact], artifact=artifact, version=version)
        elif row["region"] == "CN":
            config = row["companion"]
            import_cn(db, raw[artifact], raw[config], artifact=artifact, config_artifact=config,
                      version=version, baseline=False)
        else:
            raise ValueError("Unknown observation region")


def publish_snapshots(db, requested):
    conn = db.conn
    versions = []
    for region, version, day in requested:
        day = iso_day(day)
        known = conn.execute(
            "SELECT start_on,through_on FROM region_release WHERE region=? AND version=? AND kind='history'",
            (region, version),
        ).fetchone()
        already = any((region, version, day) == point[:3] for point in db.points)
        if already:
            continue
        if not known or not known["start_on"] <= day <= known["through_on"]:
            raise ValueError(f"No evidence for requested checkpoint {region} {version} {day}")
        db.points.append((region, version, day, "as_of"))
    for region, version, day, kind in sorted(db.points, key=lambda p: (p[0], p[2], p[1])):
        bounds = conn.execute(
            "SELECT start_on,end_before FROM region_release WHERE region=? AND version=?", (region, version)
        ).fetchone()
        if not bounds or day < bounds[0] or (bounds[1] is not None and day >= bounds[1]):
            raise ValueError(f"Checkpoint outside the regional release interval: {region} {version} {day}")
        sid = identifier("snapshot", region, version, day)
        rating = "legacy_reference_only" if (region, version, kind) == ("JP", "PARADISE LOST", "version_end") else "blocked"
        conn.execute("INSERT INTO snapshot VALUES (?,?,?,?,?,?,?)", (sid, region, version, day, kind, rating, "{}"))
        conn.execute(f"""INSERT INTO snapshot_chart(snapshot_id,revision_id,chart_from,song_from,event_id)
                        SELECT :snapshot, q.* FROM ({CURRENT_REVISIONS}) q
                        ORDER BY q.revision_id""", {"snapshot": sid, "day": day, "region": region})
        stats = coverage(snapshot_rows(conn, region, day, sid))
        if not stats["available_chart_count"]:
            raise ValueError(f"Empty chart checkpoint for {region} {version} {day}")
        stats["conflicts"] = dict(conn.execute(
            "SELECT status,count(*) FROM conflict WHERE region=? AND version=? GROUP BY status",
            (region, version),
        ).fetchall())
        conn.execute("UPDATE snapshot SET coverage_json=? WHERE id=?", (json_text(stats), sid))
        versions.append({"region": region, "version": version, "date": day, "kind": kind,
                         "rating_status": rating, **stats})
    return versions


def check_integrity(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise ValueError("Unexpected SQLite schema version")
    if tuple(conn.execute("PRAGMA integrity_check").fetchone()) != ("ok",):
        raise ValueError("SQLite integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("SQLite foreign-key check failed")
    for table, key in (("region_song_state", "song_id"), ("region_chart_state", "revision_id"),
                       ("resolved_event", "revision_id")):
        if conn.execute(f"""
            SELECT 1 FROM (
              SELECT valid_to, lead(valid_from) OVER (PARTITION BY region,{key} ORDER BY valid_from) AS next
              FROM {table}
            ) WHERE next IS NOT NULL AND (valid_to IS NULL OR next < valid_to) LIMIT 1
        """).fetchone():
            raise ValueError(f"Overlapping effective intervals in {table}")
    if conn.execute("""
        SELECT 1 FROM region_release a JOIN region_release b
        ON a.region=b.region AND a.start_on<=b.start_on AND a.version!=b.version
        WHERE a.end_before IS NULL OR b.start_on < a.end_before LIMIT 1
    """).fetchone():
        raise ValueError("Overlapping regional release windows")
    if conn.execute("""
        SELECT 1 FROM resolved_event e JOIN chart_revision r ON r.id=e.revision_id
        JOIN chart c ON c.id=r.chart_id WHERE c.difficulty='WE' LIMIT 1
    """).fetchone():
        raise ValueError("WE must not have a standard Rating constant")


def artifact_digest(path):
    return sha(path.read_bytes())


def create_database(path, artifacts, raw, manifest, release, requested):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
        with conn:
            conn.execute("INSERT INTO package VALUES (?,?,?,?)",
                         (release, TOOL_VERSION, json_text(manifest), manifest["schema_sha256"]))
            write_sources(conn, manifest)
            db = CatalogImport(conn, artifacts)
            populate(db, raw, manifest)
            versions = publish_snapshots(db, requested)
            check_integrity(conn)
            totals = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
                "song", "chart", "chart_revision", "observation", "resolved_event", "snapshot",
                "snapshot_chart", "jacket_asset", "external_id", "conflict", "resolution", "source_artifact",
            )}
            mappings = dict(conn.execute("SELECT status,count(*) FROM chart_mapping GROUP BY status").fetchall())
            if mappings.get("legacy_pinned") != 3048:
                raise ValueError("Baseline capture failed the 3,048-chart compatibility check")
            return {"release_id": release, "totals": totals, "mapping_counts": mappings,
                    "import_issues": dict(db.issues), "versions": versions,
                    "scope": "Observed only; no verified global denominator"}
    finally:
        conn.close()


def validate_package(folder):
    manifest = json_file((folder / "manifest.json").read_bytes())
    for name in ("catalog.sqlite", "coverage.json"):
        if artifact_digest(folder / name) != manifest["sha256"][name]:
            raise ValueError(f"History package checksum mismatch: {name}")
    uri = (folder / "catalog.sqlite").resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        check_integrity(conn)
        row = conn.execute("SELECT release_id,source_manifest FROM package").fetchone()
        if row != (manifest["release_id"], json_text(manifest["inputs"])):
            raise ValueError("History package metadata mismatch")
        if sha(json_text(manifest["inputs"]).encode("utf-8")) != folder.name or row[0] != folder.name:
            raise ValueError("History package ID does not cover the locked inputs")
    finally:
        conn.close()
    return manifest


def private_root(path):
    path = path.resolve()
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / ".tools"):
        raise ValueError("Keep private history artifacts outside Git or under ignored .tools/")
    return path


def build_package(source_root, lock, root, *, fetch=False, requested=(), activate=True):
    config, artifacts, raw, missing = load_inputs(lock, source_root)
    if missing and fetch:
        asyncio.run(fetch_missing(missing))
        config, artifacts, raw, missing = load_inputs(lock, source_root)
    if missing:
        raise ValueError("Missing locked artifacts: " + ", ".join(meta["id"] for meta, _ in missing))
    schema = SCHEMA.read_bytes()
    manifest = input_manifest(config, artifacts, raw, schema)
    manifest["requested_checkpoints"] = sorted(set(map(tuple, requested)))
    release = sha(json_text(manifest).encode("utf-8"))
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / release
    if not target.exists():
        with TemporaryDirectory(dir=releases, prefix=".building-") as temp:
            staged = Path(temp) / release
            staged.mkdir()
            report = create_database(staged / "catalog.sqlite", artifacts, raw, manifest, release,
                                     manifest["requested_checkpoints"])
            atomic_write(staged / "coverage.json", format_json(report))
            checksums = {name: artifact_digest(staged / name) for name in ("catalog.sqlite", "coverage.json")}
            atomic_write(staged / "manifest.json", format_json({
                "release_id": release, "inputs": manifest, "sha256": checksums,
            }))
            validate_package(staged)
            try:
                staged.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
    validate_package(target)
    if activate:
        atomic_write(root / "CURRENT", (release + "\n").encode("ascii"))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="private history package root")
    parser.add_argument("--sources", type=Path, help="directory holding the locked source files")
    parser.add_argument("--manifest", type=Path, default=PINS, help="source lock manifest")
    parser.add_argument("--fetch-missing", action="store_true", help="verify checksums before saving public sources")
    parser.add_argument("--checkpoint", nargs=3, action="append", metavar=("REGION", "VERSION", "YYYY-MM-DD"))
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true", help="validate the release selected by CURRENT")
    choice.add_argument("--activate", metavar="RELEASE_ID", help="validate and select an existing release")
    choice.add_argument("--stage-only", action="store_true", help="build without changing CURRENT")
    args = parser.parse_args()
    try:
        root = private_root(args.output)
        if args.check or args.activate:
            release = args.activate or (root / "CURRENT").read_text(encoding="ascii").strip()
            if not RELEASE_ID.fullmatch(release):
                raise ValueError("Invalid release ID")
            package = root / "releases" / release
            validate_package(package)
            if args.activate:
                atomic_write(root / "CURRENT", (release + "\n").encode("ascii"))
        else:
            if args.sources is None:
                parser.error("--sources is required to build")
            package = build_package(private_root(args.sources), args.manifest, root, fetch=args.fetch_missing,
                                    requested=args.checkpoint or (), activate=not args.stage_only)
        print(package)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
