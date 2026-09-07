"""Model A — text-structure segmentation.

Word-level sequence labeling of running hadith page text into four classes:

    HNUM     printed hadith number («1248 -»)
    ISNAD    the transmission chain (sanad)
    MATN     the report body
    HEADING  كتاب / باب / فصل section titles

The formulation is token *classification*, not generation: the output is a
label per word, so every decision is auditable and the source text is never
rewritten.

Ground truth is harvested from the corpus itself (`docs/PAPER.md` §4.3): the
rule-based isnād parser supplies confident sanad/matn boundaries and tables
of contents supply headings — no manual annotation.

CLI:
    python -m arabicmodels.indexing train --epochs 3
    python -m arabicmodels.indexing eval
    python -m arabicmodels.indexing infer --text "1248 - حدثنا ..."
    python -m arabicmodels.indexing spans --file page.txt --out spans.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

from .common import (DATA_DIR, MODELS_DIR, Vocab, WordTagger, device,
                     encode_words, load_ckpt, pad_batch, pad_words,
                     read_jsonl, save_ckpt, seed_all)

CKPT = MODELS_DIR / "indexing_wordtagger.pt"
DATA = DATA_DIR / "indexing.jsonl"
TAGS = ["HNUM", "ISNAD", "MATN", "HEADING"]
MAX_WORDS = 220
_NUM = re.compile(r"^[\d\u0660-\u0669]+[-–.)\]]*$")


def _words_with_offsets(text: str) -> list[tuple[str, int]]:
    return [(m.group(), m.start()) for m in re.finditer(r"\S+", text)]


def _label_unit(text: str, sanad_end: int) -> tuple[list[str], list[str]]:
    """Turn a boundary offset into per-word labels (the labeling rule)."""
    words, tags = [], []
    for w, off in _words_with_offsets(text)[:MAX_WORDS]:
        words.append(w)
        if _NUM.match(w) and not tags:
            tags.append("HNUM")
        elif off < sanad_end:
            tags.append("ISNAD")
        else:
            tags.append("MATN")
    return words, tags


def _prepare(rows: list[dict]) -> list[tuple[list[str], list[str]]]:
    out = []
    for r in rows:
        if r.get("kind") == "heading":
            words = [w for w, _ in _words_with_offsets(r["text"])[:MAX_WORDS]]
            if len(words) >= 2:
                out.append((words, ["HEADING"] * len(words)))
        elif r.get("sanad_end", 0) > 0:
            words, tags = _label_unit(r["text"], r["sanad_end"])
            if len(words) >= 6 and "MATN" in tags and "ISNAD" in tags:
                out.append((words, tags))
    return out


def _batches(data, cvocab, tvocab, batch_size, shuffle):
    import random
    idx = list(range(len(data)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), batch_size):
        chunk = [data[j] for j in idx[i:i + batch_size]]
        x = pad_words([encode_words(w, cvocab) for w, _ in chunk])
        y = pad_batch([tvocab.encode(t) for _, t in chunk], pad=-100)
        yield x, y, chunk


@torch.no_grad()
def _evaluate(model, data, cvocab, tvocab, dev) -> dict:
    model.eval()
    total = errs = 0
    boundary_hits = boundary_total = 0
    boundary_word_err = []
    matn_id = tvocab.stoi["MATN"]
    for x, y, chunk in _batches(data, cvocab, tvocab, 64, shuffle=False):
        pred = model(x.to(dev)).argmax(-1).cpu()
        mask = y != -100
        total += int(mask.sum())
        errs += int((pred[mask] != y[mask]).sum())
        for bi, (words, tags) in enumerate(chunk):
            if "ISNAD" not in tags or "MATN" not in tags:
                continue
            gold_b = tags.index("MATN")
            p = pred[bi][:len(words)].tolist()
            pred_b = next((i for i, t in enumerate(p) if t == matn_id), -1)
            boundary_total += 1
            if pred_b >= 0:
                d = abs(pred_b - gold_b)
                boundary_word_err.append(d)
                if d <= 2:
                    boundary_hits += 1
    boundary_word_err.sort()
    return {
        "word_accuracy": 1 - errs / max(total, 1),
        "tokens": total,
        "boundary_within_2_words": boundary_hits / max(boundary_total, 1),
        "boundary_median_word_err": (boundary_word_err[len(boundary_word_err) // 2]
                                     if boundary_word_err else None),
        "boundaries": boundary_total,
    }


def train(args) -> None:
    seed_all()
    dev = device()
    rows = read_jsonl(DATA, limit=args.limit)
    data = {s: [] for s in ("train", "dev", "test")}
    for r in rows:
        data[r["split"]].append(r)
    tr, dv = _prepare(data["train"]), _prepare(data["dev"])
    cvocab = Vocab.build((list("".join(w)) for w, _ in tr), max_size=400)
    tvocab = Vocab(dict({"<pad>": 0, "<unk>": 1},
                        **{t: i + 2 for i, t in enumerate(TAGS)}))
    model = WordTagger(len(cvocab), len(tvocab)).to(dev)
    print(f"device={dev} train={len(tr)} dev={len(dv)} "
          f"params={sum(p.numel() for p in model.parameters()):,}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    lossf = nn.CrossEntropyLoss(ignore_index=-100)
    best = 0.0
    for ep in range(1, args.epochs + 1):
        model.train()
        t0, tot, nb = time.time(), 0.0, 0
        for x, y, _ in _batches(tr, cvocab, tvocab, args.batch_size, shuffle=True):
            opt.zero_grad()
            out = model(x.to(dev))
            loss = lossf(out.reshape(-1, out.shape[-1]), y.to(dev).reshape(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            opt.step()
            tot += float(loss)
            nb += 1
            if nb % 200 == 0:
                print(f"  ep{ep} batch {nb} loss {tot/nb:.4f}", flush=True)
        m = _evaluate(model, dv, cvocab, tvocab, dev)
        print(f"epoch {ep}: loss {tot/max(nb,1):.4f} dev acc {m['word_accuracy']:.4f} "
              f"boundary±2w {m['boundary_within_2_words']:.4f} ({time.time()-t0:.0f}s)",
              flush=True)
        if m["word_accuracy"] > best:
            best = m["word_accuracy"]
            save_ckpt(CKPT, model, {"char_vocab": cvocab.stoi, "tag_vocab": tvocab.stoi,
                                    "metrics": m, "task": "indexing", "version": "0.2"})
            print(f"  saved -> {CKPT}")


def load_model(path: Path | str | None = None):
    """Load the structure tagger. Returns (model, char_vocab, tag_vocab)."""
    ck = load_ckpt(Path(path) if path else CKPT)
    cvocab, tvocab = Vocab(ck["char_vocab"]), Vocab(ck["tag_vocab"])
    model = WordTagger(len(cvocab), len(tvocab))
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, cvocab, tvocab


def evaluate(args) -> None:
    dev = device()
    rows = [r for r in read_jsonl(DATA, limit=args.limit) if r["split"] == "test"]
    model, cvocab, tvocab = load_model()
    model.to(dev)
    m = _evaluate(model, _prepare(rows), cvocab, tvocab, dev)
    print(f"test tokens={m['tokens']} boundaries={m['boundaries']}")
    print(f"word accuracy:          {m['word_accuracy']:.4f}")
    print(f"matn boundary ±2 words: {m['boundary_within_2_words']:.4f}")
    print(f"boundary median error:  {m['boundary_median_word_err']} words")


@torch.no_grad()
def tag_words(model, cvocab, tvocab, dev, words: list[str]) -> list[str]:
    """Label a single sequence of words (truncated to MAX_WORDS)."""
    if not words:
        return []
    itos = tvocab.itos()
    x = pad_words([encode_words(words[:MAX_WORDS], cvocab)]).to(dev)
    pred = model(x).argmax(-1)[0][:len(words)].tolist()
    tags = [itos.get(t, "?") for t in pred]
    return tags + ["?"] * (len(words) - len(tags))


@torch.no_grad()
def infer(args) -> None:
    text = args.text or Path(args.file).read_text(encoding="utf-8")
    model, cvocab, tvocab = load_model()
    dev = device()
    model.to(dev)
    out = []
    for line in text.splitlines() or [text]:
        words = line.split()
        if not words:
            continue
        tags = tag_words(model, cvocab, tvocab, dev, words)
        # assemble contiguous spans into display segments
        spans: list[tuple[str, list[str]]] = []
        for w, tg in zip(words, tags):
            if spans and spans[-1][0] == tg:
                spans[-1][1].append(w)
            else:
                spans.append((tg, [w]))
        out.append("\n".join(f"[{tg}] {' '.join(ws)}" for tg, ws in spans))
    sys.stdout.buffer.write(("\n\n".join(out) + "\n").encode("utf-8"))


@torch.no_grad()
def page_spans(model, cvocab, tvocab, dev, text: str) -> list[list]:
    """Label a full page and merge contiguous same-label words into
    [start, end, label] spans over RAW character offsets.

    Pages longer than MAX_WORDS are processed in consecutive blocks; see
    `docs/PAPER.md` §8.3 and the matching limitation in §10.
    """
    words = _words_with_offsets(text)
    if len(words) < 12:
        return []
    itos = tvocab.itos()
    spans: list[list] = []
    for i in range(0, len(words), MAX_WORDS):
        chunk = words[i:i + MAX_WORDS]
        x = pad_words([encode_words([w for w, _ in chunk], cvocab)]).to(dev)
        tags = model(x).argmax(-1)[0][:len(chunk)].tolist()
        for (w, off), t in zip(chunk, tags):
            label = itos.get(t, "?")
            end = off + len(w)
            if spans and spans[-1][2] == label:
                spans[-1][1] = end
            else:
                spans.append([off, end, label])
    return spans


def usable_spans(spans: list[list]) -> bool:
    """True when the page really contains hadith anatomy: at least one ISNAD
    span and one MATN span of substance. Used to skip front matter, indexes
    and other non-hadith pages."""
    isnad = sum(e - s for s, e, label in spans if label == "ISNAD")
    matn = sum(e - s for s, e, label in spans if label == "MATN")
    return isnad >= 30 and matn >= 40


def spans_cmd(args) -> None:
    """Batch structure annotation over text files -> JSON spans."""
    paths = [Path(p) for p in args.files]
    model, cvocab, tvocab = load_model()
    dev = device()
    model.to(dev)
    results = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        sp = page_spans(model, cvocab, tvocab, dev, text)
        results.append({"file": str(p), "usable": usable_spans(sp), "spans": sp})
        print(f"{p.name}: {len(sp)} spans, usable={usable_spans(sp)}", flush=True)
    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def build_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("train", help="train the structure tagger")
    tp.add_argument("--epochs", type=int, default=3)
    tp.add_argument("--batch-size", type=int, default=32)
    tp.add_argument("--lr", type=float, default=1e-3)
    tp.add_argument("--limit", type=int, default=None)
    tp.set_defaults(func=train)
    ep = sub.add_parser("eval", help="evaluate on the held-out test split")
    ep.add_argument("--limit", type=int, default=None)
    ep.set_defaults(func=evaluate)
    ip = sub.add_parser("infer", help="label text and print bracketed spans")
    ip.add_argument("--text")
    ip.add_argument("--file")
    ip.set_defaults(func=infer)
    sp = sub.add_parser("spans", help="batch-annotate files -> JSON offsets")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--out")
    sp.set_defaults(func=spans_cmd)
    return ap


def main() -> None:
    ap = build_parser(argparse.ArgumentParser(
        prog="arabicmodels.indexing",
        description="Neural text-structure segmentation (HNUM/ISNAD/MATN/HEADING)"))
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
