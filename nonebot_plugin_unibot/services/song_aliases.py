"""Alias source snapshots and their derived, ID-keyed search projection."""
import csv
from collections import defaultdict
from io import StringIO
import unicodedata

URLS = {
    "marisabot": "https://raw.githubusercontent.com/QingQiz/MarisaBot/master/Marisa.Frontend/public/assets/chunithm/aliases.tsv",
    **{f"gcm_{lang}": f"https://raw.githubusercontent.com/lomotos10/GCM-bot/main/data/aliases/{lang}/chuni.tsv"
       for lang in ("en", "ja")},
}


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def parse_tsv(content):
    reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), delimiter="\t", strict=True)
    rows = [row for row in reader if row]
    if not rows or not any(len(row) > 1 for row in rows) or any(not normalized(row[0]) for row in rows):
        raise ValueError("Invalid title alias snapshot")
    return rows


def project(songs, snapshots):
    titles = defaultdict(list)
    ids = set()
    for song in songs:
        sid = str(song["id"])
        titles[normalized(song["title"])].append(sid)
        ids.add(sid)
    entries = defaultdict(dict)
    pending = []
    for source, rows in snapshots.items():
        for key, *aliases in rows:
            targets = titles[normalized(key)] if source in URLS else ([key] if key in ids else [])
            if len(targets) != 1:
                pending.append((source, key, aliases, targets))
                continue
            for alias in aliases:
                norm = normalized(alias)
                if norm:
                    entry = entries[targets[0]].setdefault(norm, {"alias": alias, "sources": []})
                    if source not in entry["sources"]:
                        entry["sources"].append(source)
    return entries, pending


def local_rows(old):
    if "alias_sources" in old:
        return old["alias_sources"]["local"]
    return [[str(s["id"]), *s["aliases"]] for s in old.get("songs", [])
            if s.get("data_source") in (None, "lx", "lxns", "lxns_cn") and s.get("aliases")]


def publish(data, snapshots):
    entries, _ = project(data["songs"], snapshots)
    data["alias_sources"] = snapshots
    for song in data["songs"]:
        if song.get("data_source") not in (None, "lx", "lxns", "lxns_cn"):
            continue
        song["aliases"] = [v["alias"] for v in entries[str(song["id"])].values()]


def edit_local(data, sid, alias, remove):
    snapshots = data.get("alias_sources") or {"local": local_rows(data)}
    rows = snapshots["local"]
    row = next((r for r in rows if r[0] == sid), None)
    norm = normalized(alias)
    exists = row is not None and any(normalized(v) == norm for v in row[1:])
    if exists != remove:
        return False
    if remove:
        row[:] = [sid, *(v for v in row[1:] if normalized(v) != norm)]
    elif row is None:
        rows.append([sid, alias])
    else:
        row.append(alias)
    publish(data, snapshots)
    return True
