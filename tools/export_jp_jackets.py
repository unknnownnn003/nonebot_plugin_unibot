"""Export verified HDD jackets to a UniBot LocalStore historical_catalog directory."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

from PIL import Image

import import_paradise_lost  # Offline service bootstrap
from _unibot_history.jp_history import JpHistory, jacket_path


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export(history, output, roots, version=None, ids=()):
    rows = history.manifests(version, ids[0] if len(ids) == 1 else None)
    failures, exported, results = [], {}, []
    rows = [row for row in rows if not ids or row["music_id"] in ids]
    for number, row in enumerate(rows, 1):
        if number == 1 or number % 100 == 0:
            print(f"{number}/{len(rows)}", file=sys.stderr)
        digest = row["sha256"]
        path = jacket_path(output, digest)
        key = {field: row[field] for field in ("version", "identity", "revision", "music_id", "source", "path", "sha256")}
        if row["status"] != "matched" or not row["path"] or path is None:
            failures.append(key | {"error": row["status"]})
            continue
        if digest not in exported:
            checksum = path.with_suffix(".sha256")
            try:
                png_hash = sha256(path.read_bytes()).hexdigest()
                if checksum.read_text(encoding="ascii").strip() != png_hash:
                    raise ValueError("PNG checksum differs")
            except (OSError, ValueError):
                png_hash = None
                for root in roots:
                    try:
                        original = history.resource_bytes(root, row["path"], digest)
                        with Image.open(BytesIO(original), formats=["DDS"]) as image:
                            buffer = BytesIO()
                            image.convert("RGBA").save(buffer, format="PNG")
                        png = buffer.getvalue()
                    except (OSError, ValueError, KeyError, RuntimeError, BadZipFile, Image.DecompressionBombError):
                        continue
                    png_hash = sha256(png).hexdigest()
                    atomic_write(path, png)
                    atomic_write(checksum, (png_hash + "\n").encode("ascii"))
                    break
                if png_hash is None:
                    failures.append(key | {"error": "unreadable, invalid DDS or SHA-256 mismatch"})
                    continue
            exported[digest] = png_hash
        results.append(key | {"png_path": path.relative_to(output).as_posix(), "png_sha256": exported[digest]})
    return {"schema_version": 1, "records": results, "failures": failures, "unique_pngs": len(exported)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="LocalStore historical_catalog directory")
    parser.add_argument("--root", required=True, action="append", type=Path, help="SDBT/SDHD root, repeatable")
    parser.add_argument("--version")
    parser.add_argument("--id", dest="ids", type=int, action="append", default=[])
    args = parser.parse_args()
    history = JpHistory(args.database)
    version = None
    if args.version:
        edition = history.parse_version(args.version)
        if not edition:
            parser.error("Unknown version")
        version = edition["version"]
    output = args.output.resolve()
    roots = [root.resolve(strict=True) for root in args.root]
    if any(output.is_relative_to(root) for root in roots):
        parser.error("Output must be outside HDD trees")
    report = export(history, output, roots, version, args.ids)
    scope = (version or "all").replace(" ", "-")
    if args.ids:
        scope += "-" + sha256(",".join(map(str, sorted(set(args.ids)))).encode()).hexdigest()[:12]
    atomic_write(output / f"jacket-export-{scope}.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"{len(report['records'])} records, {report['unique_pngs']} unique PNGs, {len(report['failures'])} failures")
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
