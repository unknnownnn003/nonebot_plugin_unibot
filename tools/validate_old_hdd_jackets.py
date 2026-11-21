"""Decode HDD-linked jackets read-only and produce a deterministic private QA sample."""
import argparse
import csv
from contextlib import closing
from pathlib import Path
from random import Random
import sqlite3

from PIL import Image, ImageDraw

from old_hdd_scan import file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError('Output must be outside the source HDD tree')
    output.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(args.catalog.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM jackets ORDER BY sha256').fetchall()
        samples = []
        rng = Random(2121)
        for version, ex_type in (('CHUNITHM', 0), ('PLUS', 2), ('AIR PLUS', 0), ('STAR', 2),
                                 ('AMAZON', 0), ('CRYSTAL', 2), ('PARADISE', 0), ('PARADISE LOST', 2)):
            pool = db.execute('''SELECT music_id,j.path,j.sha256 FROM music_versions m JOIN jacket_manifest j
                                 USING(version,identity,music_id) WHERE m.version=? AND ex_type=?
                                 AND state='present' AND j.status='matched' ORDER BY music_id, identity''',
                              (version, ex_type)).fetchall()
            if pool:
                samples.append((version, ex_type, rng.choice(pool)))

    with (output / 'jacket_validation.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(('sha256','path','format','width','height','mode','alpha_min','alpha_max','result'))
        for row in rows:
            path = (root / row['representative_path']).resolve(strict=True)
            try:
                if not path.is_relative_to(root) or file_hash(path) != row['sha256']:
                    raise ValueError('Source path or SHA-256 mismatch')
                with Image.open(path) as image:
                    image.load()
                    alpha = image.getchannel('A').getextrema() if 'A' in image.getbands() else (255, 255)
                    item = (image.format, *image.size, image.mode, *alpha)
                    result = 'decoded' if image.size == (row['width'], row['height']) else 'size_mismatch'
            except (OSError, ValueError) as exc:
                item, result = (None,) * 6, f'{type(exc).__name__}: {exc}'
            writer.writerow((row['sha256'], row['representative_path'], *item, result))

    sheet = Image.new('RGB', (880, 470), '#232836')
    draw = ImageDraw.Draw(sheet)
    for index, (version, ex_type, row) in enumerate(samples):
        with Image.open(root / row['path']) as source:
            tile = source.convert('RGBA')
            original_size = tile.size
        tile.thumbnail((164, 164), Image.Resampling.LANCZOS)
        left, top = (index % 4) * 220 + 28, (index // 4) * 230 + 20
        draw.rectangle((left, top, left + 163, top + 163), fill='#171b24')
        sheet.paste(tile, (left + (164 - tile.width) // 2, top + (164 - tile.height) // 2), tile)
        draw.text((left, top + 171), f"{version} / {'WE' if ex_type else 'NORMAL'}", fill='white')
        draw.text((left, top + 190), f"ID {row['music_id']}  {original_size[0]}x{original_size[1]}", fill='#b5bfd1')
    sheet.save(output / 'jacket_sample.png')


if __name__ == '__main__':
    main()
