"""The plain functions of the CSV explorer: reading a file, summarising, filtering, grouping.

Nothing here knows Streamlit, the Portal or the model, so every function can
be tested and reused by itself. app.py is the page and holds everything else.

Only the standard library is used. pandas would do the same in fewer lines
(it comes with Streamlit and may be imported), but for 50,000 rows the csv
module is enough, starts faster and keeps the memory of the app small.
"""

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

MAX_FILE_BYTES = 5 * 1024 * 1024  # the same 5 MB that .streamlit/config.toml tells Streamlit to accept
MAX_ROWS = 50000  # rows that are read; the rest of a longer file is left out, and the page says so
MAX_COLUMNS = 60
MAX_GROUPS = 200  # groups of a group-by that are shown
MAX_POINTS = 5000  # points of a scatter chart
MAX_SAMPLE_ROWS = 20  # rows the model gets
MAX_PROMPT_COLUMNS = 40
MAX_PROMPT_CELL = 60  # characters of one cell in the prompt
MAX_PROMPT_SAMPLE_CHARS = 6000  # of all sample rows together

#: Tried in this order when no encoding is chosen. Each is strict, so the
#: first that fits wins; Latin-1 reads any byte and is the last resort.
DETECTED = ("utf-8", "gb18030", "cp1252", "latin-1")
#: What the page offers when the detection got it wrong.
ENCODINGS = ("utf-8", "utf-16", "gb18030", "big5hkscs", "cp1252")
DELIMITERS = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
OPERATORS = ("contains", "equals", "is not", "is greater than", "is less than", "is missing", "is not missing")
AGGREGATES = ("count", "sum", "mean")
MISSING = frozenset(("", "na", "n/a", "null", "none", "nan", "-"))

# 12, -3.5, 1,234.56, 1e6, 45%. A fixed shape and a short cell: nothing to slow it down.
NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d{1,3})?%?")
# 2026-11-12, 2026/11/12, 2026-11-12 14:05, 2026-11-12T14:05:09.123Z
DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?)?(?:Z|[+-]\d{2}:?\d{2})?")


class BadFile(ValueError):
    """The upload cannot be read as a table. The message is a sentence for the visitor."""


@dataclass
class Table:
    """A CSV file as text cells: the names of the columns and the rows below them."""

    columns: List[str]
    rows: List[List[str]]
    encoding: str = "utf-8"
    delimiter: str = ","
    cut: bool = False  # the file had more than MAX_ROWS rows
    uneven: int = 0  # rows with more or fewer cells than the header, padded or cut to fit
    summary: List[dict] = field(default_factory=list)  # one entry per column, see summarise()


# --- Reading ---------------------------------------------------------------------------------------


def decode(data: bytes, encoding: Optional[str] = None) -> Tuple[str, str]:
    """``(text, encoding)``: the bytes as text, and the encoding that was used.

    A byte order mark decides. Without one, UTF-8 is tried, then the usual
    encodings of Chinese and of Western spreadsheets. That is a guess: files
    in Big5 and in GB18030 cannot be told apart by their bytes, which is why
    the page lets the visitor choose. A chosen encoding is never refused;
    bytes it cannot read become replacement characters.
    """
    if encoding:
        return data.decode(encoding, errors="replace"), encoding
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace"), "utf-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"
    for candidate in DETECTED:
        try:
            return data.decode(candidate), candidate
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"  # not reached: Latin-1 reads everything


def sniff_delimiter(text: str) -> str:
    """The delimiter of a CSV text, judged by its first lines; a comma when nothing else fits.

    For every candidate the first twenty lines are read. The winner is the
    one that gives the most lines the same number of cells as the header,
    and more than one cell.
    """
    lines = [line for line in text[:20000].splitlines()[:20] if line.strip()]
    best, best_score = ",", 0
    for candidate in DELIMITERS:
        try:
            counts = [len(row) for row in csv.reader(lines, delimiter=candidate)]
        except csv.Error:
            continue
        if not counts or counts[0] < 2:
            continue
        score = counts[0] * sum(1 for count in counts if count == counts[0])
        if score > best_score:
            best, best_score = candidate, score
    return best


def column_names(header: Sequence[str]) -> List[str]:
    """The header as names that are present, short and unique: "column 3" for a blank one, "id (2)" for a second "id"."""
    names: List[str] = []
    for position, cell in enumerate(header, 1):
        name = " ".join(cell.split())[:80] or "column %d" % position
        candidate, number = name, 1
        while candidate in names:
            number += 1
            candidate = "%s (%d)" % (name, number)
        names.append(candidate)
    return names


