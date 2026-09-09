"""Arabic orthographic normalization.

Collapses the spelling variation that makes exact string comparison
unreliable in classical Arabic: diacritics and taṭwīl are removed, the alif
forms (آ أ إ ٱ) fold to ا, tāʾ marbūṭa (ة) to ه, alif maqṣūra (ى) to ي, and
the carrier hamzas (ؤ ئ) to ء.

Used by the isnād parser and the morphology teacher. The neural models do
*not* normalize their input: they read the raw surface form, which is what
makes the character encoders robust to these very variants.
"""
import re

_DIACRITICS = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u0640]")
_ALEF_FORMS = re.compile(r"[\u0622\u0623\u0625\u0671]")   # آ أ إ ٱ -> ا
_TA_MARBUTA = "\u0629"                                    # ة -> ه
_ALEF_MAQSURA = "\u0649"                                  # ى -> ي
_HAMZA_FORMS = re.compile(r"[\u0624\u0626]")              # ؤ ئ -> ء
_WS = re.compile(r"\s+")


def strip_diacritics(text: str) -> str:
    """Remove diacritics and taṭwīl, changing nothing else.

    The first half of `normalize_arabic`, on its own. The letter skeleton,
    the hamza carriers, tāʾ marbūṭa and all punctuation survive, so the result
    is still the raw surface form the neural models are trained to read — only
    unvowelled. `normalize_arabic` is the wrong tool for that job: its
    orthographic folding rewrites letters the character encoders rely on.

    Diacritics never stand alone, so this never changes a word count and a
    per-word labeling stays aligned with the text it came from.
    """
    return _DIACRITICS.sub("", text or "")


def normalize_arabic(text: str) -> str:
    """Fold orthographic variants and collapse whitespace."""
    if not text:
        return ""
    t = strip_diacritics(text)
    t = _ALEF_FORMS.sub("\u0627", t)
    t = t.replace(_TA_MARBUTA, "\u0647")
    t = t.replace(_ALEF_MAQSURA, "\u064A")
    t = _HAMZA_FORMS.sub("\u0621", t)
    t = _WS.sub(" ", t)
    return t.strip()


def has_tashkeel(text: str) -> bool:
    """True when the text carries at least one diacritic mark."""
    return bool(re.search(r"[\u064B-\u065F\u0670]", text or ""))
