"""Compatibility helpers for reading XLSX files from third-party exporters.

Some Tencent Documents exports contain empty ``<fill/>`` entries in
``xl/styles.xml`` and empty chart ``<c:grouping/>`` values.  openpyxl rejects
those entries before worksheets can be read.  The compatibility path repairs
only those known defects, in memory, and never changes the source workbook.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook


_STYLES_PATH = "xl/styles.xml"
_EMPTY_FILL_RE = re.compile(
    rb"<fill\s*/\s*>|<fill\s*>\s*</fill\s*>",
    flags=re.IGNORECASE,
)
_NO_FILL_XML = b'<fill><patternFill patternType="none"/></fill>'
_EMPTY_CHART_GROUPING_RE = re.compile(rb"<c:grouping\s*/\s*>")
_STANDARD_CHART_GROUPING_XML = b'<c:grouping val="standard"/>'


def _repair_styles_xml(styles_xml: bytes) -> tuple[bytes, int]:
    """Replace invalid empty fills without adding, deleting, or reordering them."""
    return _EMPTY_FILL_RE.subn(_NO_FILL_XML, styles_xml)


def _repaired_workbook_stream(source: Path) -> BytesIO | None:
    """Build an in-memory repaired XLSX, or return ``None`` if no defect exists."""
    output = BytesIO()
    with ZipFile(source, "r") as original:
        replacements: dict[str, bytes] = {}
        try:
            styles_xml = original.read(_STYLES_PATH)
        except KeyError:
            styles_xml = b""
        if styles_xml:
            repaired_styles, repair_count = _repair_styles_xml(styles_xml)
            if repair_count:
                replacements[_STYLES_PATH] = repaired_styles

        for entry in original.infolist():
            if not (entry.filename.startswith("xl/charts/")
                    and entry.filename.endswith(".xml")):
                continue
            chart_xml = original.read(entry.filename)
            repaired_chart, repair_count = _EMPTY_CHART_GROUPING_RE.subn(
                _STANDARD_CHART_GROUPING_XML, chart_xml
            )
            if repair_count:
                replacements[entry.filename] = repaired_chart

        if not replacements:
            return None

        with ZipFile(output, "w", compression=ZIP_DEFLATED) as repaired:
            for entry in original.infolist():
                payload = replacements.get(entry.filename)
                if payload is None:
                    payload = original.read(entry.filename)
                repaired.writestr(entry, payload)

    output.seek(0)
    return output


def load_workbook_compat(filename: str | Path, **kwargs: Any):
    """Load an XLSX, repairing only the known malformed-empty-fill defect.

    The source file is never modified.  All unrelated parsing errors are
    re-raised unchanged so genuinely damaged workbooks are not silently hidden.
    """
    # Inspect styles first instead of deliberately triggering openpyxl's error.
    # On Windows, openpyxl 3.1.5 can leave the source ZIP handle open when style
    # parsing raises, which then prevents cleanup or replacement of the file.
    repaired_stream = _repaired_workbook_stream(Path(filename))
    if repaired_stream is not None:
        return load_workbook(repaired_stream, **kwargs)
    return load_workbook(filename, **kwargs)
