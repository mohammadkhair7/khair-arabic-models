"""The four tasks the service exposes, and the model registry behind them.

Checkpoints are loaded lazily and then kept warm: the first request for a task
pays ~2-4 seconds of load time and every later one pays only inference. A lock
guards the load so that two simultaneous first-requests do not each build a
copy of the same model.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .config import settings
from .extract import Document


@dataclass
class Unit:
    """One processed line/paragraph/row."""
    n: int
    source: str
    output: str
    # (token, label) for the tagging tasks; empty for diacritization. The UI
    # uses it to colour words, and the CSV/Markdown exporters to add columns.
    pairs: list[tuple[str, str]] = field(default_factory=list)


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
                    "tashkeel_plain": lambda: Diacritizer.load(pos=False, device=device),
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


def _run_tashkeel_plain(text: str) -> tuple[str, list]:
    return _get("tashkeel_plain").diacritize(text), []


def _run_pos(text: str) -> tuple[str, list]:
    pairs = _get("pos").tag(text)
    return " ".join(f"{w}/{t}" for w, t in pairs), pairs


def _run_structure(text: str) -> tuple[str, list]:
    segments = _get("structure").segments(text)
    return "\n".join(f"[{label}] {chunk}" for label, chunk in segments), segments


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    label_ar: str
    description: str
    description_ar: str
    run: object
    # Column headings the exporters add for this task's `pairs`.
    pair_columns: tuple[str, str] | None = None


TASKS: dict[str, Task] = {t.key: t for t in (
    Task("tashkeel", "Diacritize (tashkīl)", "التشكيل الكامل",
         "Restore every diacritic from the bare letters, using the "
         "grammar-aware v0.2 model.",
         "إعادة بناء جميع الحركات من الحروف المجردة بالنموذج النحوي (الإصدار ٢).",
         _run_tashkeel),
    Task("tashkeel_gaps", "Fill missing diacritics", "إكمال الحركات الناقصة",
         "Keep the diacritics the text already has and only vowel the words "
         "that are completely bare. Quranic quotations are left untouched.",
         "الإبقاء على الحركات الموجودة وتشكيل الكلمات المجردة فقط، مع عدم المساس "
         "بالآيات القرآنية.",
         _run_tashkeel_gaps),
    Task("tashkeel_plain", "Diacritize (v0.1, no grammar)", "التشكيل (الإصدار ١)",
         "The earlier character-only diacritizer. Useful for comparison.",
         "النموذج الأول المعتمد على الحروف فقط، للمقارنة.",
         _run_tashkeel_plain),
    Task("pos", "Part-of-speech tags", "الوسم الصرفي",
         "Label every word with one of 24 parts of speech.",
         "وسم كل كلمة بأحد أربعة وعشرين قسمًا نحويًا.",
         _run_pos, pair_columns=("word", "tag")),
    Task("structure", "Hadith structure", "بنية الحديث",
         "Split running text into hadith number, isnād, matn and heading.",
         "تقسيم النص إلى رقم الحديث والإسناد والمتن والعنوان.",
         _run_structure, pair_columns=("segment", "label")),
)}


def process(document: Document, task_key: str) -> Result:
    """Run one task over every non-empty unit of `document`.

    Blank units are passed through untouched rather than sent to a model:
    they cost nothing and preserving them keeps the output line-aligned with
    the input, which matters when someone diffs the two.
    """
    task = TASKS.get(task_key)
    if task is None:
        raise ValueError(f"unknown task '{task_key}'")

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
        units.append(Unit(i, source, output, list(pairs)))

    # Keep the table rectangular if the timeout cut us short.
    if document.table is not None and len(units) < len(document.table.rows):
        document.table.rows = document.table.rows[:len(units)]

    return Result(task=task.key, task_label=task.label, units=units,
                  document=document, elapsed_s=time.perf_counter() - started,
                  notes=notes + document.notes)
