"""Markdown parsing helpers for PHB .md files.

The PHB content is laid out in predictable patterns:

- Tables: ``**Table- Foo**`` followed by a pipe-delimited block.
- Traits: ``***Trait Name***. Description text.`` (bold-italic with period)
- Fields: ``**Field Name:** value`` on its own line.
- Sections: ``#``, ``##``, ``###`` headings split content into chunks.

These helpers turn raw markdown into structured pieces without bringing in
a full markdown library (which would balloon the dependency tree and try
to render the content, which we don't want).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ============================================================================
# Section splitting
# ============================================================================


@dataclass
class Section:
    """A heading and the lines that follow it (until the next heading)."""

    level: int  # 1-6
    title: str
    body: str  # raw lines, excluding the heading line itself

    @property
    def title_lower(self) -> str:
        return self.title.strip().lower()


def split_sections(text: str) -> list[Section]:
    """Split markdown into a flat list of sections, one per ATX heading.

    Every ``#``, ``##``, ``###`` etc. becomes its own Section. The body
    of each section contains everything between this heading and the next
    heading of ANY level — including deeper headings as raw text — so the
    caller can recurse with split_sections if it needs a hierarchy.

    Use ``get_sections_at_level`` to filter by depth.
    """
    sections: list[Section] = []
    current_level: int | None = None
    current_title: str = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            # Flush previous
            if current_level is not None:
                sections.append(
                    Section(current_level, current_title, "\n".join(current_lines))
                )
            current_level = len(m.group(1))
            current_title = m.group(2).strip()
            current_lines = []
        else:
            if current_level is not None:
                current_lines.append(line)

    if current_level is not None:
        sections.append(Section(current_level, current_title, "\n".join(current_lines)))

    return sections


def get_sections_at_level(sections: list[Section], level: int) -> list[Section]:
    """Filter sections by heading level."""
    return [s for s in sections if s.level == level]


# ============================================================================
# Tables
# ============================================================================


@dataclass
class Table:
    """A markdown table."""

    headers: list[str]
    rows: list[list[str]]
    title: str = ""

    def find_row(self, *, col: int | str, value: str) -> list[str] | None:
        """Find the first row where the given column matches value (case-insensitive)."""
        if isinstance(col, str):
            try:
                col = self.headers.index(col)
            except ValueError:
                return None
        target = value.strip().lower()
        for row in self.rows:
            if col < len(row) and row[col].strip().lower() == target:
                return row
        return None

    def filter_rows(self, *, col: int | str, value: str) -> list[list[str]]:
        """All rows matching col=value (case-insensitive substring)."""
        if isinstance(col, str):
            try:
                col = self.headers.index(col)
            except ValueError:
                return []
        target = value.strip().lower()
        return [r for r in self.rows if col < len(r) and target in r[col].strip().lower()]


_TABLE_HEADER_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def parse_table(block: str) -> Table | None:
    """Parse a markdown pipe table from a block of lines.

    Returns None if no valid table is found.
    """
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None

    # Find header
    header_idx = None
    for i, line in enumerate(lines):
        if _TABLE_HEADER_RE.match(line) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header_idx = i
            break
    if header_idx is None:
        return None

    headers_raw = lines[header_idx]
    headers = [h.strip() for h in headers_raw.strip("|").split("|")]

    # Parse rows
    rows: list[list[str]] = []
    for line in lines[header_idx + 2 :]:
        if not _TABLE_HEADER_RE.match(line):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Pad to header width
        while len(cells) < len(headers):
            cells.append("")
        rows.append(cells)

    return Table(headers=headers, rows=rows)


def find_tables(text: str) -> list[tuple[str, Table]]:
    """Find all tables in a markdown block.

    Returns list of (preceding_heading_or_empty, Table). The heading is the
    last ``**Table- ...**`` style line just above the table, or empty.
    """
    results: list[tuple[str, Table]] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r"^\*\*Table[-–]\s*(.+?)\*\*\s*$", line)
        if m:
            title = m.group(1).strip()
        elif line.startswith("|"):
            title = ""
        else:
            i += 1
            continue
        # Try to parse a table starting at the next line
        block_start = i + (1 if m else 0)
        block_lines = []
        j = block_start
        while j < len(lines):
            ln = lines[j]
            if ln.strip().startswith("|") or _TABLE_SEP_RE.match(ln.strip()):
                block_lines.append(ln)
                j += 1
            elif ln.strip() == "":
                j += 1
            else:
                break
        if block_lines:
            tbl = parse_table("\n".join(block_lines))
            if tbl is not None:
                tbl.title = title or tbl.title
                results.append((title, tbl))
                i = j
                continue
        i += 1

    return results


# ============================================================================
# Traits (bold-italic "***Name***. description")
# ============================================================================


_TRAIT_RE = re.compile(r"\*\*\*(.+?)\*\*\*\.\s*(.+?)(?=\n\*\*\*|\n### |\n## |\Z)", re.DOTALL)


def parse_traits(body: str) -> list[tuple[str, str]]:
    """Parse ``***Trait Name***. description`` blocks.

    Returns list of (name, description). Multi-line descriptions are
    collapsed to a single paragraph.
    """
    matches: list[tuple[str, str]] = []
    for m in _TRAIT_RE.finditer(body):
        name = _clean_inline(m.group(1))
        desc = _collapse_whitespace(m.group(2))
        matches.append((name, desc))
    return matches


# ============================================================================
# Fields (**Field Name:** value)
# ============================================================================


_FIELD_RE = re.compile(r"^\*\*([^*]+?):\*\*\s*(.+?)(?=\n\*\*|\Z)", re.DOTALL | re.MULTILINE)


def parse_fields(body: str) -> dict[str, str]:
    """Parse ``**Field:** value`` lines into a dict.

    Multi-line values are collapsed to a single line.
    """
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(body):
        key = m.group(1).strip().lower()
        value = _collapse_whitespace(m.group(2))
        fields[key] = value
    return fields


# ============================================================================
# String helpers
# ============================================================================


def _clean_inline(text: str) -> str:
    """Strip residual markdown emphasis from inline text."""
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    return text.strip()


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace/newlines into single spaces."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_cost_gp(text: str) -> float:
    """Parse a PHB cost string ('5 gp', '1 sp', '5 cp', '1,500 gp') to gold pieces.

    Returns 0.0 if unparseable.
    """
    text = text.strip().replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(cp|sp|ep|gp|pp)\s*$", text, re.IGNORECASE)
    if not m:
        return 0.0
    amount = float(m.group(1))
    unit = m.group(2).lower()
    multipliers = {"cp": 0.01, "sp": 0.1, "ep": 0.5, "gp": 1.0, "pp": 10.0}
    return amount * multipliers[unit]


def parse_weight_lb(text: str) -> float:
    """Parse a PHB weight string ('10 lb.', '1/4 lb.', '-') to pounds.

    Returns 0.0 for '-' or unparseable.
    """
    text = text.strip().rstrip(".").lower()
    if text in {"", "-"}:
        return 0.0
    # Strip optional 'lb' or 'lbs' suffix
    text = re.sub(r"\s*lbs?\s*$", "", text).strip()
    # Fraction
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", text)
    if m:
        return float(m.group(1)) / float(m.group(2))
    m = re.match(r"^(\d+(?:\.\d+)?)$", text)
    if m:
        return float(m.group(1))
    return 0.0


def parse_damage(text: str) -> tuple[str, str]:
    """Parse '1d8 slashing' or '1 piercing' into (dice, type).

    Returns ("", "") if unparseable. Dice is empty for non-dice damage
    like '1 piercing'.
    """
    text = text.strip()
    m = re.match(r"^(\d+d\d+(?:\s*[+\-]\s*\d+)?)\s+(\w+)\s*$", text)
    if m:
        return m.group(1).replace(" ", ""), m.group(2).lower()
    m = re.match(r"^(\d+)\s+(\w+)\s*$", text)
    if m:
        return "", m.group(2).lower()
    return "", ""


def parse_range(text: str) -> tuple[int | None, int | None]:
    """Parse 'range 20/60' or '(range 80/320)' into (normal, long).

    Returns (None, None) if not present.
    """
    m = re.search(r"range\s+(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None