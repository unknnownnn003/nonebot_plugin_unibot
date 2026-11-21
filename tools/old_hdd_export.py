"""Cross-checkpoint event generation and deterministic audit exports."""
import csv
import json

from old_hdd_scan import encoded, file_hash


EXPORTS = {
    'music_versions': 'music_versions', 'chart_versions': 'chart_versions',
    'worlds_end_versions': 'worlds_end_versions', 'changes': 'chart_changes',
    'option_changes': 'option_changes', 'option_pairs': 'option_pairs',
    'jacket_manifest': 'jacket_manifest', 'jackets': 'jackets', 'sources': 'source_manifest',
    'packages': 'packages', 'paradise_split_evidence': 'paradise_split_evidence',
    'conflicts': 'conflicts', 'unmatched_records': 'unmatched_records',
    'score_mapping': 'score_mapping', 'references_used': 'references_used',
    'wiki_we_checks': 'wiki_we_checks', 'wiki_level_checks': 'wiki_level_checks',
    'raw_song_index': 'raw_song_index', 'raw_we': 'raw_we',
}

FEATURES = {
    'music_versions': ('title', 'artist', 'sort_name', 'genre', 'works', 'rights', 'release_tag', 'net_open',
                       'disable_flag', 'first_lock', 'cue', 'jacket_sha256', 'jacket_ref'),
    'chart_versions': ('hdd_hundredths', 'selected_hundredths', 'display_level', 'c2s_sha256', 'notes_designer'),
    'worlds_end_versions': ('tag', 'tag_id', 'stars', 'c2s_sha256', 'jacket_sha256', 'notes_designer'),
}

EVENT_NAME = {
    'hdd_hundredths': 'hdd_constant_observed_change', 'selected_hundredths': 'constant_changed', 'display_level': 'display_level_changed',
    'c2s_sha256': 'chart_content_changed', 'jacket_sha256': 'jacket_changed',
    'tag': 'we_attribute_changed', 'stars': 'we_stars_changed', 'title': 'title_changed', 'artist': 'artist_changed',
}


def changes(db):
    versions = [r[0] for r in db.execute('SELECT version FROM versions ORDER BY ordinal')]
    for table, fields in FEATURES.items():
        key = 'identity' if table == 'music_versions' else 'chart_identity'
        family = {'music_versions': 'song', 'chart_versions': 'chart', 'worlds_end_versions': 'we'}[table]
        old, seen, prior_version = {}, set(), None
        for version in versions:
            current = {r[key]: dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE version = ?', (version,))}
            for identity in sorted(old.keys() | current.keys()):
                before, after = old.get(identity), current.get(identity)
                representative = after if after is not None else before
                old_state, new_state = [x['state'] if x else 'not_observed' for x in (before, after)]

                def event(kind, field, a, b, certainty):
                    db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (prior_version, version, identity, representative['music_id'], representative['title'],
                                representative.get('difficulty', 'WE' if family == 'we' else None),
                                kind, field, encoded(a), encoded(b), before['source'] if before else None,
                                after['source'] if after else None, certainty))

                if new_state == 'present' and old_state != 'present':
                    if identity in seen and old_state in ('disabled', 'removed'):
                        kind, confidence = f'{family}_restored', 'dated_or_xml_state'
                    elif identity in seen:
                        kind, confidence = f'{family}_reobserved', 'source_presence'
                    else:
                        kind, confidence = f'{family}_first_observed', 'source_presence'
                    event(kind, 'state', old_state, new_state, confidence)
                if old_state == 'present' and new_state != 'present':
                    kind = f'{family}_{new_state}'
                    event(kind, 'state', old_state, new_state,
                          'dated_or_xml_state' if new_state in ('removed', 'disabled') else 'coverage_gap_possible')
                if old_state == new_state == 'present':
                    for field in fields:
                        a, b = before[field], after[field]
                        if a is None or b is None or a == b:
                            continue
                        inherited = ('inferred' in before.get('constant_status', '') or
                                     'inferred' in after.get('constant_status', ''))
                        confidence = 'inferred_transition' if inherited else 'observed_checkpoint_difference'
                        event(EVENT_NAME.get(field, 'metadata_changed'), field, a, b, confidence)
                if new_state == 'present':
                    seen.add(identity)
            prior_version, old = version, current


def csv_export(db, table, path, where=''):
    rows = db.execute(f'SELECT * FROM {table} {where} ORDER BY rowid')
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow([c[0] for c in rows.description])
        writer.writerows(rows)


def write(output, name, text):
    (output / name).write_text(text, encoding='utf-8', newline='\n')


def count(db, sql, args=()):
    return db.execute(sql, args).fetchone()[0]


