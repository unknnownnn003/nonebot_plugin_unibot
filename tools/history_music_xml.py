"""Read CHUNITHM Music.xml metadata without loading charts or game binaries."""
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import argparse
import json
import re
from xml.parsers import expat
from xml.etree import ElementTree


DIFFICULTIES = ('BAS', 'ADV', 'EXP', 'MAS', 'ULT')
NAMES = ('BASIC', 'ADVANCED', 'EXPERT', 'MASTER', 'ULTIMA')


@dataclass(frozen=True)
class ChartMetadata:
    slot: int
    difficulty: str
    enabled: bool | None
    level: int | None
    level_decimal: int | None
    constant: Decimal | None
    chart_path: str | None
    locator: str


def raw_constant(level, hundredths):
    if level is None or hundredths is None:
        return None
    if level < 0 or not 0 <= hundredths <= 99:
        raise ValueError('Invalid Music.xml level or levelDecimal')
    return Decimal(level) + Decimal(hundredths).scaleb(-2)


def field(node, name):
    return (node.findtext(name) or '').strip() or None


def integer(node, name):
    text = field(node, name)
    if text is not None and not re.fullmatch(r'-?[0-9]+', text):
        raise ValueError(f'{name} must be an integer')
    return int(text) if text is not None else None


def boolean(node, name):
    value = field(node, name)
    if value is None:
        return None
    if value.lower() not in ('1', '0', 'true', 'false'):
        raise ValueError(f'{name} must be a boolean')
    return value.lower() in ('1', 'true')


def difficulty_for(slot, label, profile, ex_type):
    label = (label or '').upper().replace('’', "'")
    if label.replace(' ', '') in ("WORLD'SEND", 'WORLDSEND', 'WE'):
        return 'WE'
    if 0 <= slot < 4 and label in ('', NAMES[slot]):
        return DIFFICULTIES[slot]
    if slot == 4:
        if label == 'ULTIMA' and profile != 'legacy' and ex_type in (None, 0):
            return 'ULT'
        if not label and profile == 'chusan' and ex_type in (None, 0):
            return 'ULT'
        if not label and (profile == 'legacy' or ex_type == 2):
            return 'WE'
    if slot == 5 and not label and profile == 'chusan':
        return 'WE'
    return 'unknown'


def reject_doctype(*args):
    raise ValueError('DTD declarations are unsupported in Music.xml metadata')


def parse_music_xml(data, *, profile='auto'):
    if profile not in ('auto', 'legacy', 'chusan'):
        raise ValueError('Unknown XML profile')
    if len(data) > 2_000_000:
        raise ValueError('Music.xml exceeds the metadata size limit')
    # Expat detects declarations independently of character encoding.
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.Parse(data, True)
    root = ElementTree.parse(BytesIO(data)).getroot()
    if root.tag != 'MusicData':
        raise ValueError('Expected MusicData root')
    key, title = integer(root, 'name/id'), field(root, 'name/str')
    if key is None or key < 0 or not title:
        raise ValueError('Music.xml needs a nonnegative music ID and a title')
    ex_type = integer(root, 'exType')
    disabled = boolean(root, 'disableFlag')
    charts = []
    seen = set()
    for index, row in enumerate(root.findall('fumens/MusicFumenData'), 1):
        slot = integer(row, 'type/id')
        if slot is None or slot < 0 or slot in seen:
            raise ValueError('Missing, invalid or duplicate chart slot')
        seen.add(slot)
        diff = difficulty_for(slot, field(row, 'type/data'), profile, ex_type)
        level, fraction = integer(row, 'level'), integer(row, 'levelDecimal')
        number = raw_constant(level, fraction)
        enabled = boolean(row, 'enable')
        if diff not in DIFFICULTIES or not enabled or ex_type in (1, 2) or not level:
            number = None
        charts.append(ChartMetadata(slot, diff, enabled, level, fraction, number,
                                    field(row, 'file/path'), f'MusicData/fumens/MusicFumenData[{index}]'))
    if not charts:
        raise ValueError('Music.xml has no chart metadata')
    return {'music_id': key, 'title': title, 'artist': field(root, 'artistName/str'),
            'release_tag_id': integer(root, 'releaseTagName/id'), 'release_tag': field(root, 'releaseTagName/str'),
            'format_version': field(root, 'formatVersion'), 'resource_version': field(root, 'resourceVersion/str'),
            'song_disabled': disabled, 'first_lock': boolean(root, 'firstLock'), 'ex_type': ex_type,
            'worlds_end_tag': field(root, 'worldsEndTagName/str'), 'charts': charts}


def observation(path, *, profile, game_version, effective_date, captured_at):
    observed = datetime.fromisoformat(captured_at)
    if observed.utcoffset() is None:
        raise ValueError('Capture time must include a UTC offset')
    if effective_date and date.fromisoformat(effective_date).isoformat() != effective_date:
        raise ValueError('Use an ISO YYYY-MM-DD effective date')
    with path.open('rb') as file:
        data = file.read(2_000_001)
    parsed = parse_music_xml(data, profile=profile)
    parsed['charts'] = [dict(asdict(c), chart_id=f"{parsed['music_id']}:{c.slot}") for c in parsed['charts']]
    return {**parsed, 'file': str(path), 'sha256': sha256(data).hexdigest(), 'profile': profile,
            'claimed_game_version': game_version, 'claimed_effective_date': effective_date,
            'captured_at': captured_at, 'version_evidence': 'operator_supplied', 'official_playability': 'unverified'}


def diff_observations(before, after):
    """Compare explicit, already-resolved files. A change still needs identity review."""
    def chart_key(row):
        return row['slot'], row['difficulty']

    def state(row):
        if row is None:
            return None
        return {k: row[k] for k in ('constant', 'enabled', 'chart_path')}

    if (before['music_id'], before['title'], before['artist']) != (after['music_id'], after['title'], after['artist']):
        raise ValueError('Song identity differs; review it before comparing snapshots')
    old = {chart_key(row): state(row) for row in before['charts']}
    new = {chart_key(row): state(row) for row in after['charts']}
    return {'music_id': before['music_id'], 'before_sha256': before['sha256'], 'after_sha256': after['sha256'],
            'before_song_disabled': before['song_disabled'], 'after_song_disabled': after['song_disabled'],
            'changes': [{'slot': key[0], 'difficulty': key[1], 'before': old.get(key), 'after': new.get(key)}
                        for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--profile', choices=('auto', 'legacy', 'chusan'), default='auto')
    parser.add_argument('--game-version')
    parser.add_argument('--effective-date')
    parser.add_argument('--captured-at', required=True)
    args = parser.parse_args()
    result = observation(args.file, profile=args.profile, game_version=args.game_version,
                         effective_date=args.effective_date, captured_at=args.captured_at)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
