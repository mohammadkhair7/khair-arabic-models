"""arabicmodels — compact neural models for classical Arabic.

Four small BiLSTM models (2.8–5.2 M parameters) covering text-structure
segmentation, part-of-speech tagging and diacritization, all trained without
a single manually labeled example. See `docs/PAPER.md`.

    >>> from arabicmodels import Diacritizer
    >>> Diacritizer.load().diacritize("قال رسول الله")
"""
from .api import Diacritizer, PosTagger, StructureTagger
from .isnad import IsnadParse, parse_isnad
from .normalize import has_tashkeel, normalize_arabic
from .tashkeel import apply_marks, split_marks
from .tokenization import Token, whitespace_tokenize

__version__ = "0.1.0"

__all__ = [
    # high-level models
    "Diacritizer",
    "PosTagger",
    "StructureTagger",
    # text utilities
    "normalize_arabic",
    "has_tashkeel",
    "split_marks",
    "apply_marks",
    "whitespace_tokenize",
    "Token",
    # rule-based labeler
    "parse_isnad",
    "IsnadParse",
    "__version__",
]
