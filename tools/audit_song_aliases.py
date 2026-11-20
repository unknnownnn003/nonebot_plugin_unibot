"""Audit downloaded public source files against a read-only local catalog."""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/services/song_aliases.py"
spec = importlib.util.spec_from_file_location("song_alias_audit", MODULE)
aliases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aliases)


def audit(args):
    def read(path):
        return json.loads(path.read_bytes())

    songs = read(args.songs)["songs"]
    local = aliases.local_rows(read(args.baseline))
    snapshots = {"lxns": [[str(r["song_id"]), *r["aliases"]] for r in read(args.lxns)["aliases"]],
                 "local": local}
    for source in aliases.URLS:
        snapshots[source] = aliases.parse_tsv(getattr(args, source).read_bytes())
    entries, pending = aliases.project(songs, snapshots)
    keys = {(sid, norm) for sid, items in entries.items() for norm in items}
    old = {(sid, aliases.normalized(value)) for sid, *values in local for value in values if aliases.normalized(value)}
    original, _ = aliases.project(songs, {k: snapshots[k] for k in ("lxns", "local")})
    original_keys = {(sid, norm) for sid, items in original.items() for norm in items}
    shared = Counter(norm for _, norm in keys)
    titles = {str(s["id"]): s["title"] for s in songs}
    report = ["# UniBot 国服别名验收", "", f"国服曲库 {len(songs)} 首。映射仅使用 NFKC、大小写和空白归一后的唯一标题或落雪原始 ID。",
              "", "|来源|原始别名条数|有有效别名的歌曲数|去重后关系数|", "|---|---:|---:|---:|"]
    for source, rows in snapshots.items():
        mapped, _ = aliases.project(songs, {source: rows})
        report.append(f"|{source}|{sum(len(r)-1 for r in rows)}|{len(mapped)}|{sum(map(len,mapped.values()))}|")
    duplicates = sum(len(entry["sources"]) > 1 for records in entries.values() for entry in records.values())
    gcm, _ = aliases.project(songs, {k:v for k,v in snapshots.items() if k.startswith("gcm_")})
    report += ["", f"GCM 英、日两表共 {sum(len(r)-1 for k in ('gcm_en','gcm_ja') for r in snapshots[k])} 条，覆盖 {len(gcm)} 首。",
               f"跨来源重复关系 {duplicates} 条。跨歌曲共享的标准化别名 {sum(n>1 for n in shared.values())} 个。",
               f"最终可搜索关系 {len(keys)} 条。相对原同步逻辑（同一国服曲库、当次 LXNS 加本地历史）新增 {len(keys-original_keys)} 条。",
               f"相对本地更新前曲库快照新增 {len(keys-old)} 条。本地历史可能含早期合并的落雪别名，来源无法追溯。",
               "Haruki 和 KndBot 不计入导入来源。", "", "## 标准化曲名与别名精确命中", ""]
    samples = ["雅迪", "鸟之诗", "空虚", max(titles.values(), key=len), shared.most_common(1)[0][0]]
    for term in dict.fromkeys(samples):
        norm = aliases.normalized(term)
        hits = [f"{sid} {title}" for sid,title in titles.items() if norm == aliases.normalized(title) or norm in entries.get(sid,{})]
        report.append(f"- {term}（{len(hits)} 首）　" + "、".join(hits))
    report += ["", "## 完整待映射记录", "", "每行依次为来源、原始键、所有别名和可能的国服 ID。空候选表示国服缺少完全匹配。", "", "~~~~jsonl",
               *(json.dumps(row, ensure_ascii=False) for row in pending), "~~~~", "",
               "各 TSV 均经严格解析，解析错误 0 条。单列仅记录标题，不产生别名关系。", "", "## 输入校验", ""]
    for key in ("songs","lxns","baseline",*aliases.URLS):
        report.append(f"- {key} SHA-256　" + hashlib.sha256(getattr(args,key).read_bytes()).hexdigest())
    args.output.write_text("\n".join(report)+"\n",encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("songs", "baseline", "lxns", *aliases.URLS, "output"):
        parser.add_argument("--"+name, required=True, type=Path)
    audit(parser.parse_args())