def parse(data: bytes, encoding: Optional[str] = None, delimiter: Optional[str] = None, max_rows: int = MAX_ROWS) -> Table:
    """Read an uploaded file. ``encoding`` and ``delimiter`` are detected when not given.

    Raises ``BadFile`` with a sentence for the visitor when this is not a
    table that can be explored.
    """
    if len(data) > MAX_FILE_BYTES:
        raise BadFile("The file has %s; at most %s can be explored." % (size_text(len(data)), size_text(MAX_FILE_BYTES)))
    if data.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        raise BadFile("This is a spreadsheet file, not CSV. In the spreadsheet choose to save or export it as CSV first.")
    text, used_encoding = decode(data, encoding)
    if used_encoding != "utf-16" and "\x00" in text[:4096]:
        raise BadFile("This does not look like a text file.")
    if not text.strip():
        raise BadFile("The file is empty.")
    used_delimiter = delimiter or sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=used_delimiter)
    rows: List[List[str]] = []
    cut, uneven, columns = False, 0, None
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # a blank line is not a row
            if columns is None:
                columns = column_names(row)
                if len(columns) > MAX_COLUMNS:
                    raise BadFile("The file has %d columns; at most %d can be explored." % (len(columns), MAX_COLUMNS))
                continue
            if len(rows) >= max_rows:
                cut = True
                break
            if len(row) != len(columns):
                uneven += 1
                row = (row + [""] * len(columns))[: len(columns)]
            rows.append([cell.strip() for cell in row])
    except csv.Error as exc:
        raise BadFile("The file could not be read as CSV (%s)." % exc) from None
    if columns is None:
        raise BadFile("The file is empty.")
    table = Table(columns, rows, used_encoding, used_delimiter, cut, uneven)
    table.summary = summarise(table)
    return table


def size_text(size: int) -> str:
    return "%.1f MB" % (size / (1024 * 1024)) if size >= 1024 * 1024 else "%d KB" % max(1, size // 1024)


# --- What a cell holds -----------------------------------------------------------------------------


def is_missing(value: str) -> bool:
    return value.strip().lower() in MISSING


def to_number(value: str) -> Optional[float]:
    """The number a cell holds, or None.

    "1,234.5" and "45%" are numbers. "007" is not: a code with leading zeros
    is a name, and adding such codes up would be wrong. A decimal comma
    ("1,5") is not understood.
    """
    text = value.strip()
    if len(text) > 40 or not NUMBER.fullmatch(text):
        return None
    digits = text.lstrip("+-")
    if len(digits) > 1 and digits[0] == "0" and digits[1].isdigit():
        return None
    number = float(text.rstrip("%").replace(",", ""))
    return number if math.isfinite(number) else None


def to_date(value: str) -> Optional[str]:
    """A date or date-time cell as "2026-11-12" or "2026-11-12 14:05:09" (which sorts as time does), or None."""
    match = DATE.fullmatch(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        if hour is None:
            return date(int(year), int(month), int(day)).isoformat()
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0)).isoformat(sep=" ")
    except ValueError:
        return None  # the 30th of February


def number_text(number: float) -> str:
    """A number the way a person writes it: 1,234 or 0.125, at most four decimals."""
    if number == int(number) and abs(number) < 1e15:
        return format(int(number), ",")
    if abs(number) >= 1e15 or 0 < abs(number) < 0.0001:
        return "%.4g" % number
    return format(number, ",.4f").rstrip("0").rstrip(".")


# --- Summarising -----------------------------------------------------------------------------------


def summarise(table: Table) -> List[dict]:
    """One entry per column: its type, how many cells are missing, how many distinct values, minimum, maximum, mean.

    A column is a "number" when every cell that is present is one, a "date"
    likewise, otherwise "text" ("empty" when nothing is present). Minimum,
    maximum and mean are texts ready to show, and blank where they mean
    nothing.
    """
    summary = []
    for position, name in enumerate(table.columns):
        present = [row[position] for row in table.rows if not is_missing(row[position])]
        entry = {
            "column": name,
            "type": "empty",
            "missing": len(table.rows) - len(present),
            "distinct": len(set(present)),
            "minimum": "",
            "maximum": "",
            "mean": "",
        }
        numbers = all_of(to_number, present) if present else None
        dates = all_of(to_date, present) if present and numbers is None else None
        if numbers is not None:
            entry.update(
                type="number",
                minimum=number_text(min(numbers)),
                maximum=number_text(max(numbers)),
                mean=number_text(sum(numbers) / len(numbers)),
            )
        elif dates is not None:
            entry.update(type="date", minimum=min(dates), maximum=max(dates))
        elif present:
            entry["type"] = "text"
        summary.append(entry)
    return summary


