"""Turn a `Result` into a downloadable file.

Four formats, each aimed at a different reader:

* **txt** - just the processed text, nothing else. Feed it to another tool.
* **csv** - opens in Excel with Arabic intact. Tabular input keeps the user's
  own columns; pasted text gets a token-level breakdown for the tagging tasks.
* **md** - a readable document with the run's metadata at the top.
* **pdf** - the same, typeset right-to-left with a real Arabic font.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .process import TASKS, Result
from .security import csv_safe

CREDIT = ("Processed with khair-arabic-models by Mohammad Mohammad Khair, "
          "MS, EMBA - International Computing Institute for Quran and Islamic "
          "Sciences (QuranComputing.org), part of the Hadith.chat project. "
          "Apache 2.0.")

MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _meta(result: Result) -> list[tuple[str, str]]:
    return [
        ("Task", result.task_label),
        ("Source", result.source_name),
        ("Units", f"{len(result.units):,}"),
        ("Processed", _stamp()),
        ("Time", f"{result.elapsed_s:.1f}s"),
    ]


# --------------------------------------------------------------------------

def as_txt(result: Result) -> bytes:
    body = "\n".join(u.output for u in result.units)
    return body.encode("utf-8")


def as_csv(result: Result) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    task = TASKS[result.task]
    table = result.document.table

    if table is not None:
        # Hand back the user's own spreadsheet with one column added, so the
        # result drops straight back into whatever they were building.
        writer.writerow([csv_safe(c) for c in table.columns] + [task.key])
        for row, unit in zip(table.rows, result.units):
            writer.writerow([csv_safe(c) for c in row] + [csv_safe(unit.output)])
    elif task.pair_columns:
        # One row per word (POS) or per segment (structure): the shape you
        # actually want to sort, filter and pivot on.
        left, right = task.pair_columns
        writer.writerow(["line", left, right])
        for unit in result.units:
            for a, b in unit.pairs:
                writer.writerow([unit.n, csv_safe(a), csv_safe(b)])
    else:
        writer.writerow(["line", "input", "output"])
        for unit in result.units:
            if unit.source.strip():
                writer.writerow([unit.n, csv_safe(unit.source), csv_safe(unit.output)])

    # The BOM is what makes Excel on Windows read the file as UTF-8 rather
    # than mangling every Arabic character.
    return buffer.getvalue().encode("utf-8-sig")


def as_markdown(result: Result) -> bytes:
    out = [f"# {result.task_label}", ""]
    out += [f"- **{k}:** {v}" for k, v in _meta(result)]
    for note in result.notes:
        out.append(f"- **Note:** {note}")
    out += ["", "---", ""]

    table = result.document.table
    if table is not None:
        header = table.columns + [result.task]
        out.append("| " + " | ".join(_md_cell(c) for c in header) + " |")
        out.append("|" + "|".join([" --- "] * len(header)) + "|")
        for row, unit in zip(table.rows, result.units):
            cells = [_md_cell(c) for c in row] + [_md_cell(unit.output)]
            out.append("| " + " | ".join(cells) + " |")
    else:
        for unit in result.units:
            out.append(unit.output if unit.output.strip() else "")
            out.append("")

    out += ["", "---", "", f"*{CREDIT}*", ""]
    return "\n".join(out).encode("utf-8")


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

_FONT_SEARCH = (
    Path(__file__).with_name("fonts"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/opentype/noto"),
    Path("C:/Windows/Fonts"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
)
# Ordered best-first: a Naskh face renders diacritics legibly, Arial and
# Tahoma merely render them.
_FONT_NAMES = ("NotoNaskhArabic-Regular.ttf", "Amiri-Regular.ttf",
               "NotoSansArabic-Regular.ttf", "Scheherazade-Regular.ttf",
               "arial.ttf", "Arial.ttf", "tahoma.ttf", "Tahoma.ttf",
               "segoeui.ttf", "DejaVuSans.ttf")


def find_font() -> Path | None:
    """Locate a TrueType face that can draw Arabic, or return None.

    PDF export is the one feature with a hard external dependency - a PDF
    embeds its font, so there is no system fallback to lean on. Rather than
    ship a 500 KB binary in the repository we look in the usual places and
    let the caller disable the button if nothing turns up.
    """
    if settings.pdf_font:
        return settings.pdf_font if settings.pdf_font.is_file() else None
    for directory in _FONT_SEARCH:
        if not directory.is_dir():
            continue
        for name in _FONT_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def pdf_available() -> bool:
    try:
        import fpdf  # noqa: F401
    except ImportError:
        return False
    return find_font() is not None


def _shaper():
    """Return a function that puts Arabic into the visual order a PDF needs.

    A PDF has no text engine: it draws glyphs at coordinates, so the letters
    have to be joined into their initial/medial/final forms and reordered
    right-to-left *before* they are written. HarfBuzz does this properly when
    it is installed; `arabic_reshaper` plus the bidi algorithm is the pure
    Python fallback, and `delete_harakat=False` is essential here because the
    diacritics are the entire output of three of our four tasks.
    """
    import arabic_reshaper
    reshaper = arabic_reshaper.ArabicReshaper(configuration={
        "delete_harakat": False,
        "support_ligatures": True,
    })
    try:
        from bidi import get_display
    except ImportError:
        from bidi.algorithm import get_display

    def shape(text: str) -> str:
        if not text.strip():
            return text
        return get_display(reshaper.reshape(text))

    return shape


def as_pdf(result: Result) -> bytes:
    from fpdf import FPDF

    font = find_font()
    if font is None:
        raise RuntimeError(
            "PDF export needs an Arabic TrueType font. Install one (on Debian: "
            "`apt install fonts-noto-core`) or point ARABICWEB_PDF_FONT at a "
            ".ttf file.")

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(16, 16, 16)
    pdf.add_font("arabic", "", str(font))
    pdf.set_font("arabic", size=12)

    # fpdf2 shapes and reorders text itself when uharfbuzz is present, which
    # is better than anything we can do by hand. Only fall back if it is not.
    shape = str
    try:
        pdf.set_text_shaping(True)
    except Exception:                       # noqa: BLE001 - any failure = fallback
        shape = _shaper()

    pdf.add_page()
    pdf.set_font_size(17)
    pdf.multi_cell(0, 10, shape(result.task_label), align="R")
    pdf.set_font_size(9)
    pdf.set_text_color(110)
    for key, value in _meta(result):
        pdf.multi_cell(0, 5, shape(f"{value}  :{key}"), align="R")
    for note in result.notes:
        pdf.multi_cell(0, 5, shape(note), align="R")
    pdf.ln(3)
    pdf.set_draw_color(200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    pdf.set_text_color(0)
    pdf.set_font_size(13)
    for unit in result.units:
        if not unit.output.strip():
            pdf.ln(3)
            continue
        for line in unit.output.split("\n"):
            pdf.multi_cell(0, 9, shape(line), align="R")
        pdf.ln(1)

    pdf.ln(6)
    pdf.set_font_size(7)
    pdf.set_text_color(130)
    pdf.multi_cell(0, 4, shape(CREDIT), align="R")

    return bytes(pdf.output())


# --------------------------------------------------------------------------

RENDERERS = {"txt": as_txt, "csv": as_csv, "md": as_markdown, "pdf": as_pdf}


def render(result: Result, fmt: str) -> bytes:
    renderer = RENDERERS.get(fmt)
    if renderer is None:
        raise ValueError(f"unknown format '{fmt}'")
    return renderer(result)


def filename(result: Result, fmt: str) -> str:
    """Build the download name from the user's own filename, safely.

    `result.source_name` has already been through `sanitize_filename`, so all
    that is left is to drop its extension and append ours - the user never
    gets to choose the final suffix.
    """
    stem = os.path.splitext(result.source_name)[0] or "result"
    return f"{stem}.{result.task}.{fmt}"
