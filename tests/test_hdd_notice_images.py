import sys
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from apply_hdd_notice_reviews import materialize  # noqa: E402
from hdd_embedded_images import embedded_dds  # noqa: E402
from hdd_image_inventory import Reader, fingerprint, inventory  # noqa: E402
from hdd_notice_images import compare, dates, possible_days, xml_references  # noqa: E402


class ImageEvidenceTests(unittest.TestCase):
    def test_month_day_weekday_range_and_printed_order(self):
        start, other = dates('開催期間 2026/9/25 ～ 11月11日(水)')
        self.assertEqual(start['printed'], '2026/9/25')
        span = [(date(2025, 1, 1), date(2026, 12, 31))]
        self.assertEqual(possible_days(start, span), ['2026-09-25'])
        self.assertEqual(possible_days(other, span), ['2026-11-11'])
        self.assertEqual(list(dates('2月30日')), [])
        self.assertEqual(possible_days(next(dates('9月25日')), span), ['2025-09-25', '2026-09-25'])

    def test_cache_reuse_tracks_zip_member_and_checks_deleted_resource(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            loose = root / 'notice.dds'
            loose.write_bytes(b'DDS test')
            with ZipFile(root / 'option.zip', 'w') as archive:
                archive.writestr('unindexed/notice.dds', loose.read_bytes())
            db = sqlite3.connect(':memory:')
            self.addCleanup(db.close)
            db.row_factory = sqlite3.Row
            roots = {'SDHD': root}
            inventory(db, roots)
            reader = Reader(roots, None, stack)
            items = list(db.execute("SELECT * FROM resource WHERE extension='.dds' ORDER BY member"))
            first, raw = fingerprint(db, items[0], reader)
            second, _ = fingerprint(db, items[1], reader)
            self.assertEqual(raw, b'DDS test')
            self.assertEqual(first, second)
            inventory(db, roots)
            self.assertEqual(fingerprint(db, items[0], reader), (first, None))
            loose.unlink()
            with self.assertRaises(FileNotFoundError):
                fingerprint(db, items[0], reader)

    def test_changed_image_does_not_inherit_reference(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE history_asset_links(location,asset,relation,source,batch)')
            db.execute("INSERT INTO history_asset_links VALUES ('image.dds','old','event','Event.xml','A000')")
            self.assertEqual(dict(xml_references(db, {'new': [{'path': 'image.dds', 'member': ''}]})), {})
            self.assertEqual(xml_references(db, {'old': [{'path': 'image.dds', 'member': ''}]})['old'][0]['source'],
                             'Event.xml')

    def test_embedded_dds_reports_offsets_and_rejects_truncated_headers(self):
        from struct import pack_into
        header = bytearray(128)
        header[:8] = b'DDS |\0\0\0'
        pack_into('<II', header, 12, 12, 16)
        pack_into('<I', header, 76, 32)
        header[84:88] = b'DXT1'
        records = list(embedded_dds(b'FILS' + header + header[:100]))
        self.assertEqual(records, [{'offset': 4, 'width': 16, 'height': 12, 'fourcc': '44585431'}])

    def test_review_never_replaces_existing_confirmed_date(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript("""CREATE TABLE sdhd_first_release(identity,exact_on);
                CREATE TABLE history_events(identity,kind,effective_on);
                INSERT INTO sdhd_first_release VALUES ('song','2026-01-01');""")
            self.assertEqual(materialize(db, {'identity': 'song'}, '2026-01-02', {}, {}),
                             'conflict_existing_exact')
            self.assertEqual(db.execute('SELECT exact_on FROM sdhd_first_release').fetchone(), ('2026-01-01',))

    def test_visual_comparison_is_not_raw_file_hash(self):
        original = Image.new('RGB', (100, 50), 'navy')
        original.paste((230, 150, 40), (15, 8, 60, 32))
        same = original.resize((200, 100))
        other = Image.new('RGB', original.size, 'white')
        self.assertLess(compare(original, same)['whole_frame_mean_error'], .02)
        self.assertGreater(compare(original, other)['whole_frame_mean_error'], .1)


if __name__ == '__main__':
    unittest.main()
