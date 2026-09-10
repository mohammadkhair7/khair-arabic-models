"""Human-readable names for the tags the models emit, in English and Arabic.

The checkpoints store terse codes - `noun_prop`, `verb_pseudo`, `MATN` - which
are the right thing to keep in a data file and the wrong thing to show a
reader. This module is the single place that turns a code into a name, so the
preview, the CSV, the Markdown and the PDF cannot drift apart.

The codes themselves stay canonical everywhere they matter: CSV export keeps a
`tag_code` column, so a spreadsheet remains machine-readable whichever
language the reader chose.
"""
from __future__ import annotations

LANGUAGES = ("en", "ar")
DEFAULT_LANGUAGE = "en"

# The 24 tags of the POS student, which follows the CAMeL Tools tag set.
POS: dict[str, tuple[str, str]] = {
    "abbrev":         ("abbreviation",             "اختصار"),
    "adj":            ("adjective",                "صفة"),
    "adv":            ("adverb",                   "ظرف"),
    "adv_interrog":   ("interrogative adverb",     "ظرف استفهام"),
    "adv_rel":        ("relative adverb",          "ظرف موصول"),
    "conj":           ("conjunction",              "حرف عطف"),
    "conj_sub":       ("subordinating conjunction", "حرف مصدري"),
    "digit":          ("number",                   "رقم"),
    "interj":         ("interjection",             "أداة تعجب"),
    "noun":           ("noun",                     "اسم"),
    "noun_prop":      ("proper noun",              "اسم علم"),
    "part":           ("particle",                 "حرف"),
    "part_interrog":  ("interrogative particle",   "أداة استفهام"),
    "part_neg":       ("negation particle",        "أداة نفي"),
    "part_verb":      ("verbal particle",          "حرف مختص بالفعل"),
    "part_voc":       ("vocative particle",        "أداة نداء"),
    "prep":           ("preposition",              "حرف جر"),
    "pron":           ("pronoun",                  "ضمير"),
    "pron_dem":       ("demonstrative pronoun",    "اسم إشارة"),
    "pron_interrog":  ("interrogative pronoun",    "اسم استفهام"),
    "pron_rel":       ("relative pronoun",         "اسم موصول"),
    "punc":           ("punctuation",              "علامة ترقيم"),
    "verb":           ("verb",                     "فعل"),
    "verb_pseudo":    ("pseudo-verb",              "حرف مشبه بالفعل"),
}

# The four spans of the structure tagger.
STRUCTURE: dict[str, tuple[str, str]] = {
    "HNUM":    ("hadith number", "رقم الحديث"),
    "ISNAD":   ("isnād",         "الإسناد"),
    "MATN":    ("matn",          "المتن"),
    "HEADING": ("heading",       "العنوان"),
}

SETS = {"pos": POS, "structure": STRUCTURE}

# Column headings for the tabular exports, per task and language.
COLUMNS = {
    "pos":       {"en": ("word", "tag"), "ar": ("الكلمة", "الوسم")},
    "structure": {"en": ("segment", "label"), "ar": ("المقطع", "التصنيف")},
}

# Wording for the narrator chain drawn under an isnād. Not a tag set — these
# are headings and column names, not codes a model emits — but they live here
# for the same reason everything else does: one place, so the preview, the
# CSV, the Markdown and the PDF cannot word it differently.
NARRATORS = {
    "en": {"heading": "narrators", "n": "#", "verb": "verb", "name": "narrator"},
    "ar": {"heading": "رواة الإسناد", "n": "#", "verb": "صيغة التحمل",
           "name": "الراوي"},
}


def narrator_words(lang: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    return NARRATORS[normalize(lang)]


def normalize(lang: str | None) -> str:
    """Coerce anything the client sends into a language we actually have."""
    lang = (lang or "").strip().lower()[:2]
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def name(kind: str, code: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """Return the display name for one tag.

    An unrecognised code is passed through untouched: a model that gains a tag
    should show the raw code rather than silently render as something wrong.
    """
    pair = SETS.get(kind, {}).get(code)
    if pair is None:
        return code
    return pair[1] if normalize(lang) == "ar" else pair[0]


def columns(kind: str, lang: str = DEFAULT_LANGUAGE) -> tuple[str, str]:
    return COLUMNS.get(kind, {}).get(normalize(lang), ("item", "label"))


def glossary(kind: str) -> list[dict[str, str]]:
    """The whole tag set, for the UI legend and the /api/config payload."""
    return [{"code": code, "en": en, "ar": ar}
            for code, (en, ar) in SETS.get(kind, {}).items()]
