"""Lossless LXNS CN catalog records and explicit-origin query projections.

No network, NoneBot imports, title-based identity inference, or persistent writes.
Legacy mixed files are projected in memory; their original bytes remain intact.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path

SOURCE = "lxns_cn"
SCHEMA_VERSION = 2
FOREIGN_FIELDS = {"chunirec_id", "chunirec_url", "rinnet_id", "chunirec_level", "chunirec_level_value"}


def numeric_id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("Invalid LXNS song ID")
    return int(value)


@dataclass(frozen=True)
class Chart:
    song_id: int
    index: int
    attributes: dict

    @property
    def difficulty(self):
        return self.attributes["difficulty"]

    @property
    def jacket_id(self):
        return (self.attributes.get("origin_id") or self.song_id) if self.difficulty == 5 else self.song_id

    def as_dict(self):
        return dict(deepcopy(self.attributes), song_id=self.song_id, chart_key=f"{self.song_id}:{self.difficulty}:{self.index}")


@dataclass(frozen=True)
class Song:
    id: int
    attributes: dict
    charts: tuple[Chart, ...]

    def as_dict(self):
        return dict(deepcopy(self.attributes), id=self.id, data_source=SOURCE,
                    difficulties=[chart.as_dict() for chart in self.charts])


class Catalog:
    def __init__(self, payload, *, legacy=False):
        if not isinstance(payload, dict) or not isinstance(payload.get("songs"), list):
            raise ValueError("Invalid LXNS catalog")
        self.metadata = {key: deepcopy(value) for key, value in payload.items() if key != "songs"}
        self.songs = {}
        self.excluded = 0
        for item in payload["songs"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid song record")
            if item.get("data_source") not in (None, "lxns", "lx", SOURCE):
                if legacy:
                    self.excluded += 1
                    continue
                raise ValueError("Foreign catalog source is not accepted")
            sid = numeric_id(item.get("id"))
            if sid in self.songs:
                raise ValueError("Duplicate LXNS song ID")
            if not isinstance(item.get("title"), str) or not isinstance(item.get("difficulties"), list):
                raise ValueError("Invalid song fields")
            attributes = {k: deepcopy(v) for k, v in item.items() if k not in FOREIGN_FIELDS | {"difficulties"}}
            charts = []
            standard_difficulties = set()
            for index, raw in enumerate(item["difficulties"]):
                if not isinstance(raw, dict) or type(raw.get("difficulty")) is not int or not 0 <= raw["difficulty"] <= 5:
                    raise ValueError("Invalid LXNS chart difficulty")
                if raw["difficulty"] < 5:
                    if raw["difficulty"] in standard_difficulties:
                        raise ValueError("Duplicate standard chart difficulty")
                    standard_difficulties.add(raw["difficulty"])
                chart = {k: deepcopy(v) for k, v in raw.items() if k not in FOREIGN_FIELDS | {"lx_level", "lx_level_value", "chart_key", "song_id"}}
                # Historical mixed files explicitly retained CN values in lx_*.
                if legacy:
                    for field in ("level", "level_value"):
                        if raw.get("lx_" + field) is not None:
                            chart[field] = raw["lx_" + field]
                value = chart.get("level_value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid LXNS chart constant")
                if chart.get("origin_id") is not None:
                    chart["origin_id"] = numeric_id(chart["origin_id"])
                charts.append(Chart(sid, index, chart))
            self.songs[sid] = Song(sid, attributes, tuple(charts))

    def as_dict(self):
        return dict(deepcopy(self.metadata), schema_version=SCHEMA_VERSION, source=SOURCE,
                    songs=[song.as_dict() for song in self.songs.values()])

    def parent_id(self, song_id):
        song = self.songs[song_id]
        # Only WE-only records with one explicit non-WE parent may be grouped.
        origins = {c.attributes.get("origin_id") for c in song.charts if c.difficulty == 5}
        if song.charts and all(c.difficulty == 5 for c in song.charts) and len(origins) == 1:
            origin = next(iter(origins))
            parent = self.songs.get(origin)
            if parent is not None and any(c.difficulty != 5 for c in parent.charts):
                return origin
        return song_id

    def query_song(self, song_id):
        """A display view only: preserve original record/chart IDs and versions."""
        sid = numeric_id(song_id)
        song = self.songs[sid]
        view = song.as_dict()
        related = [s for s in self.songs.values() if s.id != sid and self.parent_id(s.id) == sid]
        view["related_songs"] = [s.as_dict() for s in related]
        view["difficulties"] += [c.as_dict() for s in related for c in s.charts]
        parent = self.parent_id(sid)
        view["parent_song_id"] = parent if parent != sid else None
        return view


def active_catalog(payload):
    return Catalog(payload, legacy=True).as_dict()


def read_catalog(path):
    return active_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def jacket_id(song):
    identity = numeric_id(song["id"])
    owned = [chart for chart in song.get("difficulties", []) if chart.get("song_id", identity) == identity]
    for chart in owned:
        if chart["difficulty"] != 5:
            return identity
    return Chart(identity, 0, owned[0]).jacket_id if owned else identity


def chart_owner(view, chart):
    sid = numeric_id(chart.get("song_id", view["id"]))
    for song in [view] + view.get("related_songs", []):
        if numeric_id(song["id"]) == sid:
            return song
    raise ValueError("Chart owner is absent from query view")


def catalog_changes(old, new):
    """Count changes by song ID and by chart identity, including repeated WE rows."""
    counts = {f"{kind}_{action}": 0 for kind in ("songs", "charts")
              for action in ("added", "changed", "removed", "disabled")}

    def chart_groups(song):
        groups = defaultdict(list)
        for chart in song.charts if song is not None else ():
            attrs = chart.attributes
            key = (chart.difficulty,)
            if chart.difficulty == 5:
                key += (attrs.get("origin_id"), str(attrs.get("kanji")), str(attrs.get("star")))
            groups[key].append(attrs)
        return groups

    for sid in old.songs.keys() | new.songs.keys():
        before, after = old.songs.get(sid), new.songs.get(sid)
        before_disabled = bool(before and before.attributes.get("disabled"))
        after_disabled = bool(after and after.attributes.get("disabled"))
        old_charts, new_charts = chart_groups(before), chart_groups(after)
        changed = False

        def newly_disabled(old_attrs, new_attrs):
            return (not (before_disabled or old_attrs.get("disabled"))
                    and bool(after_disabled or new_attrs.get("disabled")))

        for key in old_charts.keys() | new_charts.keys():
            previous = old_charts[key]
            current = new_charts[key]
            # Match unchanged rows first so changes in upstream order do not count.
            previous, current = previous.copy(), current.copy()
            for attrs in previous.copy():
                if attrs in current:
                    previous.remove(attrs)
                    current.remove(attrs)
                    counts["charts_disabled"] += after_disabled and not before_disabled and not attrs.get("disabled")
            paired = min(len(previous), len(current))
            counts["charts_added"] += len(current) - paired
            counts["charts_removed"] += len(previous) - paired
            counts["charts_changed"] += paired
            counts["charts_disabled"] += sum(
                bool(after_disabled or new_chart.get("disabled")) and not (before_disabled or old_chart.get("disabled"))
                for old_chart, new_chart in zip(previous, current))
            changed |= bool(previous or current)
        if before is None:
            counts["songs_added"] += 1
        elif after is None:
            counts["songs_removed"] += 1
        else:
            counts["songs_disabled"] += after_disabled and not before_disabled
            ignored = {"id", "aliases", "data_source"}
            changed |= ({k: v for k, v in before.attributes.items() if k not in ignored}
                        != {k: v for k, v in after.attributes.items() if k not in ignored})
            counts["songs_changed"] += changed
    return counts
