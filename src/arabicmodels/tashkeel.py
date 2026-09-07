"""Models B1 / B2 — diacritization (tashkīl).

For every Arabic letter the model predicts one of **16 classes**: eight vowel
states (none, fatḥa, ḍamma, kasra, sukūn, fatḥatān, ḍammatān, kasratān) each
with or without shadda. Base letters are never generated, only classified, so
the output text is guaranteed to differ from the input in combining marks
alone — it cannot drop, insert or alter a letter.

Two versions ship, forming a controlled ablation (`docs/PAPER.md` §7.3):

  v0.1  `TashkeelNet`     characters only
  v0.2  `TashkeelPosNet`  every character embedding is concatenated with a
                          32-d embedding of its word's POS tag (predicted by
                          `arabicmodels.pos` on the bare text), so the vowel
                          choice — case endings above all — is conditioned on
                          grammar. This is the recommended model.

Training labels are free: vocalized classical prints are their own reference.
Stripping the marks yields the input; the original is the target.

CLI:
    python -m arabicmodels.tashkeel train --epochs 3            # v0.1
    python -m arabicmodels.tashkeel tag-data                    # POS-tag windows
    python -m arabicmodels.tashkeel train --pos --epochs 3      # v0.2
    python -m arabicmodels.tashkeel eval [--pos]
    python -m arabicmodels.tashkeel infer --text "قال رسول الله" --pos
    python -m arabicmodels.tashkeel annotate page.txt --pos --out out.txt
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

from .common import (DATA_DIR, MODELS_DIR, PAD, Vocab, device, load_ckpt,
                     pad_batch, read_jsonl, save_ckpt, seed_all)

CKPT = MODELS_DIR / "tashkeel_bilstm.pt"
DATA = DATA_DIR / "tashkeel.jsonl"
# POS-conditioned variant (v0.2): per-char input = char emb ⊕ word POS-tag emb
CKPT_POS = MODELS_DIR / "tashkeel_bilstm_pos.pt"
DATA_POS = DATA_DIR / "tashkeel_pos.jsonl"

# diacritic classes: vowel (8) x shadda (2) = 16
_VOWELS = ["", "\u064E", "\u064F", "\u0650", "\u0652",  # none fatha damma kasra sukun
           "\u064B", "\u064C", "\u064D"]                 # fathatan dammatan kasratan
_VOWEL_ID = {v: i for i, v in enumerate(_VOWELS)}
SHADDA = "\u0651"
N_CLASSES = len(_VOWELS) * 2
_MARK = re.compile(r"[\u064B-\u0652\u0670]")
_ARABIC_LETTER = re.compile(r"[\u0621-\u064A]")

MAX_WINDOW_CHARS = 380


def split_marks(vocalized: str) -> tuple[str, list[int]]:
    """Split vocalized text into (bare text, per-character class labels).

    Non-letter characters get class 0.

    Only the 16 classes above survive the round trip. Marks outside that
    inventory — superscript (dagger) alef U+0670 above all, as in هَٰذَا and
    ٱلرَّحْمَٰن — are removed here and cannot be restored by `apply_marks`,
    so the models never predict them.
    """
    base_chars: list[str] = []
    labels: list[int] = []
    i = 0
    while i < len(vocalized):
        ch = vocalized[i]
        if _MARK.match(ch):
            i += 1
            continue
        marks = ""
        j = i + 1
        while j < len(vocalized) and _MARK.match(vocalized[j]):
            marks += vocalized[j]
            j += 1
        shadda = 1 if SHADDA in marks else 0
        vowel = next((m for m in marks if m in _VOWEL_ID and m), "")
        base_chars.append(ch)
        labels.append(_VOWEL_ID.get(vowel, 0) + len(_VOWELS) * shadda)
        i = j if j > i + 1 else i + 1
    return "".join(base_chars), labels


def apply_marks(text: str, labels: list[int]) -> str:
    """Re-insert diacritics after their letters. Base letters are untouched.

    Marks are emitted shadda-first (`0651 064E`). Unicode canonical ordering
    sorts by combining class and so prefers vowel-first (`064E 0651`); the two
    render identically and are equal under `unicodedata.normalize("NFC", …)`,
    but not as raw code point sequences. Normalize both sides before comparing
    model output against a reference corpus.
    """
    out = []
    for ch, lab in zip(text, labels):
        out.append(ch)
        if _ARABIC_LETTER.match(ch) and lab:
            if lab >= len(_VOWELS):
                out.append(SHADDA)
                lab -= len(_VOWELS)
            if lab:
                out.append(_VOWELS[lab])
    return "".join(out)


class TashkeelNet(nn.Module):
    """v0.1 — character-only diacritizer."""

    def __init__(self, n_chars: int, emb: int = 128, hid: int = 384):
        super().__init__()
        self.emb = nn.Embedding(n_chars, emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb, hid, num_layers=2, bidirectional=True,
                            batch_first=True, dropout=0.2)
        self.head = nn.Linear(hid * 2, N_CLASSES)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h, _ = self.lstm(self.emb(x))
        return self.head(h)


class TashkeelPosNet(nn.Module):
    """v0.2 — grammar-aware diacritization.

    Every character embedding is concatenated with the POS-tag embedding of
    the word it belongs to, so the vowel choice (case endings above all) is
    conditioned on syntax.
    """

    def __init__(self, n_chars: int, n_tags: int, emb: int = 128,
                 tag_emb: int = 32, hid: int = 384):
        super().__init__()
        self.emb = nn.Embedding(n_chars, emb, padding_idx=PAD)
        self.tag_emb = nn.Embedding(n_tags, tag_emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb + tag_emb, hid, num_layers=2, bidirectional=True,
                            batch_first=True, dropout=0.2)
        self.head = nn.Linear(hid * 2, N_CLASSES)

    def forward(self, x: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        h, _ = self.lstm(torch.cat([self.emb(x), self.tag_emb(t)], dim=-1))
        return self.head(h)


def char_tag_ids(bare: str, tags: list[str], tvocab: Vocab) -> list[int]:
    """Broadcast word-level POS tags to one tag id per character.

    Spaces inherit the preceding word's tag; this is irrelevant because the
    loss and the output both mask non-letters.
    """
    ids: list[int] = []
    w = 0
    for ch in bare:
        tag = tags[w] if w < len(tags) else "x"
        ids.append(tvocab.stoi.get(tag, 1))
        if ch == " ":
            w += 1
    return ids


def _prepare(rows: list[dict]) -> list[tuple]:
    """(bare, labels) rows; POS rows additionally carry the word tags."""
    out = []
    for r in rows:
        text, labels = split_marks(r["text"])
        if len(text) < 20:
            continue
        if "tags" in r:
            out.append((text, labels, r["tags"]))
        else:
            out.append((text, labels))
    return out


def _batches(data, vocab: Vocab, batch_size: int, shuffle: bool,
             tvocab: Vocab | None = None):
    import random
    idx = list(range(len(data)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), batch_size):
        chunk = [data[j] for j in idx[i:i + batch_size]]
        x = pad_batch([vocab.encode(row[0]) for row in chunk])
        y = pad_batch([row[1] for row in chunk], pad=-100)
        # only Arabic letters contribute to the loss
        for bi, row in enumerate(chunk):
            for ci, ch in enumerate(row[0]):
                if not _ARABIC_LETTER.match(ch):
                    y[bi, ci] = -100
        if tvocab is not None:
            t = pad_batch([char_tag_ids(row[0], row[2], tvocab) for row in chunk])
            yield (x, t), y
        else:
            yield x, y


def _forward(model, xb, dev):
    if isinstance(xb, tuple):
        return model(xb[0].to(dev), xb[1].to(dev))
    return model(xb.to(dev))


@torch.no_grad()
def _evaluate(model, data, vocab, dev, tvocab: Vocab | None = None) -> dict:
    """Diacritic error rate over all Arabic letters and over marked letters.

    `der_marked` restricts scoring to letters the reference actually
    vocalizes, because classical prints are only selectively vocalized.
    """
    model.eval()
    total = errs = marked = marked_errs = 0
    for xb, y in _batches(data, vocab, 128, shuffle=False, tvocab=tvocab):
        pred = _forward(model, xb, dev).argmax(-1).cpu()
        mask = y != -100
        total += int(mask.sum())
        errs += int((pred[mask] != y[mask]).sum())
        mmask = mask & (y > 0)
        marked += int(mmask.sum())
        marked_errs += int((pred[mmask] != y[mmask]).sum())
    return {"der_all": errs / max(total, 1),
            "der_marked": marked_errs / max(marked, 1),
            "chars": total}


def train(args) -> None:
    seed_all()
    dev = device()
    pos = getattr(args, "pos", False)
    rows = read_jsonl(DATA_POS if pos else DATA, limit=args.limit)
    data = {s: [] for s in ("train", "dev", "test")}
    for r in rows:
        data[r["split"]].append(r)
    tr, dv = _prepare(data["train"]), _prepare(data["dev"])
    vocab = Vocab.build((row[0] for row in tr), max_size=400)
    tvocab = Vocab.build((row[2] for row in tr), max_size=100) if pos else None
    if pos:
        model = TashkeelPosNet(len(vocab), len(tvocab)).to(dev)
    else:
        model = TashkeelNet(len(vocab)).to(dev)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"device={dev} pos={pos} train={len(tr)} dev={len(dv)} "
          f"vocab={len(vocab)} params={n_params:,}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    lossf = nn.CrossEntropyLoss(ignore_index=-100)
    ckpt = CKPT_POS if pos else CKPT
    best = 1e9
    for ep in range(1, args.epochs + 1):
        model.train()
        t0, tot_loss, nb = time.time(), 0.0, 0
        for xb, y in _batches(tr, vocab, args.batch_size, shuffle=True, tvocab=tvocab):
            opt.zero_grad()
            out = _forward(model, xb, dev)
            loss = lossf(out.reshape(-1, N_CLASSES), y.to(dev).reshape(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            opt.step()
            tot_loss += float(loss)
            nb += 1
            if nb % 200 == 0:
                print(f"  ep{ep} batch {nb} loss {tot_loss/nb:.4f}", flush=True)
        m = _evaluate(model, dv, vocab, dev, tvocab=tvocab)
        print(f"epoch {ep}: loss {tot_loss/max(nb,1):.4f} "
              f"dev DER(all) {m['der_all']:.4f} DER(marked) {m['der_marked']:.4f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        if m["der_all"] < best:
            best = m["der_all"]
            extra = {"vocab": vocab.stoi, "metrics": m, "task": "tashkeel",
                     "version": "0.2" if pos else "0.1"}
            if pos:
                extra["tag_vocab"] = tvocab.stoi
            save_ckpt(ckpt, model, extra)
            print(f"  saved -> {ckpt}")


def load_model(pos: bool = False, path: Path | str | None = None):
    """Load a diacritizer. Returns (model, vocab, tag_vocab|None, metrics)."""
    ck = load_ckpt(Path(path) if path else (CKPT_POS if pos else CKPT))
    vocab = Vocab(ck["vocab"])
    if pos:
        tvocab = Vocab(ck["tag_vocab"])
        model = TashkeelPosNet(len(vocab), len(tvocab))
    else:
        tvocab = None
        model = TashkeelNet(len(vocab))
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, vocab, tvocab, ck.get("metrics", {})


# --- POS tagger bridge (word tags feed the v0.2 diacritizer) ---------------

class PosFeatureTagger:
    """Thin wrapper over the POS student, used as a feature extractor."""

    def __init__(self, dev, path: Path | str | None = None):
        from .common import WordTagger, encode_words, pad_words
        from .pos import CKPT as POS_CKPT
        from .pos import MAX_WORDS
        ck = load_ckpt(Path(path) if path else POS_CKPT)
        self.cvocab = Vocab(ck["char_vocab"])
        self.tvocab = Vocab(ck["tag_vocab"])
        self.itos = self.tvocab.itos()
        self.model = WordTagger(len(self.cvocab), len(self.tvocab))
        self.model.load_state_dict(ck["state_dict"])
        self.model.eval()
        self.model.to(dev)
        self.dev = dev
        self.max_words = MAX_WORDS
        self._encode_words, self._pad_words = encode_words, pad_words

    @torch.no_grad()
    def tag_batch(self, sentences: list[list[str]]) -> list[list[str]]:
        out: list[list[str]] = []
        for i in range(0, len(sentences), 64):
            chunk = sentences[i:i + 64]
            x = self._pad_words(
                [self._encode_words(w[:self.max_words], self.cvocab) for w in chunk]
            ).to(self.dev)
            pred = self.model(x).argmax(-1).cpu()
            for words, p in zip(chunk, pred):
                n = min(len(words), self.max_words)
                tags = [self.itos.get(int(t), "x") for t in p[:n]]
                tags += ["x"] * (len(words) - n)
                out.append(tags)
        return out


def tag_data(args) -> None:
    """Silver-tag every diacritization window with the POS student and write
    the merged {text, tags, split} training file for the v0.2 model."""
    dev = device()
    tagger = PosFeatureTagger(dev)
    rows = read_jsonl(DATA, limit=args.limit)
    t0 = time.time()
    DATA_POS.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_POS, "w", encoding="utf-8") as f:
        for i in range(0, len(rows), 256):
            chunk = rows[i:i + 256]
            sents = [_MARK.sub("", r["text"]).split() for r in chunk]
            tags = tagger.tag_batch(sents)
            for r, tg in zip(chunk, tags):
                f.write(json.dumps({"text": r["text"], "tags": tg,
                                    "split": r["split"]}, ensure_ascii=False) + "\n")
            if (i // 256) % 20 == 0:
                print(f"  tagged {min(i+256, len(rows))}/{len(rows)} "
                      f"({time.time()-t0:.0f}s)", flush=True)
    print(f"wrote {DATA_POS} ({len(rows)} windows) in {time.time()-t0:.0f}s")


def evaluate(args) -> None:
    dev = device()
    pos = getattr(args, "pos", False)
    rows = [r for r in read_jsonl(DATA_POS if pos else DATA, limit=args.limit)
            if r["split"] == "test"]
    data = _prepare(rows)
    model, vocab, tvocab, _ = load_model(pos)
    model.to(dev)
    m = _evaluate(model, data, vocab, dev, tvocab=tvocab)
    print(f"test windows={len(data)} chars={m['chars']} pos={pos}")
    print(f"DER (all letters):    {m['der_all']:.4f}")
    print(f"DER (marked letters): {m['der_marked']:.4f}")


@torch.no_grad()
def infer(args) -> None:
    text = args.text or Path(args.file).read_text(encoding="utf-8")
    pos = getattr(args, "pos", False)
    model, vocab, tvocab, _ = load_model(pos)
    dev = device()
    model.to(dev)
    tagger = PosFeatureTagger(dev) if pos else None
    out_lines = []
    for line in text.splitlines() or [text]:
        bare = _MARK.sub("", line)
        if not bare.strip():
            out_lines.append(line)
            continue
        x = pad_batch([vocab.encode(bare)]).to(dev)
        if pos:
            tags = tagger.tag_batch([bare.split()])[0]
            t = pad_batch([char_tag_ids(bare, tags, tvocab)]).to(dev)
            labels = model(x, t).argmax(-1)[0][:len(bare)].tolist()
        else:
            labels = model(x).argmax(-1)[0][:len(bare)].tolist()
        out_lines.append(apply_marks(bare, labels))
    sys.stdout.buffer.write(("\n".join(out_lines) + "\n").encode("utf-8"))


# --- gap-only merge -------------------------------------------------------

_QURAN_SPAN = re.compile(r"\uFD3F[^\uFD3E]*\uFD3E|\{[^}]*\}")  # ﴿...﴾ or {...}
_TOKEN_SPLIT = re.compile(r"(\s+)")


@torch.no_grad()
def _diacritize_words(model, vocab, dev, words: list[str],
                      tvocab=None, tagger=None) -> list[str]:
    """Diacritize bare words with sentence context, chunked near the training
    window size so inference matches the conditions the model was trained on."""
    chunks: list[list[str]] = []
    cur: list[str] = []
    clen = 0
    for w in words:
        if cur and clen + 1 + len(w) > MAX_WINDOW_CHARS:
            chunks.append(cur)
            cur, clen = [], 0
        cur.append(w)
        clen += len(w) + 1
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for ch in chunks:
        s = " ".join(ch)
        x = pad_batch([vocab.encode(s)]).to(dev)
        if tagger is not None:
            tags = tagger.tag_batch([ch])[0]
            t = pad_batch([char_tag_ids(s, tags, tvocab)]).to(dev)
            labels = model(x, t).argmax(-1)[0][:len(s)].tolist()
        else:
            labels = model(x).argmax(-1)[0][:len(s)].tolist()
        out.extend(apply_marks(s, labels).split(" "))
    return out


def annotate_text(model, vocab, dev, text: str, tvocab=None, tagger=None,
                  protect_quran: bool = True) -> str:
    """Gap-only merge: add model diacritics **only** to fully bare words.

    A conservative policy for texts where the source vocalization carries
    editorial authority:

      * a word that already carries any diacritic keeps its marks verbatim;
      * Qurʾānic citation spans (﴿…﴾ or {…}) are never altered;
      * only fully bare words receive model output.
    """
    protected = ([(m.start(), m.end()) for m in _QURAN_SPAN.finditer(text)]
                 if protect_quran else [])

    def is_protected(a: int, b: int) -> bool:
        return any(a < pe and b > ps for ps, pe in protected)

    tokens = _TOKEN_SPLIT.split(text)
    need_idx: list[int] = []
    offset = 0
    for i, tok in enumerate(tokens):
        a, b = offset, offset + len(tok)
        offset = b
        if (i % 2 == 0 and tok and _ARABIC_LETTER.search(tok)
                and not _MARK.search(tok) and not is_protected(a, b)):
            need_idx.append(i)
    if not need_idx:
        return text
    marked = _diacritize_words(model, vocab, dev, [tokens[i] for i in need_idx],
                               tvocab=tvocab, tagger=tagger)
    for i, w in zip(need_idx, marked):
        tokens[i] = w
    return "".join(tokens)


def annotate_cmd(args) -> None:
    """Batch gap-only diacritization over text files."""
    pos = getattr(args, "pos", False)
    model, vocab, tvocab, _ = load_model(pos)
    dev = device()
    model.to(dev)
    tagger = PosFeatureTagger(dev) if pos else None
    outputs = []
    for p in [Path(f) for f in args.files]:
        text = p.read_text(encoding="utf-8")
        merged = annotate_text(model, vocab, dev, text, tvocab=tvocab,
                               tagger=tagger,
                               protect_quran=not args.no_protect_quran)
        outputs.append(merged)
        print(f"{p.name}: {len(text)} chars in, "
              f"{'changed' if merged != text else 'unchanged'}", flush=True)
    payload = "\n".join(outputs)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def build_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("train", help="train a diacritizer")
    tp.add_argument("--epochs", type=int, default=3)
    tp.add_argument("--batch-size", type=int, default=64)
    tp.add_argument("--lr", type=float, default=2e-3)
    tp.add_argument("--limit", type=int, default=None)
    tp.add_argument("--pos", action="store_true",
                    help="POS-conditioned v0.2 (reads data/tashkeel_pos.jsonl)")
    tp.set_defaults(func=train)
    ep = sub.add_parser("eval", help="diacritic error rate on the test split")
    ep.add_argument("--limit", type=int, default=None)
    ep.add_argument("--pos", action="store_true")
    ep.set_defaults(func=evaluate)
    ip = sub.add_parser("infer", help="diacritize text (marks are recomputed)")
    ip.add_argument("--text")
    ip.add_argument("--file")
    ip.add_argument("--pos", action="store_true")
    ip.set_defaults(func=infer)
    td = sub.add_parser("tag-data",
                        help="POS-tag tashkeel.jsonl -> tashkeel_pos.jsonl")
    td.add_argument("--limit", type=int, default=None)
    td.set_defaults(func=tag_data)
    an = sub.add_parser("annotate",
                        help="gap-only merge over files (keeps existing marks)")
    an.add_argument("files", nargs="+")
    an.add_argument("--out")
    an.add_argument("--pos", action="store_true")
    an.add_argument("--no-protect-quran", action="store_true",
                    help="also diacritize inside ﴿…﴾ / {…} spans")
    an.set_defaults(func=annotate_cmd)
    return ap


def main() -> None:
    ap = build_parser(argparse.ArgumentParser(
        prog="arabicmodels.tashkeel",
        description="Neural Arabic diacritization (16-class per-letter tagging)"))
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
