"""High-level inference API.

Three loaders wrap the four checkpoints behind a small, stable surface:

    >>> from arabicmodels import Diacritizer, PosTagger, StructureTagger
    >>> d = Diacritizer.load()                       # v0.2, grammar-aware
    >>> d.diacritize("قال رسول الله صلى الله عليه وسلم")
    >>> PosTagger.load().tag("حدثنا قتيبة بن سعيد")
    >>> StructureTagger.load().tag("1248 - حدثنا محمد بن بشار ...")

Each loader reads its weights once and keeps the model warm. Models are
CPU-friendly; pass `device="cuda"` to use a GPU.
"""
from pathlib import Path

import torch

from . import indexing as _indexing
from . import isnad as _isnad
from . import pos as _pos
from . import tashkeel as _tashkeel
from .common import device as _auto_device
from .common import encode_words, pad_batch, pad_words


def _resolve(dev) -> torch.device:
    return torch.device(dev) if dev is not None else _auto_device()


class Diacritizer:
    """Restore Arabic diacritics (tashkīl).

    `diacritize` recomputes every mark from the bare letters. `fill_gaps`
    applies the conservative merge instead: words that already carry marks
    keep them, and only fully bare words are diacritized.
    """

    def __init__(self, model, vocab, tag_vocab, tagger, dev, metrics):
        self._model = model
        self._vocab = vocab
        self._tag_vocab = tag_vocab
        self._tagger = tagger
        self._device = dev
        self.metrics = metrics
        self.pos_aware = tag_vocab is not None

    @classmethod
    def load(cls, pos: bool = True, device=None,
             path: Path | str | None = None) -> "Diacritizer":
        """Load a diacritizer. `pos=True` selects the grammar-aware v0.2."""
        dev = _resolve(device)
        model, vocab, tag_vocab, metrics = _tashkeel.load_model(pos, path)
        model.to(dev)
        tagger = _tashkeel.PosFeatureTagger(dev) if pos else None
        return cls(model, vocab, tag_vocab, tagger, dev, metrics)

    @torch.no_grad()
    def diacritize(self, text: str) -> str:
        """Fully re-diacritize `text`; existing marks are recomputed."""
        bare = _tashkeel._MARK.sub("", text)
        if not bare.strip():
            return text
        x = pad_batch([self._vocab.encode(bare)]).to(self._device)
        if self.pos_aware:
            tags = self._tagger.tag_batch([bare.split()])[0]
            t = pad_batch([_tashkeel.char_tag_ids(bare, tags, self._tag_vocab)])
            labels = self._model(x, t.to(self._device))
        else:
            labels = self._model(x)
        labels = labels.argmax(-1)[0][:len(bare)].tolist()
        return _tashkeel.apply_marks(bare, labels)

    def fill_gaps(self, text: str, protect_quran: bool = True) -> str:
        """Diacritize only the fully bare words, keeping existing marks."""
        return _tashkeel.annotate_text(
            self._model, self._vocab, self._device, text,
            tvocab=self._tag_vocab, tagger=self._tagger,
            protect_quran=protect_quran)


class PosTagger:
    """Word-level part-of-speech tagging with 24 tags."""

    def __init__(self, model, cvocab, tvocab, dev):
        self._model = model
        self._cvocab = cvocab
        self._tvocab = tvocab
        self._itos = tvocab.itos()
        self._device = dev

    @classmethod
    def load(cls, device=None, path: Path | str | None = None) -> "PosTagger":
        dev = _resolve(device)
        model, cvocab, tvocab = _pos.load_model(path)
        model.to(dev)
        return cls(model, cvocab, tvocab, dev)

    @property
    def tags(self) -> list[str]:
        """The tag inventory, excluding the pad and unk slots."""
        return [t for t in self._tvocab.stoi if not t.startswith("<")]

    @torch.no_grad()
    def tag(self, text: str) -> list[tuple[str, str]]:
        """Tag one string. Returns [(word, tag), ...]."""
        words = text.split()
        if not words:
            return []
        return list(zip(words, self.tag_words(words)))

    @torch.no_grad()
    def tag_words(self, words: list[str]) -> list[str]:
        """Tag a pre-split word list (truncated to the model's window)."""
        if not words:
            return []
        n = _pos.MAX_WORDS
        x = pad_words([encode_words(words[:n], self._cvocab)]).to(self._device)
        pred = self._model(x).argmax(-1)[0][:len(words)].tolist()
        tags = [self._itos.get(t, "?") for t in pred]
        return tags + ["?"] * (len(words) - len(tags))


class StructureTagger:
    """Segment running text into HNUM / ISNAD / MATN / HEADING."""

    LABELS = ("HNUM", "ISNAD", "MATN", "HEADING")
    # The model's context window, in words. Public because callers that batch
    # several passages together need to know how much fits before the window
    # closes: whether a line is a chain, a body or a title is partly a
    # question about the lines around it, and only text inside one window can
    # answer it.
    WINDOW = _indexing.MAX_WORDS

    def __init__(self, model, cvocab, tvocab, dev):
        self._model = model
        self._cvocab = cvocab
        self._tvocab = tvocab
        self._device = dev

    @classmethod
    def load(cls, device=None, path: Path | str | None = None) -> "StructureTagger":
        dev = _resolve(device)
        model, cvocab, tvocab = _indexing.load_model(path)
        model.to(dev)
        return cls(model, cvocab, tvocab, dev)

    def tag(self, text: str) -> list[tuple[str, str]]:
        """Label each word of one unit. Returns [(word, label), ...]."""
        return list(zip(text.split(), self.tag_words(text.split())))

    def tag_words(self, words: list[str]) -> list[str]:
        """Label a pre-split word list; longer than WINDOW is done in blocks.

        Pass several passages at once, joined in reading order, to give each
        the others as context — a matn quoted with no chain in front of it is
        unrecognizable on its own.
        """
        return _indexing.tag_words(self._model, self._cvocab, self._tvocab,
                                   self._device, words)

    @staticmethod
    def group(tagged: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Merge runs of same-labelled words. [(word, label)] -> [(label, text)]."""
        out: list[tuple[str, list[str]]] = []
        for w, tg in tagged:
            if out and out[-1][0] == tg:
                out[-1][1].append(w)
            else:
                out.append((tg, [w]))
        return [(label, " ".join(ws)) for label, ws in out]

    def spans(self, text: str) -> list[list]:
        """Label a whole page and merge runs into [start, end, label] spans
        over character offsets in the original text."""
        return _indexing.page_spans(self._model, self._cvocab, self._tvocab,
                                    self._device, text)

    def segments(self, text: str) -> list[tuple[str, str]]:
        """Group contiguous same-label words. Returns [(label, text), ...]."""
        return self.group(self.tag(text))

    @staticmethod
    def narrators(isnad_text: str) -> list[_isnad.Hop]:
        """Split one ISNAD segment into its narrators, in transmission order.

        The model finds the chain; these rules divide it. Offsets on each hop
        are relative to `isnad_text`, so `isnad_text[h.start:h.end]` is the
        narrator's name exactly as written, and `[h.verb_start:h.verb_end]` the
        verb that hands the report to him.

        Not a method on the model, and no model runs here — it is a `Hop` list
        for a span the caller already has.
        """
        return _isnad.narrator_hops(isnad_text)
