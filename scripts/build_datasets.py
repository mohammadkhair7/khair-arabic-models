"""Build the three training datasets from a corpus of plain Arabic text.

This is the corpus-agnostic version of the pipeline described in
`docs/PAPER.md` §4. It reproduces the same filters that produced the shipped
datasets under `data/`, but reads from ordinary files instead of the private
database the original corpus lived in (see `docs/DATA.md` for provenance).

The three label sources need nothing but the text itself:

  tashkeel  the vocalized text IS the reference — strip the marks to get the
            input. Requires text that carries diacritics.
  indexing  the rule-based isnād parser supplies sanad/matn boundaries; only
            its confident output is kept.
  pos       the CAMeL Tools morphology engine supplies silver tags.
            Requires: pip install "arabicmodels[teacher]"
                      camel_data -i morphology-db-msa-r13

Input formats (`--input`):
  * a `.jsonl` file with one `{"text": "..."}` object per line
  * a `.txt` file, or a directory of them (see `--record`)

Examples:
    python scripts/build_datasets.py --task tashkeel --input corpus/ \
        --tashkeel-windows 70000
    python scripts/build_datasets.py --task indexing --input units.jsonl \
        --headings toc_titles.txt
    python scripts/build_datasets.py --task pos --input units.jsonl \
        --pos-passages 4000
"""
import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterator

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from arabicmodels.common import DATA_DIR, split_of  # noqa: E402
from arabicmodels.isnad import parse_isnad  # noqa: E402

_D = re.compile(r"[\u064B-\u0652]")
_AR = re.compile(r"[\u0621-\u064A]")
# viewer artefacts commonly left in exported digital-library text
_JUNK = re.compile(r"AddHistory\([^)]*\)[^;]*;?|\[\d+/\d+\]")


# --- input -----------------------------------------------------------------

def iter_texts(path: Path, record: str) -> Iterator[str]:
    """Yield one text per corpus record."""
    if path.is_dir():
        for f in sorted(path.rglob("*.txt")):
            yield from iter_texts(f, record)
        for f in sorted(path.rglob("*.jsonl")):
            yield from iter_texts(f, record)
        return
    if path.suffix == ".jsonl":
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    text = obj.get("text", "")
                    if text.strip():
                        yield text
        return
    raw = path.read_text(encoding="utf-8-sig")
    if record == "file":
        if raw.strip():
            yield raw
    elif record == "blank-line":
        for block in re.split(r"\n\s*\n", raw):
            if block.strip():
                yield block.strip()
    else:  # line
        for line in raw.splitlines():
            if line.strip():
                yield line.strip()


