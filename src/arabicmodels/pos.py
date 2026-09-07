"""Model C — part-of-speech tagger, distilled from a morphology engine.

A compact word-level tagger with 24 tags, trained to imitate the CAMeL Tools
morphological analyzer (`arabicmodels.teachers`). The student needs no
morphology database at inference, which makes it fast enough to tag a whole
corpus and small enough to embed as a feature extractor inside the
grammar-aware diacritizer (`arabicmodels.tashkeel` v0.2).

Accuracy is reported as *agreement with the teacher*; see `docs/PAPER.md`
§7.2 for what that does and does not establish.

CLI:
    python -m arabicmodels.pos train --epochs 4
    python -m arabicmodels.pos eval
    python -m arabicmodels.pos infer --text "حدثنا قتيبة بن سعيد"
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

import torch
import torch.nn as nn

from .common import (DATA_DIR, MODELS_DIR, Vocab, WordTagger,
                     add_train_io_args, device, encode_words, init_or_build,
                     load_ckpt, pad_batch, pad_words, read_jsonl, save_ckpt,
                     seed_all)

CKPT = MODELS_DIR / "pos_wordtagger.pt"
DATA = DATA_DIR / "pos.jsonl"
MAX_WORDS = 120


def _prepare(rows: list[dict]) -> list[tuple[list[str], list[str]]]:
    out = []
    for r in rows:
        toks, tags = r["tokens"], r["tags"]
        if len(toks) != len(tags) or not toks:
            continue
        for i in range(0, len(toks), MAX_WORDS):
            out.append((toks[i:i + MAX_WORDS], tags[i:i + MAX_WORDS]))
    return out


def _batches(data, cvocab: Vocab, tvocab: Vocab, batch_size: int, shuffle: bool):
    import random
    idx = list(range(len(data)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), batch_size):
        chunk = [data[j] for j in idx[i:i + batch_size]]
        x = pad_words([encode_words(w, cvocab) for w, _ in chunk])
        y = pad_batch([tvocab.encode(t) for _, t in chunk], pad=-100)
        yield x, y


@torch.no_grad()
def _evaluate(model, data, cvocab, tvocab, dev) -> dict:
    model.eval()
    total = errs = 0
    confusions: Counter = Counter()
    itos = tvocab.itos()
    for x, y in _batches(data, cvocab, tvocab, 64, shuffle=False):
        pred = model(x.to(dev)).argmax(-1).cpu()
        mask = y != -100
        total += int(mask.sum())
        wrong = mask & (pred != y)
        errs += int(wrong.sum())
        for g, p in zip(y[wrong].tolist(), pred[wrong].tolist()):
            confusions[(itos.get(g, "?"), itos.get(p, "?"))] += 1
    return {"accuracy": 1 - errs / max(total, 1), "tokens": total,
            "top_confusions": confusions.most_common(8)}


def train(args) -> None:
    seed_all(args.seed)
    dev = device()
    data_path = Path(args.data) if args.data else DATA
    out_path = Path(args.out) if args.out else CKPT
    rows = read_jsonl(data_path, limit=args.limit)
    data = {s: [] for s in ("train", "dev", "test")}
    for r in rows:
        data[r["split"]].append(r)
    tr, dv = _prepare(data["train"]), _prepare(data["dev"])
    if not tr:
        raise SystemExit(f"no usable training rows in {data_path} — see docs/DATA.md")

    def build():
        cv = Vocab.build((list("".join(w)) for w, _ in tr), max_size=400)
        tv = Vocab.build((t for _, t in tr), max_size=100)
        return WordTagger(len(cv), len(tv)), cv, tv

    def load(ck):
        cv, tv = Vocab(ck["char_vocab"]), Vocab(ck["tag_vocab"])
        m = WordTagger(len(cv), len(tv))
        m.load_state_dict(ck["state_dict"])
        return m, cv, tv

    model, cvocab, tvocab = init_or_build(args, build, load)
    model.to(dev)
    unseen = {t for _, tags in tr for t in tags} - set(tvocab.stoi)
    if unseen:
        print(f"  warning: {len(unseen)} tag(s) absent from the vocabulary will "
              f"train as <unk>: {sorted(unseen)[:8]}")
    print(f"device={dev} data={data_path.name} train={len(tr)} dev={len(dv)} "
          f"tags={len(tvocab)-2} params={sum(p.numel() for p in model.parameters()):,}")
    print(f"checkpoint -> {out_path}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    lossf = nn.CrossEntropyLoss(ignore_index=-100)
    best = 0.0
    for ep in range(1, args.epochs + 1):
        model.train()
        t0, tot, nb = time.time(), 0.0, 0
        for x, y in _batches(tr, cvocab, tvocab, args.batch_size, shuffle=True):
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
        print(f"epoch {ep}: loss {tot/max(nb,1):.4f} dev acc {m['accuracy']:.4f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        if m["accuracy"] > best:
            best = m["accuracy"]
            save_ckpt(out_path, model,
                      {"char_vocab": cvocab.stoi, "tag_vocab": tvocab.stoi,
                       "metrics": m, "task": "pos", "version": "0.1"})
            print(f"  saved -> {out_path}")


def load_model(path: Path | str | None = None):
    """Load the POS tagger. Returns (model, char_vocab, tag_vocab)."""
    ck = load_ckpt(Path(path) if path else CKPT)
    cvocab, tvocab = Vocab(ck["char_vocab"]), Vocab(ck["tag_vocab"])
    model = WordTagger(len(cvocab), len(tvocab))
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, cvocab, tvocab


def evaluate(args) -> None:
    dev = device()
    data_path = Path(args.data) if args.data else DATA
    rows = [r for r in read_jsonl(data_path, limit=args.limit)
            if r["split"] == "test"]
    model, cvocab, tvocab = load_model(args.ckpt)
    model.to(dev)
    m = _evaluate(model, _prepare(rows), cvocab, tvocab, dev)
    print(f"test tokens={m['tokens']}")
    print(f"accuracy: {m['accuracy']:.4f}")
    print("top confusions (teacher -> student):")
    for (g, p), n in m["top_confusions"]:
        print(f"  {g} -> {p}: {n}")


@torch.no_grad()
def infer(args) -> None:
    text = args.text or Path(args.file).read_text(encoding="utf-8")
    model, cvocab, tvocab = load_model(args.ckpt)
    dev = device()
    model.to(dev)
    itos = tvocab.itos()
    out_lines = []
    for line in text.splitlines() or [text]:
        words = line.split()
        if not words:
            out_lines.append("")
            continue
        x = pad_words([encode_words(words[:MAX_WORDS], cvocab)]).to(dev)
        tags = model(x).argmax(-1)[0][:len(words)].tolist()
        out_lines.append(" ".join(f"{w}/{itos.get(t, '?')}"
                                  for w, t in zip(words, tags)))
    sys.stdout.buffer.write(("\n".join(out_lines) + "\n").encode("utf-8"))


def build_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("train", help="distil the tagger from the silver dataset")
    tp.add_argument("--epochs", type=int, default=4)
    tp.add_argument("--batch-size", type=int, default=32)
    tp.add_argument("--lr", type=float, default=1e-3)
    tp.add_argument("--limit", type=int, default=None)
    add_train_io_args(tp)
    tp.set_defaults(func=train)
    ep = sub.add_parser("eval", help="agreement with the teacher on the test split")
    ep.add_argument("--limit", type=int, default=None)
    ep.add_argument("--data", metavar="JSONL")
    ep.add_argument("--ckpt", metavar="CKPT")
    ep.set_defaults(func=evaluate)
    ip = sub.add_parser("infer", help="tag text as word/tag pairs")
    ip.add_argument("--text")
    ip.add_argument("--file")
    ip.add_argument("--ckpt", metavar="CKPT")
    ip.set_defaults(func=infer)
    return ap


def main() -> None:
    ap = build_parser(argparse.ArgumentParser(
        prog="arabicmodels.pos",
        description="Neural POS tagger distilled from CAMeL Tools morphology"))
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
