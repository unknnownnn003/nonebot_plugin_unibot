"""Revision-bound cross-region score mapping and JP Rating simulation."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import json

from . import jp_history, legacy_rating, rating


@dataclass(frozen=True)
class Mapping:
    cn_id: int
    slot: int
    cn_fingerprint: str
    jp_identity: str
    notation_sha256: str
    evidence: str


def read_mappings(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("verified"), list):
        raise ValueError("Invalid JP score mapping manifest")
    mappings = []
    for entry in payload["verified"]:
        try:
            row = Mapping(**entry)
            valid = (type(row.cn_id) is int and row.cn_id >= 0 and type(row.slot) is int and 0 <= row.slot <= 4
                     and jp_history.HASH.fullmatch(row.cn_fingerprint)
                     and jp_history.HASH.fullmatch(row.notation_sha256)
                     and row.jp_identity.strip() and row.evidence.strip())
        except (TypeError, AttributeError) as error:
            raise ValueError("Invalid JP score mapping entry") from error
        if not valid:
            raise ValueError("Invalid JP score mapping entry")
        mappings.append(row)
    return mappings


def partition(edition, chart):
    # An earlier playable observation wins for old songs, including re-releases
    # and songs that later gain ULTIMA. An inferred package date alone cannot
    # qualify a song for NEW20.
    if chart["seen_before"]:
        return "old"
    day = chart["released_on"]
    if not day or chart["release_basis"] in {"package_date_assumption", "disk_presence_only",
                                               "tutorial_package_observation_day"}:
        return None
    if day > edition["end_on"]:
        return None
    return "old" if day < edition["start_on"] else "new"


def inventory(history, edition):
    return history._rows("""WITH prior AS (
            SELECT DISTINCT m.identity FROM versions v JOIN music_versions m USING(version)
            WHERE v.ordinal < ? AND m.state='present'
        ), releases AS (
            SELECT e.identity, min(e.effective_on) AS day FROM history_events e
            JOIN history_entities h USING(identity)
            WHERE h.kind='song' AND e.kind='released' AND e.effective_on IS NOT NULL GROUP BY e.identity
        )
        SELECT c.*,m.jacket_sha256,m.release_tag,r.revision,
            json_extract(r.payload,'$.notation_sha256') AS notation_sha256,
            coalesce(f.exact_on,CASE WHEN f.identity IS NULL THEN releases.day END) AS released_on,
            coalesce(f.basis,'historical_release_event') AS release_basis, prior.identity IS NOT NULL AS seen_before
        FROM chart_versions c JOIN music_versions m USING(version,identity)
        JOIN versions v USING(version)
        JOIN history_observations o ON o.batch='end:'||c.version AND o.identity=c.chart_identity
        JOIN history_revisions r USING(revision)
        LEFT JOIN prior ON prior.identity=m.identity
        LEFT JOIN sdhd_first_release f ON f.identity=m.identity
        LEFT JOIN releases ON releases.identity=m.identity
        WHERE c.version=? AND c.slot BETWEEN 0 AND 4 AND c.enabled=1
          AND c.state='present' AND m.state='present' AND coalesce(m.disable_flag,0)=0
          AND c.constant_status IN ('hdd_observed','hdd_inherited') AND c.selected_hundredths IS NOT NULL
          AND EXISTS (SELECT 1 FROM history_windows w WHERE w.version=c.version AND w.identity=c.chart_identity
              AND v.end_on BETWEEN w.start_on AND w.end_on AND w.availability IN ('present','inferred_present')
              AND w.revision=o.revision)
        ORDER BY c.chart_identity""", (edition["ordinal"], edition["version"]))


class MappingIndex:
    def __init__(self, history, edition, charts, approved):
        self.charts = {row["chart_identity"]: row for row in charts}
        self.candidates = defaultdict(set)
        self.approved = defaultdict(set)
        version = edition["version"]
        evidence = history.score_mappings(version) + history.cn_mapping_candidates(version)
        # Before PARADISE LOST the same canonical identities can still be
        # searched as leads. These rows never establish revision equivalence.
        if version != legacy_rating.VERSION and edition["ordinal"] <= history.by_name[legacy_rating.VERSION]["ordinal"]:
            evidence += history.score_mappings(legacy_rating.VERSION)
        for row in evidence:
            if row["cn_music_id"] is not None and row["chart_identity"] in self.charts:
                self.candidates[row["cn_music_id"], row["cn_slot"]].add(row["chart_identity"])
        self.owners = defaultdict(set)
        for row in approved:
            key = row.cn_id, row.slot
            chart = self.charts.get(row.jp_identity)
            if chart:
                self.candidates[key].add(row.jp_identity)
                if row.notation_sha256 == chart["notation_sha256"] and row.slot == chart["slot"]:
                    self.approved[key].add(row)
                    self.owners[row.jp_identity].add(key)

    def resolve(self, key, fingerprints):
        identities = self.candidates.get(key, set())
        approved = {row for row in self.approved.get(key, ()) if row.cn_fingerprint in fingerprints}
        approved_ids = {row.jp_identity for row in approved}
        if len(fingerprints) == 1 and len(approved_ids) == 1:
            identity = next(iter(approved_ids))
            if len(self.owners[identity]) == 1:
                return "verified", self.charts[identity]
            return "conflict", None
        if len(identities) > 1 or (identities and len(fingerprints) > 1):
            return "conflict", None
        return ("candidate" if identities else "unknown"), None


def select(history, edition, snapshot, catalog, approved):
    from ..best_rating import normalize_fc, score_to_rank

    b50 = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
    modern = edition["ordinal"] >= history.by_name["NEW"]["ordinal"]
    records = {}
    for record in snapshot.scores:
        key = record["id"], record["level_index"]
        if 0 <= key[1] <= 4 and (key not in records or record["score"] > records[key]["score"]):
            records[key] = record
    index = MappingIndex(history, edition, inventory(history, edition), approved)
    coverage = Counter(verified=0, candidate=0, conflict=0, unknown=0, unclassified=0, high_unresolved=0)
    ranked = {"old": [], "new": []}
    for key, record in records.items():
        song = catalog.songs.get(key[0])
        fingerprints = ([legacy_rating.chart_identity(song.attributes, chart.attributes)
                         for chart in song.charts if chart.difficulty == key[1] and rating.eligible(song, chart)]
                        if song else [])
        state, chart = index.resolve(key, fingerprints)
        coverage[state] += 1
        pool = partition(edition, chart) if b50 and chart else "old"
        score = record["score"]
        if chart and pool is None:
            coverage["unclassified"] += 1
        if chart is None or pool is None:
            coverage["high_unresolved"] += score >= 1_007_500
            continue
        constant = jp_history.hdd_constant(chart)
        ranked[pool].append(dict(
            id=chart["music_id"], chart_identity=chart["chart_identity"], revision=chart["revision"],
            jacket_id=chart["jacket_sha256"], song_name=chart["title"], level_index=chart["slot"],
            level_value=constant, score=score, clear=record.get("clear"),
            full_combo=normalize_fc(record.get("full_combo")),
            rank=score_to_rank(score) if modern else legacy_rating.score_rank(score),
            rating=(rating.chart_rating if modern else legacy_rating.chart_rating)(constant, score),
        ))
    for items in ranked.values():
        items.sort(key=lambda row: (-row["rating"], -row["score"], row["chart_identity"]))
    sample = len(ranked["old"]) + len(ranked["new"])
    return ranked["old"][:30], ranked["new"][:20], dict(coverage, score_count=len(records),
        sample=sample, unresolved=len(records) - sample)


def validate_request(history, version, *, b50):
    edition = history.parse_version(version)
    if not edition:
        return None, "未识别日服版本，请使用数据库中的正式版本名。"
    expected_b50 = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
    if b50 != expected_b50:
        command = "b50" if expected_b50 else "b30"
        return None, f"{edition['version']} 请使用 /{command} {edition['version']}。"
    return edition, None


async def render(snapshot, catalog, history, edition, *, b50):
    from .. import best_rating
    from .image_components import player_images
    from .job_store import async_call, async_render
    from .paths import HISTORY_DIR
    from .results import image, outcome

    try:
        approved = await async_call(read_mappings, HISTORY_DIR / "score_mappings.json")
        old, new, data = await async_call(select, history, edition, snapshot, catalog, approved)
    except jp_history.HISTORY_ERRORS:
        return outcome("JP_HISTORY_UNAVAILABLE", "日服 HDD 历史库或谱面映射清单校验失败。", status="unavailable")
    data.update(version=edition["version"], cutoff=edition["end_on"], best30_count=len(old), new20_count=len(new))
    total = data["score_count"]
    percentage = data["verified"] / total if total else 0
    hint = (f"{edition['version']} · {edition['end_on']} · 可计算 {data['sample']}/{total} 张 · "
            f"映射覆盖 {percentage:.1%} · 候选 {data['candidate']} / 冲突 {data['conflict']} / "
            f"未知 {data['unknown']} / 未分区 {data['unclassified']} · 未纳入 SSS 及以上 {data['high_unresolved']} 张")
    if data["sample"] < 10 or (b50 and data["unresolved"] and (not old or not new)):
        return outcome("JP_HISTORY_INSUFFICIENT", "日服历史 Rating 数据不足。\n" + hint,
                       status="needs_input", data=data)
    if data["unresolved"]:
        hint += " · 部分模拟，结果可能偏低"
    assets = await player_images(snapshot.player)
    jackets = await async_call(lambda: {digest: jp_history.load_jacket(HISTORY_DIR, digest)
                                      for digest in {row["jacket_id"] for row in old + new}})
    if b50:
        data.update(best30_average=rating.average(old, 30), new20_average=rating.average(new, 20),
                    rating_estimate=rating.average(old + new, 50))
        content = await async_render(best_rating.draw_jp_b50, snapshot.player, old, new, jackets, assets, data, hint)
    else:
        summary = legacy_rating.simulate([row["rating"] for row in old])
        data.update(b30_average=str(summary.b30), repeat_best=str(summary.repeat_best), top_ten=str(summary.top_ten))
        content = await async_render(best_rating.draw_jp_b30, snapshot.player, old, jackets, assets, summary, data, hint)
    return outcome(hint=hint, data=data, artifacts=[image(content)])