def breakdown(db, sql, args=()):
    return dict(db.execute(sql, args))


def version_summary(db):
    results = []
    for version in db.execute('SELECT * FROM versions ORDER BY ordinal').fetchall():
        name = version['version']
        filters = {
            'ordinary_songs': "music_versions WHERE ex_type=0 AND state='present'",
            'ordinary_charts': "chart_versions WHERE state='present'",
            'worlds_end': "worlds_end_versions WHERE state='present'",
            'numeric_hdd': "chart_versions WHERE state='present' AND hdd_hundredths IS NOT NULL",
            'numeric_selected': "chart_versions WHERE selected_hundredths IS NOT NULL",
            'inferred_constants': "chart_versions WHERE constant_status='inferred_at_transition'",
            'unknown_membership': "music_versions WHERE state IN ('unknown','conflict')",
        }
        results.append(dict(version) | {key: count(db, f'SELECT count(*) FROM {where} AND version=?', (name,))
                                        for key, where in filters.items()})
    return results


def intersection(db, table, key, before, after):
    return count(db, f'''SELECT count(*) FROM {table} a JOIN {table} b USING({key})
                        WHERE a.version=? AND b.version=? AND a.state='present' AND b.state='present' ''',
                 (before, after))


def export(db, output, *, audit_csv=False):
    if audit_csv:
        for table, filename in EXPORTS.items():
            csv_export(db, table, output / f'{filename}.csv')
    versions = version_summary(db)
    write(output, 'versions.json', json.dumps(versions, ensure_ascii=False, indent=2) + '\n')
    report = ['# 旧框 HDD 提取结果', '', '| 版本 | 截点（JST） | 普通歌曲 | 普通谱面 | WE 谱面 | HDD 数值 | 采纳数值 | 曲目归属未知 |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in versions:
        report.append('| ' + ' | '.join(str(r[key]) for key in ('version', 'end_on', 'ordinary_songs',
                       'ordinary_charts', 'worlds_end', 'numeric_hdd', 'numeric_selected', 'unknown_membership')) + ' |')
    delta = breakdown(db, 'SELECT kind, count(*) FROM changes GROUP BY kind ORDER BY kind')
    constants = breakdown(db, "SELECT confidence,count(*) FROM changes WHERE kind='constant_changed' GROUP BY confidence")
    unknown = breakdown(db, 'SELECT kind, count(*) FROM conflicts GROUP BY kind ORDER BY kind')
    report.extend(['', f'普通有效谱面 HDD 原值合计 **{sum(r["numeric_hdd"] for r in versions):,}**。',
                   f'可选用数值 **{sum(r["numeric_selected"] for r in versions):,}**，其中 1.50 过渡推定 **{sum(r["inferred_constants"] for r in versions):,}**。',
                   '', '跨代事件见数据库 `changes`。同一谱面可以同时有定数、显示等级、内容等多类变化。',
                   '', '生命周期变动与来源见 `history_events`。',
                   '', f'定数变化按依据统计 `{encoded(constants)}`。', '', f'各事件计数 `{encoded(delta)}`。',
                   '', f'异常计数 `{encoded(unknown)}`。', '', '缺少前代或后代 XML 的事件以来源所见标记。停配或禁用另有状态。',
                   '', f'曲绘索引状态 `{encoded(breakdown(db, "SELECT status,count(*) FROM jacket_manifest GROUP BY status"))}`。',
                   f'所有 Music.xml 关联到的唯一曲绘字节内容共 {count(db, "SELECT count(*) FROM jackets"):,} 份。',
                   f'快照实际涉及 {count(db, "SELECT count(DISTINCT sha256) FROM jacket_manifest"):,} 份。'])
    write(output, 'EXTRACTION_REPORT.md', '\n'.join(report) + '\n')
    discovery_report(db, output)
    split_report(db, output, versions)
    validation_reports(db, output)
    write(output, 'SHA256SUMS', ''.join(f'{file_hash(p)}  {p.name}\n' for p in sorted(output.iterdir()) if p.is_file()))


def discovery_report(db, output):
    report = ['# 旧框 HDD 结构勘查', '', '扫描目标为 11 个解出的目录。HDD 目录名按用户提供对应版本。',
              '实际基础层为 `data/A000`，追加层为 `bin/option/Axxx` 的已解出目录。`bin/project.conf` 配置的机台挂载路径为 `C:/mount/Option/`。',
              '同根 VHD 只登记大小。XML 原始字节存于 `raw_xml`，来源、大小、哈希列于 `sources`。',
              '系统元数据及游戏二进制不参与解析。', '',
              '| HDD | 包（含 A000） | XML | Music.xml | C2S | 图片 | 包文件字节 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for row in db.execute("""SELECT hdd, count(DISTINCT package), sum(kind IN ('xml','music_xml')),
                           sum(kind='music_xml'),sum(kind='c2s'),sum(kind='image'),sum(bytes)
                           FROM sources WHERE package IS NOT NULL GROUP BY hdd ORDER BY hdd"""):
        report.append('| ' + ' | '.join(map(str, row)) + ' |')
    report += ['', '前 10 份使用 `data.conf` 的数据版本与包间交集。相同数据版本的相同副本只计一次。相同修订出现不同有效载荷时保留冲突。',
               '初代与 PLUS 的 XML 全部缺少 disableFlag。有效谱面依据该两代 schema、启用标记及 C2S 识别，原值仍为空。',
               '当没有可信生效日时，差异只记为包间观测。重复累积包缺少某 ID 不作删除证据。',
               'A032/A033 与公开停配日期逐条比较。A114 的复活内容和 A910 第三方内容保留原始来源。',
               '', '逐包 XML 数量、数据版本、来源身份及包间重叠详见 `packages`、`option_pairs` 和 `option_changes`。']
    write(output, 'HDD_DISCOVERY.md', '\n'.join(report) + '\n')


def split_report(db, output, versions):
    p, pl = versions[-2:]
    report = ['# PARADISE 与 PARADISE LOST', '', f'还原目标是 {p["end_on"]} 和 {pl["end_on"]}（JST）。',
              'A000 是 1.50.0 基础观测。A032 / A033 含跨版本内容和禁用状态。使用当年发行与停配记录。',
              '1.55 标签作为曲目归属线索。netOpenName 留作原始排期字段。',
              '8 月 5 日停配进入 LOST。Wiki 标为 11 月 3 日仍可玩的记录按该日保留。',
              'Wiki 的升级说明支持 PARADISE 的晚期 XML 定数作为过渡推定。原始来源、选择值及推定标签分别保留。', '',
              '| 内容 | 两代均可见 | 仅 LOST 可见 | 仅 PARADISE 可见 |', '|---|---:|---:|---:|']
    for table, key in [('music_versions', 'identity'), ('chart_versions', 'chart_identity'),
                       ('worlds_end_versions', 'chart_identity')]:
        n = intersection(db, table, key, p['version'], pl['version'])
        old = count(db, f"SELECT count(*) FROM {table} WHERE version=? AND state='present'", (p['version'],))
        new = count(db, f"SELECT count(*) FROM {table} WHERE version=? AND state='present'", (pl['version'],))
        report.append(f'| {table} | {n} | {new-n} | {old-n} |')
    events = breakdown(db, "SELECT kind,count(*) FROM changes WHERE new_version='PARADISE LOST' GROUP BY kind")
    report.extend(['', f'跨截点事件 `{encoded(events)}`。', '',
                   '第一张表的独有项包含已知新增、停配及覆盖不足。逐行归属、参考资料定位和未确认标记见 `paradise_split_evidence`。',
                   '', '网页原件的哈希和 URL 见 `references_used`。'])
    write(output, 'PARADISE_SPLIT_REPORT.md', '\n'.join(report) + '\n')


def validation_reports(db, output):
    w = breakdown(db, 'SELECT result,count(*) FROM wiki_we_checks GROUP BY result')
    total = count(db, "SELECT count(*) FROM worlds_end_versions WHERE state='present'")
    match = count(db, "SELECT count(*) FROM worlds_end_versions WHERE state='present' AND stars IS NOT NULL AND coalesce(tag,'') NOT IN ('','Invalid','なし')")
    report = ['# WORLD’S END 识别与校验', '', f'有效谱面中属性和数字均可解析 **{match}/{total}**。',
              '以 `exType=2` 和实际启用的 WE 槽识别。使用独立音乐 ID 和 C2S，原始星级和属性 ID 留存。',
              'Wiki 对照使用曲名和属性。多张同属性异星的记录保留歧义。', '',
              f'Wiki 比较结果 `{encoded(w)}`。', '', '| 原始编码 | 11 份 HDD 原始 XML 行（含重复） |', '|---|---:|']
    report.extend(f'| {raw} | {n} |' for raw, n in db.execute('SELECT star_raw,count(*) FROM raw_we GROUP BY star_raw ORDER BY star_raw'))
    report.extend(['', '逐行证据见 `wiki_we_checks`。WE 与同曲普通谱面的来源关系仅在共享 cue 且曲名或曲绘吻合时建立。'])
    write(output, 'WE_VALIDATION.md', '\n'.join(report) + '\n')
