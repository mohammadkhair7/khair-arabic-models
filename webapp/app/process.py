"""The four tasks the service exposes, and the model registry behind them.

Checkpoints are loaded lazily and then kept warm: the first request for a task
pays ~2-4 seconds of load time and every later one pays only inference. A lock
guards the load so that two simultaneous first-requests do not each build a
copy of the same model.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace

from . import labels
from .config import settings
from .extract import Document


@dataclass
class Narrator:
    """One link of an isnād: the verb that hands the report on, and the man
    it is handed to. `seg` is the index of the ISNAD pair this came from, so
    the page can print the chain under the right segment when a unit holds
    more than one hadith."""
    seg: int
    n: int
    verb: str
    name: str


@dataclass
class Unit:
    """One processed line/paragraph/row."""
    n: int
    source: str
    output: str
    # (token, tag code) for the tagging tasks; empty for diacritization. The
    # codes stay canonical here - `labels.name` turns them into English or
    # Arabic at the moment of display, so one result can be shown either way.
    pairs: list[tuple[str, str]] = field(default_factory=list)
    # Narrators of every ISNAD segment above, in transmission order. Structure
    # task only; empty when the model found no chain.
    narrators: list[Narrator] = field(default_factory=list)


@dataclass
class Result:
    task: str
    task_label: str
    units: list[Unit]
    document: Document
    elapsed_s: float
    notes: list[str] = field(default_factory=list)
    scan_status: str = "skipped"
    source_name: str = "pasted text"
    # Language the tag names are shown in. Not the language of the text - the
    # text is always Arabic - only of the labels wrapped around it.
    lang: str = labels.DEFAULT_LANGUAGE


# --------------------------------------------------------------------------
# lazy model registry
# --------------------------------------------------------------------------

_lock = threading.Lock()
_loaded: dict[str, object] = {}


def _get(key: str):
    if key not in _loaded:
        with _lock:
            if key not in _loaded:
                from arabicmodels import Diacritizer, PosTagger, StructureTagger
                device = settings.device
                builder = {
                    "tashkeel": lambda: Diacritizer.load(pos=True, device=device),
                    "pos": lambda: PosTagger.load(device=device),
                    "structure": lambda: StructureTagger.load(device=device),
                }[key]
                _loaded[key] = builder()
    return _loaded[key]


def preload() -> None:
    for key in ("tashkeel", "pos", "structure"):
        _get(key)


def loaded_tasks() -> list[str]:
    return sorted(_loaded)


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------

def _run_tashkeel(text: str) -> tuple[str, list]:
    return _get("tashkeel").diacritize(text), []


def _run_tashkeel_gaps(text: str) -> tuple[str, list]:
    return _get("tashkeel").fill_gaps(text), []


def _run_pos(text: str) -> tuple[str, list]:
    return "", _get("pos").tag(text)


def _run_structure(text: str) -> tuple[str, list]:
    return "", _get("structure").segments(text)


def _narrators(pairs: list[tuple[str, str]]) -> list[Narrator]:
    """Split every ISNAD segment the model found into its narrators.

    Deliberately driven off `pairs` rather than off the unit's raw text: the
    rules then divide exactly the span the page shades as the chain, and the
    two readings on screen cannot disagree.
    """
    from arabicmodels import StructureTagger

    out: list[Narrator] = []
    for i, (label, chunk) in enumerate(pairs):
        if label != "ISNAD":
            continue
        for n, hop in enumerate(StructureTagger.narrators(chunk), start=1):
            out.append(Narrator(seg=i, n=n,
                                verb=chunk[hop.verb_start:hop.verb_end],
                                name=chunk[hop.start:hop.end]))
    return out


def _format_pairs(kind: str, pairs: list, lang: str,
                  narrators: list[Narrator] = ()) -> str:
    """Render tagged output as text, in the reader's language.

    POS pairs are (word, tag) and read left-to-right as `word/tag`; structure
    pairs are (label, chunk) and lead with the bracketed label, with each
    chain's narrators listed under it.

    Every text export goes through here, which is why the narrators are woven
    into the text rather than appended to the exporters one at a time: .txt,
    Markdown, the PDF and the copy button then cannot disagree about what the
    chain was.
    """
    if kind == "pos":
        return " ".join(f"{w}/{labels.name('pos', t, lang)}" for w, t in pairs)

    words = labels.narrator_words(lang)
    lines: list[str] = []
    for i, (label, chunk) in enumerate(pairs):
        lines.append(f"[{labels.name('structure', label, lang)}] {chunk}")
        chain = [h for h in narrators if h.seg == i]
        if chain:
            lines.append(f"    {words['heading']}:")
            lines += [f"      {h.n}. {h.verb} — {h.name}" for h in chain]
    return "\n".join(lines)


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    label_ar: str
    description: str
    description_ar: str
    run: object
    # Which tag set this task's `pairs` are drawn from, or None if it emits
    # plain text. Drives both the exporters' columns and the label language.
    tag_kind: str | None = None


TASKS: dict[str, Task] = {t.key: t for t in (
    Task("tashkeel", "Diacritize (tashkīl)", "التشكيل الكامل",
         "Restore every diacritic from the bare letters, using the "
         "grammar-aware model.",
         "إعادة بناء جميع الحركات من الحروف المجردة بالنموذج النحوي.",
         _run_tashkeel),
    Task("tashkeel_gaps", "Fill missing diacritics", "إكمال الحركات الناقصة",
         "Keep the diacritics the text already has and only vowel the words "
         "that are completely bare. Quranic quotations are left untouched.",
         "الإبقاء على الحركات الموجودة وتشكيل الكلمات المجردة فقط، مع عدم المساس "
         "بالآيات القرآنية.",
         _run_tashkeel_gaps),
    Task("pos", "Part-of-speech tags", "الوسم الصرفي",
         "Label every word with one of 24 parts of speech.",
         "وسم كل كلمة بأحد أربعة وعشرين قسمًا نحويًا.",
         _run_pos, tag_kind="pos"),
    Task("structure", "Hadith structure", "بنية الحديث",
         "Split running text into hadith number, isnād, matn and heading.",
         "تقسيم النص إلى رقم الحديث والإسناد والمتن والعنوان.",
         _run_structure, tag_kind="structure"),
)}


def relabel(result: Result, lang: str) -> Result:
    """Return the same result with its tag names in another language.

    Because `Unit.pairs` holds canonical codes, switching language is pure
    string formatting - no model runs again. That is what lets the page offer
    an instant English/Arabic toggle on a result that took a minute to
    compute.
    """
    lang = labels.normalize(lang)
    if lang == result.lang:
        return result
    task = TASKS[result.task]
    units = result.units
    if task.tag_kind:
        units = [Unit(u.n, u.source,
                      _format_pairs(task.tag_kind, u.pairs, lang, u.narrators)
                      if u.pairs else u.output,
                      u.pairs, u.narrators)
                 for u in result.units]
    return replace(result, units=units, lang=lang,
                   task_label=task.label_ar if lang == "ar" else task.label)


def process(document: Document, task_key: str,
            lang: str = labels.DEFAULT_LANGUAGE) -> Result:
    """Run one task over every non-empty unit of `document`.

    Blank units are passed through untouched rather than sent to a model:
    they cost nothing and preserving them keeps the output line-aligned with
    the input, which matters when someone diffs the two.
    """
    task = TASKS.get(task_key)
    if task is None:
        raise ValueError(f"unknown task '{task_key}'")

    lang = labels.normalize(lang)
    started = time.perf_counter()
    deadline = started + settings.process_timeout_s
    units: list[Unit] = []
    notes: list[str] = []

    for i, source in enumerate(document.units, start=1):
        if not source.strip():
            units.append(Unit(i, source, source))
            continue
        if time.perf_counter() > deadline:
            notes.append(
                f"stopped after {settings.process_timeout_s}s - "
                f"{len(document.units) - i + 1:,} of "
                f"{len(document.units):,} units were not processed")
            break
        output, pairs = task.run(source)
        pairs = list(pairs)
        narrators = _narrators(pairs) if task.key == "structure" else []
        if task.tag_kind:
            output = _format_pairs(task.tag_kind, pairs, lang, narrators)
        units.append(Unit(i, source, output, pairs, narrators))

    # Keep the table rectangular if the timeout cut us short.
    if document.table is not None and len(units) < len(document.table.rows):
        document.table.rows = document.table.rows[:len(units)]

    return Result(task=task.key,
                  task_label=task.label_ar if lang == "ar" else task.label,
                  units=units, document=document,
                  elapsed_s=time.perf_counter() - started,
                  notes=notes + document.notes, lang=lang)