def all_of(convert, values: List[str]) -> Optional[list]:
    """Every value converted, or None as soon as one cannot be: a column of names is given up at its first cell."""
    converted = []
    for value in values:
        result = convert(value)
        if result is None:
            return None
        converted.append(result)
    return converted


def columns_of_type(table: Table, kind: str) -> List[str]:
    return [entry["column"] for entry in table.summary if entry["type"] == kind]


def default_key(table: Table) -> str:
    """The column a group-by starts with: the text column with the fewest different values, as long as it has some.

    Grouping by an id gives one group per row; a column such as "status" is
    what somebody wants to see first.
    """
    candidates = [entry for entry in table.summary if entry["type"] == "text" and 2 <= entry["distinct"] <= 50]
    if not candidates:
        return table.columns[0]
    return min(candidates, key=lambda entry: entry["distinct"])["column"]


def typed(table: Table, rows: List[List[str]]) -> List[dict]:
    """Rows as ``{column: cell}`` for a table on the page, with the cells of number columns as numbers.

    A table widget sorts numbers as numbers only when they are numbers: as
    text, "10" comes before "9".
    """
    numeric = [entry["type"] == "number" for entry in table.summary]
    return [
        {name: (to_number(cell) if number else cell) for name, cell, number in zip(table.columns, row, numeric)}
        for row in rows
    ]


# --- Filtering -------------------------------------------------------------------------------------


def filter_rows(table: Table, column: str, operator: str, wanted: str) -> List[List[str]]:
    """The rows whose cell in ``column`` passes ``operator`` (one of OPERATORS) against ``wanted``.

    Numbers compare as numbers and dates as dates when both sides are; text
    compares without regard to letter case. A missing cell is neither
    greater nor less than anything.
    """
    if column not in table.columns or operator not in OPERATORS:
        raise ValueError("an unknown column or comparison")
    position = table.columns.index(column)
    wanted = wanted.strip()
    wanted_number, wanted_date, wanted_text = to_number(wanted), to_date(wanted), wanted.casefold()

    def passes(cell: str) -> bool:
        if operator in ("is missing", "is not missing"):
            return is_missing(cell) == (operator == "is missing")
        if operator == "contains":
            return wanted_text in cell.casefold()
        if is_missing(cell) and operator != "is not":
            return False
        left, right = cell.casefold(), wanted_text  # compared as text unless both sides are numbers or dates
        if wanted_number is not None and to_number(cell) is not None:
            left, right = to_number(cell), wanted_number
        elif wanted_date is not None and to_date(cell) is not None:
            left, right = to_date(cell), wanted_date
        if operator == "equals":
            return left == right
        if operator == "is not":
            return left != right
        return left > right if operator == "is greater than" else left < right

    return [row for row in table.rows if passes(row[position])]


# --- Grouping and charts ---------------------------------------------------------------------------


def group_by(
    table: Table, rows: List[List[str]], key: str, how: str = "count", value: Optional[str] = None, limit: int = MAX_GROUPS
) -> Tuple[List[dict], int]:
    """``(groups, left_out)``: one entry per value of ``key``, with the count, sum or mean of ``value``.

    Each entry is ``{key: ..., result: ...}``, where ``result`` is named
    "rows", "sum of x" or "mean of x". Groups of a number or date column come
    in the order of the key; other groups largest first. At most ``limit``
    groups are returned, and ``left_out`` says how many more there were. Cells
    of ``value`` that are not numbers are skipped; a mean of nothing is no
    group.
    """
    if how not in AGGREGATES or key not in table.columns or (how != "count" and value not in table.columns):
        raise ValueError("an unknown column or aggregate")
    key_position = table.columns.index(key)
    value_position = table.columns.index(value) if how != "count" else None
    result = "rows" if how == "count" else "%s of %s" % (how, value)
    if result == key:
        result += " (result)"
    totals: Dict[str, List[float]] = {}  # the key of a group -> [rows, numbers, their sum]
    for row in rows:
        group = "(missing)" if is_missing(row[key_position]) else row[key_position]
        total = totals.setdefault(group, [0, 0, 0.0])
        total[0] += 1
        if value_position is not None:
            number = to_number(row[value_position])
            if number is not None:
                total[1] += 1
                total[2] += number
    kind = next(entry["type"] for entry in table.summary if entry["column"] == key)
    groups = []
    for group, (count, numbers, total) in totals.items():
        if how == "count":
            outcome: float = count
        elif how == "sum":
            outcome = round(total, 6)
        elif numbers:
            outcome = round(total / numbers, 6)
        else:
            continue
        shown: object = group
        if kind == "number" and to_number(group) is not None:
            shown = to_number(group)  # a number on the axis of a chart, not a label
        groups.append({key: shown, result: outcome})
    if kind in ("number", "date"):
        groups.sort(key=lambda entry: (isinstance(entry[key], str), entry[key]))
    else:
        groups.sort(key=lambda entry: (-entry[result], str(entry[key])))
    return groups[:limit], max(0, len(groups) - limit)


