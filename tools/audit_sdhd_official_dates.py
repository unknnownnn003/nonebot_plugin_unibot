"""Compare an official-evidence rebuild to the v3 inventory and export pending dates."""
import argparse
from collections import Counter
from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3

import import_paradise_lost  # Offline service bootstrap.
from _unibot_history.old_hdd_history import OldHddHistory

GAPS = """
WITH physical AS (
    SELECT o.identity,o.batch,o.source,b.version,v.ordinal,b.sequence,b.earliest_on,v.start_url,
           row_number() OVER (PARTITION BY o.identity ORDER BY v.ordinal,b.earliest_on,b.sequence,o.source) AS rn
    FROM history_observations o JOIN history_batches b USING(batch) JOIN versions v USING(version)
    WHERE b.kind='package' AND v.ordinal>12 AND o.state='hdd_enabled'
),
titles AS (SELECT identity,min(title) AS title,max(ex_type) AS ex_type FROM music_versions GROUP BY identity)
SELECT p.version,p.batch,p.earliest_on AS local_build_bound,e.kind,e.difficulty,e.music_id,e.identity,
       t.title,p.source,f.basis,p.start_url AS edition_search_anchor,
       (SELECT group_concat(DISTINCT h.day) FROM sdhd_release_evidence h WHERE h.identity=e.identity) AS candidate_dates,
       (SELECT group_concat(DISTINCT coalesce(ref.url,h.reference)) FROM sdhd_release_evidence h
         LEFT JOIN references_used ref ON ref.id=h.reference WHERE h.identity=e.identity) AS evidence_urls
FROM sdhd_first_release f JOIN history_entities e USING(identity) JOIN physical p USING(identity)
JOIN titles t ON t.identity=coalesce(e.parent,e.identity)
WHERE f.exact_on IS NULL AND p.rn=1 ORDER BY p.ordinal,p.sequence,e.kind,e.difficulty,e.music_id,e.identity
"""

RELEASES = """
WITH types AS (SELECT identity,max(ex_type) AS ex FROM music_versions GROUP BY identity)
SELECT f.identity,f.exact_on,
       CASE WHEN e.kind='song' AND ex=2 THEN 'we_song'
            WHEN e.kind='chart' AND difficulty='ULT' THEN 'ultima'
            WHEN e.kind='chart' THEN 'ordinary_chart' ELSE e.kind END AS category
FROM sdhd_first_release f JOIN history_entities e USING(identity) LEFT JOIN types USING(identity)
"""


def read_db(path):
    return closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True))


def compare_inventory(original, current):
    def content(reader, version):
        return [{key: value for key, value in row.items() if key not in ('state', 'basis')}
                for row in reader.checkpoint(version, active_only=False)]

    editions = original.versions()
    if current.versions() != editions:
        raise ValueError('Edition boundaries changed')
    for edition in editions:
        version = edition['version']
        if content(original, version) != content(current, version):
            raise ValueError(f'Physical checkpoint differs: {version}')
    return len(editions)


def compare_dates(before, after, gap_path):
    result = {'physical_checkpoints': compare_inventory(OldHddHistory(before), OldHddHistory(after))}
    with read_db(before) as old, read_db(after) as new:
        baseline = {r[0]: (r[1], r[2]) for r in old.execute(RELEASES)}
        totals = {}
        for identity, day, category in new.execute(RELEASES):
            previous, original = baseline.pop(identity)
            if original != category:
                raise ValueError(f'Identity kind changed: {identity}')
            counts = totals.setdefault(category, Counter(total=0, before=0, after=0, added=0, revised=0, lost=0))
            counts.update(total=1, before=int(bool(previous)), after=int(bool(day)),
                          added=int(day is not None and previous is None),
                          revised=int(day is not None and previous is not None and day != previous),
                          lost=int(day is None and previous is not None))
        if baseline or any(count['lost'] for count in totals.values()):
            raise ValueError('Release identity or confirmed date was lost')
        result['release_coverage'] = totals
        rows = new.execute(GAPS)
        with gap_path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow([column[0] for column in rows.description])
            writer.writerows(rows)
        result['notices'] = dict(new.execute('SELECT channel,count(*) FROM sdhd_notice GROUP BY channel'))
        result['x_dated'] = new.execute("""SELECT count(*),count(DISTINCT effective_on) FROM sdhd_notice
                                          WHERE channel='official_x_embed' AND effective_on IS NOT NULL""").fetchone()
        result['claimed_dates'] = new.execute('SELECT count(DISTINCT effective_on) FROM sdhd_notice_claim').fetchone()[0]
        result['exact_revisions'] = dict(new.execute("""SELECT field,count(*) FROM history_events
             WHERE kind='hdd_revision' AND effective_on IS NOT NULL GROUP BY field"""))
        result['official_access'] = dict(new.execute("""SELECT kind,count(*) FROM (
             SELECT DISTINCT kind,identity,effective_on,mode FROM sdhd_notice_claim WHERE kind NOT IN ('revision','first')
             ) GROUP BY kind"""))
        result['conflicts'] = new.execute("SELECT count(*) FROM sdhd_issues WHERE kind='official_date_disagreement'").fetchone()[0]
        result['unknown_by_basis'] = dict(new.execute("""SELECT basis,count(*) FROM sdhd_first_release
                                                         WHERE exact_on IS NULL GROUP BY basis"""))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--gaps', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare_dates(args.before, args.after, args.gaps), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
