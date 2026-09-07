"""CAMeL Tools morphology engine — the silver POS teacher.

Distilled into the compact POS student (`arabicmodels.pos`), which then needs
no morphology database at inference. Requires the optional dependency:

    pip install "arabicmodels[teacher]"
    camel_data -i morphology-db-msa-r13

Reference: Obeid et al. (2020), *CAMeL Tools: An Open Source Python Toolkit
for Arabic Natural Language Processing*, LREC 2020.

Two properties of this teacher shape the student and its evaluation:

1. It is **context-free**. Analyses come back ranked by frequency and the
   first is taken, so the tag does not depend on the sentence. The student is
   a sentence-level BiLSTM and therefore sees more context than its teacher.
2. Its database is **Modern Standard Arabic**, not classical Arabic, and the
   `NOAN_PROP` backoff labels unanalyzable words as proper nouns.

Consequently the student's reported accuracy bounds *agreement with this
teacher*, not absolute linguistic correctness.

KNOWN ISSUE (preserved deliberately): `_pick` intends to prefer an analysis
whose diacritized form matches the diacritics already present on the token,
but `normalize_arabic` strips diacritics from both sides before comparing, so
the first (most frequent) analysis is effectively always chosen. The shipped
`data/pos.jsonl` was generated with this behaviour; it is kept as-is so that
dataset remains exactly reproducible. Fix it only alongside a dataset and
model regeneration.
"""
from typing import Any

from ..normalize import normalize_arabic
from ..tokenization import whitespace_tokenize


class CamelMorphologyEngine:
    name = "camel"
    version = ""
    layers = ("morphology", "roots", "pos", "segments")

    def __init__(self) -> None:
        self._analyzer = None
        self._available: bool | None = None
        self.unavailable_reason = ""

    def available(self) -> bool:
        if self._available is None:
            try:
                import camel_tools
                self.version = camel_tools.__version__
                from camel_tools.morphology.database import MorphologyDB
                MorphologyDB.builtin_db()          # raises if data not downloaded
                self._available = True
            except Exception as e:
                self.unavailable_reason = (
                    f"{type(e).__name__}: {e} — install with "
                    '`pip install "arabicmodels[teacher]"` then '
                    "`camel_data -i morphology-db-msa-r13`")
                self._available = False
        return self._available

    def warm(self) -> None:
        if self._analyzer is None:
            from camel_tools.morphology.analyzer import Analyzer
            from camel_tools.morphology.database import MorphologyDB
            self._analyzer = Analyzer(MorphologyDB.builtin_db(), backoff="NOAN_PROP")

    def annotate_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        self.warm()
        from camel_tools.tokenizers.word import simple_word_tokenize
        out = []
        for text in texts:
            master = whitespace_tokenize(text)
            roots, pos, segments, morph = [], [], [], []
            for tok in master:
                words = simple_word_tokenize(tok.text)
                # analyze the longest alphabetic piece of the master token
                word = max(words, key=len) if words else tok.text
                analyses = self._analyzer.analyze(word)
                if not analyses:
                    continue
                best = _pick(analyses, word)
                roots.append({"token_idx": tok.idx, "root": best.get("root", ""),
                              "pattern": best.get("pattern", ""),
                              "lemma": best.get("lex", ""), "engine": self.name})
                pos.append({"token_idx": tok.idx, "tag": best.get("pos", ""),
                            "engine": self.name})
                d11 = best.get("d1seg") or best.get("atbseg") or ""
                if d11 and "_" in d11:
                    segments.append({"token_idx": tok.idx,
                                     "parts": d11.split("_"), "engine": self.name})
                morph.append({"token_idx": tok.idx,
                              "features": {k: v for k, v in best.items()
                                           if k in ("pos", "asp", "per", "gen", "num",
                                                    "stt", "cas", "mod", "vox", "prc0",
                                                    "prc1", "prc2", "enc0", "lex",
                                                    "root", "pattern")}})
            out.append({"roots": roots, "pos": pos, "segments": segments,
                        "morphology": morph})
        return out


def _pick(analyses: list[dict], word: str) -> dict:
    """Prefer an analysis whose diacritized form matches the input's
    diacritics; otherwise take the first (CAMeL orders by frequency).

    See the KNOWN ISSUE in the module docstring: normalization strips the
    diacritics being compared, so in practice this returns `analyses[0]`.
    """
    norm = normalize_arabic(word)
    for a in analyses:
        if normalize_arabic(a.get("diac", "")) == norm:
            return a
    return analyses[0]
