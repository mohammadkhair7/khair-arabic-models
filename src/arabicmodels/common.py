"""Shared building blocks: vocabularies, batching, checkpoint IO, and the
two-level `WordTagger` network used by the structure and POS models.

Checkpoints are self-contained: a single `torch.save` dictionary carrying the
weights, the vocabularies needed to encode input, the dev metrics recorded at
the moment the checkpoint was selected, plus the task name and version. There
are no companion tokenizer or config files.
"""
import json
import os
import random
from pathlib import Path

import numpy as np
import torch

PAD, UNK = 0, 1


def home() -> Path:
    """Repository root holding `models/` and `data/`.

    Override with the ARABICMODELS_HOME environment variable when the package
    is installed somewhere other than a source checkout.
    """
    env = os.environ.get("ARABICMODELS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


MODELS_DIR = home() / "models"
DATA_DIR = home() / "data"


def device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def seed_all(seed: int = 13) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


class Vocab:
    """Item→id map with <pad>=0 and <unk>=1."""

    def __init__(self, items: dict[str, int] | None = None):
        self.stoi: dict[str, int] = items or {"<pad>": PAD, "<unk>": UNK}

    @classmethod
    def build(cls, iterables, max_size: int = 20000) -> "Vocab":
        from collections import Counter
        c: Counter = Counter()
        for seq in iterables:
            c.update(seq)
        v = cls()
        for item, _ in c.most_common(max_size):
            v.stoi.setdefault(item, len(v.stoi))
        return v

    def __len__(self) -> int:
        return len(self.stoi)

    def encode(self, seq) -> list[int]:
        return [self.stoi.get(x, UNK) for x in seq]

    def itos(self) -> dict[int, str]:
        return {v: k for k, v in self.stoi.items()}


def pad_batch(seqs: list[list[int]], pad: int = PAD) -> torch.Tensor:
    n = max(len(s) for s in seqs)
    out = torch.full((len(seqs), n), pad, dtype=torch.long)
    for i, s in enumerate(seqs):
        out[i, :len(s)] = torch.tensor(s, dtype=torch.long)
    return out


def save_ckpt(path: Path, model: torch.nn.Module, extra: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), **extra}, path)


def load_ckpt(path: Path) -> dict:
    """Load a checkpoint, preferring the safe (`weights_only`) unpickler.

    Checkpoints written by this package hold only tensors, plain dicts and
    strings, so they load without executing pickled code. The fallback exists
    for checkpoints produced by older torch versions.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"checkpoint not found: {path}\n"
            "Model weights are stored with Git LFS — run `git lfs install` "
            "followed by `git lfs pull`, or set ARABICMODELS_HOME to the "
            "directory containing models/."
        )
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        return torch.load(path, map_location="cpu", weights_only=False)


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


class WordTagger(torch.nn.Module):
    """Two-level BiLSTM tagger, shared by the structure and POS models.

    Level 1 encodes each word from its characters (embedding → char BiLSTM →
    256-d word vector), which keeps the model robust to unseen words: rare
    classical forms and proper names still get meaningful vectors from their
    letter patterns. Level 2 reads the sequence of word vectors in both
    directions and classifies every word.
    """

    def __init__(self, n_chars: int, n_tags: int, char_emb: int = 64,
                 char_hid: int = 128, word_hid: int = 256):
        super().__init__()
        self.char_emb = torch.nn.Embedding(n_chars, char_emb, padding_idx=PAD)
        self.char_lstm = torch.nn.LSTM(char_emb, char_hid, batch_first=True,
                                       bidirectional=True)
        self.word_lstm = torch.nn.LSTM(char_hid * 2, word_hid, num_layers=2,
                                       batch_first=True, bidirectional=True,
                                       dropout=0.2)
        self.head = torch.nn.Linear(word_hid * 2, n_tags)

    def forward(self, chars: torch.Tensor) -> torch.Tensor:
        """chars: (batch, words, max_word_len) -> logits (batch, words, tags)."""
        b, w, c = chars.shape
        flat = chars.reshape(b * w, c)
        emb = self.char_emb(flat)
        _, (h, _) = self.char_lstm(emb)
        word_vecs = torch.cat([h[0], h[1]], dim=-1).reshape(b, w, -1)
        out, _ = self.word_lstm(word_vecs)
        return self.head(out)


def encode_words(words: list[str], vocab: "Vocab", max_len: int = 18) -> list[list[int]]:
    return [vocab.encode(list(w[:max_len])) or [UNK] for w in words]


def pad_words(batch: list[list[list[int]]]) -> torch.Tensor:
    """(batch of sentences of char-id lists) -> (b, max_words, max_chars)."""
    max_w = max(len(s) for s in batch)
    max_c = max((len(w) for s in batch for w in s), default=1)
    out = torch.full((len(batch), max_w, max_c), PAD, dtype=torch.long)
    for i, s in enumerate(batch):
        for j, w in enumerate(s):
            out[i, j, :len(w)] = torch.tensor(w, dtype=torch.long)
    return out


def add_train_io_args(p) -> None:
    """The `--data / --out / --init-from / --seed` group every train shares.

    Without `--out`, training overwrites the shipped checkpoint in `models/`.
    `--init-from` switches from training-from-scratch to fine-tuning: the
    starting weights *and the vocabularies* come from that checkpoint, since
    an embedding matrix is meaningless against a different vocabulary.
    """
    p.add_argument("--data", metavar="JSONL",
                   help="training data (default: the shipped dataset)")
    p.add_argument("--out", metavar="CKPT",
                   help="where to write the checkpoint "
                        "(default: overwrite the shipped one)")
    p.add_argument("--init-from", metavar="CKPT",
                   help="fine-tune from this checkpoint, reusing its vocabularies")
    p.add_argument("--seed", type=int, default=13)


def init_or_build(args, build, load) -> tuple:
    """Either fine-tune from `--init-from` or build a fresh model.

    `build()` returns (model, *vocabs) from the data; `load(ckpt)` returns the
    same tuple from a checkpoint. Kept here so all three tasks behave alike.
    """
    if getattr(args, "init_from", None):
        path = Path(args.init_from)
        out = load(load_ckpt(path))
        print(f"fine-tuning from {path} — vocabularies reused from the "
              f"checkpoint, not rebuilt from the data")
        return out
    return build()


def split_of(key: str) -> str:
    """Deterministic 90/5/5 train/dev/test split by content hash.

    Reproducible across machines and runs, and guarantees that an identical
    string always lands in the same split.
    """
    import hashlib
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100
    return "train" if h < 90 else ("dev" if h < 95 else "test")