def _write(name: str, rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts = dict(Counter(r["split"] for r in rows))
    print(f"{path}: {len(rows):,} samples {counts}")


# --- tashkeel: the text is the labeler --------------------------------------

def _windows(text: str, max_chars: int = 380) -> list[str]:
    """Cut text into windows of at most `max_chars`, on word boundaries."""
    words = text.split()
    out, cur, ln = [], [], 0
    for w in words:
        if ln + len(w) + 1 > max_chars and cur:
            out.append(" ".join(cur))
            cur, ln = [], 0
        cur.append(w)
        ln += len(w) + 1
    if cur:
        out.append(" ".join(cur))
    return out


def build_tashkeel(texts: Iterator[str], target: int, out_dir: Path,
                   min_density: float = 0.6, min_letters: int = 60) -> None:
    """Keep windows that are densely vocalized; they are their own labels."""
    rows: list[dict] = []
    for text in texts:
        text = _JUNK.sub(" ", text)
        for win in _windows(text):
            letters = len(_AR.findall(win))
            if letters < min_letters:
                continue
            if len(_D.findall(win)) / letters < min_density:
                continue
            rows.append({"text": win, "split": split_of(win)})
            if len(rows) >= target:
                break
        if len(rows) >= target:
            break
    if not rows:
        sys.exit("no vocalized windows found — is the input text diacritized?")
    _write("tashkeel", rows, out_dir)


# --- indexing: the rules are the labeler ------------------------------------

def build_indexing(texts: Iterator[str], target_units: int, out_dir: Path,
                   headings_path: Path | None, target_headings: int,
                   min_conf: float = 0.9, min_offset: int = 30,
                   seed: int = 13) -> None:
    """Label units with the isnād parser's confident sanad/matn boundaries."""
    rows: list[dict] = []
    seen = kept = 0
    for text in texts:
        seen += 1
        if not (150 <= len(text) <= 4000):
            continue
        p = parse_isnad(text)
        if p.confidence < min_conf or p.sanad_end_raw <= min_offset:
            continue
        rows.append({"text": text, "sanad_end": p.sanad_end_raw,
                     "split": split_of(text)})
        kept += 1
        if kept >= target_units:
            break
    print(f"  isnad parser: {kept:,} confident boundaries out of {seen:,} units")

    if headings_path:
        titles = [t for t in iter_texts(headings_path, "line")
                  if 15 <= len(t) <= 200]
        random.Random(seed).shuffle(titles)
        for t in titles[:target_headings]:
            rows.append({"text": t, "kind": "heading", "split": split_of(t)})
        print(f"  headings: {min(len(titles), target_headings):,}")
    if not rows:
        sys.exit("no confident boundaries found — check the input granularity "
                 "(one hadith unit per record)")
    _write("indexing", rows, out_dir)


# --- pos: the engine is the labeler -----------------------------------------

def build_pos(texts: Iterator[str], target: int, out_dir: Path,
              seed: int = 13) -> None:
    """Silver POS tags from the CAMeL morphology engine."""
    from arabicmodels.teachers import CamelMorphologyEngine
    from arabicmodels.tokenization import whitespace_tokenize

    eng = CamelMorphologyEngine()
    if not eng.available():
        sys.exit(f"CAMeL teacher unavailable: {eng.unavailable_reason}")

    pool = [t for t in texts if 200 <= len(t) <= 2500]
    random.Random(seed).shuffle(pool)
    pool = pool[:target]

    out: list[dict] = []
    batch_size = 32
    for i in range(0, len(pool), batch_size):
        batch = [_JUNK.sub(" ", t) for t in pool[i:i + batch_size]]
        anns = eng.annotate_batch(batch)
        for text, ann in zip(batch, anns):
            toks = [t.text for t in whitespace_tokenize(text)]
            tags = {p["token_idx"]: p["tag"] for p in ann["pos"]}
            tag_list = [tags.get(j, "x") or "x" for j in range(len(toks))]
            if len(toks) >= 8:
                out.append({"tokens": toks, "tags": tag_list,
                            "split": split_of(text)})
        if (i // batch_size) % 10 == 0:
            print(f"  pos: {min(i + batch_size, len(pool))}/{len(pool)}", flush=True)
    if not out:
        sys.exit("no taggable passages found")
    _write("pos", out, out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", default="all",
                    choices=["all", "tashkeel", "pos", "indexing"])
    ap.add_argument("--input", required=True, type=Path,
                    help="corpus file (.jsonl/.txt) or directory")
    ap.add_argument("--record", default="line",
                    choices=["line", "blank-line", "file"],
                    help="how to split plain .txt input into records")
    ap.add_argument("--out-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--headings", type=Path,
                    help="one section title per line (indexing HEADING class)")
    ap.add_argument("--tashkeel-windows", type=int, default=70000)
    ap.add_argument("--pos-passages", type=int, default=4000)
    ap.add_argument("--indexing-units", type=int, default=40000)
    ap.add_argument("--indexing-headings", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"input not found: {args.input}")

    def texts() -> Iterator[str]:
        return iter_texts(args.input, args.record)

    if args.task in ("all", "tashkeel"):
        print("building tashkeel dataset (text as labeler)…")
        build_tashkeel(texts(), args.tashkeel_windows, args.out_dir)
    if args.task in ("all", "indexing"):
        print("building indexing dataset (rules as labeler)…")
        build_indexing(texts(), args.indexing_units, args.out_dir,
                       args.headings, args.indexing_headings, seed=args.seed)
    if args.task in ("all", "pos"):
        print("building pos dataset (engine as labeler)…")
        build_pos(texts(), args.pos_passages, args.out_dir, seed=args.seed)


if __name__ == "__main__":
    main()
