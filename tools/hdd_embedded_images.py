"""Inventory embedded DDS textures in observed AFB/FILS and SVO/AVTS containers."""
from contextlib import ExitStack, closing
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import sqlite3
from struct import unpack_from
from zipfile import BadZipFile

from hdd_image_inventory import LIMIT, Reader, arguments, inputs

HEADER = re.compile(rb'DDS \x7c\0\0\0')
SCHEMA = '''CREATE TABLE IF NOT EXISTS embedded_probe (
    cabinet TEXT,path TEXT,ordinal INTEGER,mtime INTEGER,bytes INTEGER,crc INTEGER,
    result TEXT NOT NULL, PRIMARY KEY(cabinet,path,ordinal,mtime,bytes,crc)
) WITHOUT ROWID'''


def embedded_dds(data):
    for match in HEADER.finditer(data):
        offset = match.start()
        if offset + 128 > len(data):
            continue
        height, width = unpack_from('<II', data, offset + 12)
        if not 0 < width * height <= 30_000_000 or data[offset+76:offset+80] != b' \0\0\0':
            continue
        yield {'offset': offset, 'width': width, 'height': height,
               'fourcc': data[offset+84:offset+88].hex()}


def main():
    parser = arguments(__doc__)
    parser.add_argument('--password-file', type=Path)
    args = parser.parse_args()
    roots, output = inputs(args)
    password = (args.password_file.read_text(encoding='utf-8-sig').rstrip('\r\n') if args.password_file
                else os.getenv('UNIBOT_HDD_ZIP_PASSWORD'))
    with ExitStack() as stack:
        db = stack.enter_context(closing(sqlite3.connect(output / 'images.sqlite', timeout=30)))
        db.row_factory = sqlite3.Row
        db.execute(SCHEMA)
        if {row[0]: Path(row[1]) for row in db.execute('SELECT * FROM inventory_root')} != roots:
            raise ValueError('Run inventory against these HDD roots first')
        reader = Reader(roots, password.encode() if password else None, stack)
        records, issues = [], []
        items = db.execute("SELECT * FROM resource WHERE extension IN ('.afb','.svo') AND scope<>'custom'").fetchall()
        for item in items:
            locator = {name: item[name] for name in ('cabinet', 'path', 'member', 'ordinal')}
            key = tuple(item[name] for name in ('cabinet', 'path', 'ordinal', 'mtime', 'bytes', 'crc'))
            try:
                if not 0 <= item['bytes'] <= LIMIT:
                    raise ValueError('container read limit')
                reader.live_path(item)
                cached = db.execute('SELECT result FROM embedded_probe WHERE cabinet=? AND path=? AND ordinal=? '
                                    'AND mtime=? AND bytes=? AND crc=?', key).fetchone()
                if cached:
                    result = json.loads(cached[0])
                else:
                    data = reader.read(item)
                    hits = list(embedded_dds(data))
                    result = {'sha256': sha256(data).hexdigest(), 'textures': hits}
                    with db:
                        db.execute('INSERT OR REPLACE INTO embedded_probe VALUES (?,?,?,?,?,?,?)',
                                   (*key, json.dumps(result)))
                records.append(locator | result)
            except (OSError, ValueError, RuntimeError, BadZipFile, KeyError, NotImplementedError) as exc:
                issues.append(locator | {'error': type(exc).__name__})
    result = {'containers': len(items), 'read': len(records), 'textures': sum(len(r['textures']) for r in records),
              'records': records, 'issues': issues}
    (output / 'embedded.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print({key: result[key] for key in ('containers', 'read', 'textures')})


if __name__ == '__main__':
    main()