def numbers_of(table: Table, rows: List[List[str]], column: str) -> List[float]:
    """The cells of a column that are numbers."""
    position = table.columns.index(column)
    found = (to_number(row[position]) for row in rows)
    return [number for number in found if number is not None]


def histogram(values: Sequence[float], bins: int = 20) -> List[dict]:
    """``bins`` ranges of equal width from the smallest to the largest value: ``{"from", "to", "rows"}`` each."""
    if not values:
        return []
    bins = min(max(int(bins), 1), 100)
    low, high = min(values), max(values)
    if low == high:
        return [{"from": low, "to": high, "rows": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        counts[min(int((value - low) / width), bins - 1)] += 1  # the largest value belongs to the last range
    return [
        {"from": round(low + width * number, 6), "to": round(low + width * (number + 1), 6), "rows": count}
        for number, count in enumerate(counts)
    ]


def points(table: Table, rows: List[List[str]], x: str, y: str, limit: int = MAX_POINTS) -> Tuple[List[dict], int]:
    """``(points, left_out)`` of a scatter chart: ``{"x", "y"}`` for the rows in which both cells are numbers.

    More than ``limit`` points are thinned evenly, so the chart still shows the whole file.
    """
    x_position, y_position = table.columns.index(x), table.columns.index(y)
    found = []
    for row in rows:
        left, right = to_number(row[x_position]), to_number(row[y_position])
        if left is not None and right is not None:
            found.append({"x": left, "y": right})
    if len(found) <= limit:
        return found, 0
    step = len(found) / limit
    return [found[int(number * step)] for number in range(limit)], len(found) - limit


def to_csv(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> bytes:
    """A table as a CSV file: UTF-8, with the mark at its start that makes spreadsheets read it as such."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


# --- What the model gets ---------------------------------------------------------------------------


def sample_rows(rows: List[List[str]], limit: int = MAX_SAMPLE_ROWS) -> List[List[str]]:
    """At most ``limit`` rows, spread evenly over the table: a file's first rows are often all alike."""
    if len(rows) <= limit:
        return list(rows)
    step = len(rows) / limit
    return [rows[int(number * step)] for number in range(limit)]


def question_prompt(table: Table, question: str) -> str:
    """The prompt for a question about the table: the column summary and a few sample rows, never the whole file.

    The summary is small whatever the size of the file, so a question about
    50,000 rows costs what a question about 50 costs.
    """
    columns = table.columns[:MAX_PROMPT_COLUMNS]
    lines = ["A table with %s rows and %d columns." % (format(len(table.rows), ","), len(table.columns)), ""]
    lines.append("Columns (name | type | missing | distinct | minimum | maximum | mean):")
    for entry in table.summary[:MAX_PROMPT_COLUMNS]:
        lines.append("- %(column)s | %(type)s | %(missing)d | %(distinct)d | %(minimum)s | %(maximum)s | %(mean)s" % entry)
    if len(table.columns) > MAX_PROMPT_COLUMNS:
        lines.append("(%d more columns are not listed.)" % (len(table.columns) - MAX_PROMPT_COLUMNS))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    sent = 0
    for row in sample_rows(table.rows):
        if out.tell() > MAX_PROMPT_SAMPLE_CHARS:
            break  # wide rows: fewer of them, so that the size of the prompt stays bounded
        writer.writerow([cell[:MAX_PROMPT_CELL] for cell in row[:MAX_PROMPT_COLUMNS]])
        sent += 1
    lines += ["", "%d sample rows of the %s (not the whole table), as CSV:" % (sent, format(len(table.rows), ","))]
    lines += [out.getvalue().rstrip("\n"), "", "Question: %s" % " ".join(question.split())]
    return "\n".join(lines)
