"""Read explicit rows from locked historical sources without spreadsheet dependencies."""
from collections import defaultdict
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import ZipFile


DIFF = {"EX": "EXP", "EXP": "EXP", "EXPERT": "EXP", "赤": "EXP", "MAS": "MAS", "MASTER": "MAS", "紫": "MAS",
        "BAS": "BAS", "BASIC": "BAS", "ADV": "ADV", "ADVANCED": "ADV", "ULT": "ULT", "ULTIMA": "ULT"}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
DECIMAL = re.compile(r"(?:[1-9]|1[0-6])(?:\.[0-9])?")


def number(value):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value.strip()):
        return None
    whole, _, digit = value.strip().partition(".")
    return int(whole) * 10 + int(digit or "0")


def strings(node):
    return "".join(x.text or "" for x in node.findall(".//s:t", NS))


def workbook_rows(data):
    """Yield visible tab rows as {column name: (value, cell address)}."""
    with ZipFile(BytesIO(data)) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = [strings(el) for el in ElementTree.fromstring(book.read("xl/sharedStrings.xml"))]
        relations = {el.attrib["Id"]: el.attrib["Target"]
                     for el in ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        for tab in ElementTree.fromstring(book.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            if tab.get("state", "visible") != "visible":
                continue
            target = relations[tab.attrib[REL]]
            path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl", target))
            rows = []
            for row in ElementTree.fromstring(book.read(path)).findall("s:sheetData/s:row", NS):
                cells = []
                for cell in row.findall("s:c", NS):
                    value = cell.findtext("s:v", namespaces=NS)
                    if cell.get("t") == "s":
                        value = shared[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = strings(cell)
                    elif cell.get("t") in ("e", "b"):
                        value = None
                    # Google exports cached results for same-workbook constants.
                    # A missing cached value contributes no evidence.
                    address = cell.attrib["r"]
                    col = 0
                    for ch in address.rstrip("0123456789"):
                        col = col * 26 + ord(ch) - ord("A") + 1
                    cells.append((col, value, address))
                rows.append({col: (value, address) for col, value, address in cells if value is not None})
            yield tab.attrib["name"], rows


def comparison_rows(data, columns):
    """Require explicit difficulty and two expected edition header labels."""
    records = []
    for tab, rows in workbook_rows(data):
        groups = []
        for i, row in enumerate(rows[:9]):
            for col, (value, _) in row.items():
                keys = tuple(row.get(col + n, ("", ""))[0].strip() for n in range(1, 5))
                if value == "曲名" and keys == ("譜面", "ジャンル", *columns):
                    groups.append((i, col))
        for heading, col in groups:
            for row in rows[heading + 1:]:
                title = row.get(col, ("", ""))[0].strip()
                diff = DIFF.get(row.get(col + 1, ("", ""))[0].strip())
                if not title or diff is None:
                    continue
                genre = row.get(col + 2, ("", ""))[0]
                for side in (0, 1):
                    raw, address = row.get(col + 3 + side, ("", ""))
                    if (value := number(raw)) is not None:
                        records.append((side, title, diff, genre, value, f"{tab}!{address}"))
    if not records:
        raise ValueError(f"No comparison cells found for headers {columns}")
    return records


class TableCells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.struck = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in ("s", "strike", "del"):
            self.struck += 1
        elif tag in ("br", "p") and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag in ("s", "strike", "del"):
            self.struck = max(0, self.struck - 1)

    def handle_data(self, data):
        if self.cell is not None and not self.struck:
            self.cell.append(data)


def survey_rows(data, separator="slash"):
    parser = TableCells()
    parser.feed(data.decode("utf-8"))
    for i, row in enumerate(parser.rows):
        if len(row) != 2 or not re.fullmatch(r"\d{1,2}\.\d", row[0]):
            continue
        pattern = r"\s+/\s*|\s*/\s+" if separator == "slash" else "、"
        for j, cell in enumerate(re.split(pattern, row[1])):
            diff = "MAS"
            match = re.search(r"\s*[(（](EX|EXP|MAS|ULT|赤|紫)[)）]\s*$", cell)
            if match:
                diff = DIFF[match[1]]
                cell = cell[:match.start()]
            title = cell.strip()
            if title and not any(x in title for x in ("未確定", "未検証", "暫定", "定数不明")):
                yield title, diff, number(row[0]), f"table row {i + 1}, title {j + 1}"


def current_info_rows(data):
    """Read the public data presented by CHUNITHM Info's current table."""
    for script in re.findall(rb"self\.__next_f\.push\((\[.*?\])\)</script>", data, re.S):
        envelope = json.loads(script)
        if len(envelope) < 2 or not isinstance(envelope[1], str):
            continue
        for line in envelope[1].splitlines():
            if '"initialTable":' not in line:
                continue
            table = json.loads(line.split(":", 1)[1])[3]["initialTable"]
            if table["total"] != len(table["charts"]):
                raise ValueError("Incomplete CHUNITHM Info constant table")
            for i, (key, difficulty, level, value) in enumerate(table["charts"]):
                if difficulty in DIFF:
                    yield key, table["songs"][key], difficulty, str(level), number(str(value)), f"initialTable.charts[{i}]"
            return
    raise ValueError("Missing CHUNITHM Info constant table")


def mate_low_level_rows(data):
    """Read the current numeric group headers with the table's carried difficulty."""
    parser = TableCells()
    parser.feed(data.decode("utf-8"))
    active = False
    diff = value = None
    for i, row in enumerate(parser.rows, 1):
        if row == ["難易度", "ジャンル", "タイトル", "ノーツ数", "旧定数"]:
            active, diff, value = True, None, None
        elif active and len(row) == 1:
            value, diff = number(row[0]), None
            active = value is not None and 100 <= value <= 119
        elif active and value and len(row) in (3, 4, 5) and row[-2].isdigit():
            if len(row) == 5:
                diff = DIFF.get(row[0])
            if diff:
                yield row[-3], diff, int(row[-2]), value, f"table row {i}"
