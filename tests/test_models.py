"""Smoke tests: text utilities always run; model tests need the LFS assets.

    pip install -e ".[dev]"
    pytest
"""
import unicodedata

import pytest

from arabicmodels import (Diacritizer, PosTagger, StructureTagger, apply_marks,
                          has_tashkeel, normalize_arabic, parse_isnad,
                          split_marks, strip_diacritics, whitespace_tokenize)
from arabicmodels.common import MODELS_DIR, split_of

HADITH = ("حدثنا عبد الله بن يوسف قال أخبرنا مالك عن نافع عن عبد الله بن عمر "
          "أن رسول الله صلى الله عليه وسلم قال : إنما الأعمال بالنيات")
UNIT = ("1248 - حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني نافع "
        "عن ابن عمر ان رسول الله صلى الله عليه وسلم قال من اقتنى كلبا")
# The same unit as a printed edition sets it: fully vowelled. Editions like
# this are the ordinary input to the web app, not an edge case.
UNIT_VOWELLED = (
    "1248 - حَدَّثَنَا مُحَمَّدُ بْنُ بَشَّارٍ قَالَ حَدَّثَنَا يَحْيَى عَنْ عُبَيْدِ اللَّهِ قَالَ "
    "حَدَّثَنِي نَافِعٌ عَنِ ابْنِ عُمَرَ أَنَّ رَسُولَ اللَّهِ صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ "
    "قَالَ مَنِ اقْتَنَى كَلْبًا")

needs_models = pytest.mark.skipif(
    not (MODELS_DIR / "tashkeel_bilstm_pos.pt").exists(),
    reason="model weights absent — run `git lfs pull`")


# --- text utilities --------------------------------------------------------

def test_normalize_folds_orthographic_variants():
    assert normalize_arabic("أَحْمَد") == "احمد"
    assert normalize_arabic("مكتبة") == "مكتبه"
    assert normalize_arabic("علي") == normalize_arabic("على")


def test_has_tashkeel():
    assert has_tashkeel("قَالَ")
    assert not has_tashkeel("قال")


def test_strip_diacritics_leaves_the_letters_alone():
    # Unlike normalize_arabic, which also folds أ إ آ ة ى.
    assert strip_diacritics("أَحْمَد") == "أحمد"
    assert strip_diacritics("مَكْتَبَة") == "مكتبة"
    assert not has_tashkeel(strip_diacritics("قَالَ رَسُولُ اللَّهِ"))
    # Marks never stand alone, so a word count survives it. The structure
    # tagger relies on this to keep per-word labels aligned with the text.
    v = "حَدَّثَنَا مُحَمَّدُ بْنُ بَشَّارٍ"
    assert len(strip_diacritics(v).split()) == len(v.split())


def test_whitespace_tokenize_offsets_map_back():
    text = "  حدثنا  مالك عن نافع "
    for tok in whitespace_tokenize(text):
        assert text[tok.start:tok.end] == tok.text


def test_split_marks_roundtrip_is_lossless_under_nfc():
    # apply_marks emits shadda before the vowel; canonical order is the
    # reverse, so the round trip is exact only after NFC normalization.
    vocalized = "قَالَ رَسُولُ اللَّهِ مُحَمَّدٌ"
    bare, labels = split_marks(vocalized)
    assert "\u064E" not in bare                      # marks really removed
    assert len(bare) == len(labels)
    assert unicodedata.normalize("NFC", apply_marks(bare, labels)) == \
        unicodedata.normalize("NFC", vocalized)


def test_dagger_alef_is_outside_the_16_class_inventory():
    # A documented limitation: U+0670 is stripped and never restored.
    bare, labels = split_marks("هَٰذَا")
    assert "\u0670" not in bare
    assert apply_marks(bare, labels) == "هَذَا"


def test_split_marks_classes_are_in_range():
    _, labels = split_marks("مُحَمَّدٌ")
    assert all(0 <= v < 16 for v in labels)


def test_split_of_is_deterministic_and_partitions():
    assert split_of("x") == split_of("x")
    assert {split_of(str(i)) for i in range(500)} <= {"train", "dev", "test"}


def test_parse_isnad_finds_boundary_and_narrators():
    p = parse_isnad(HADITH)
    assert p.sanad_end_raw > 30
    assert p.confidence >= 0.9
    assert len(p.hops) >= 3
    assert any("عبد الله بن يوسف" in h.mention for h in p.hops)
    # the boundary must fall inside the chain, before the report body
    assert "مالك" in HADITH[:p.sanad_end_raw]


# --- models ----------------------------------------------------------------

@needs_models
def test_diacritizer_adds_marks_without_touching_letters():
    d = Diacritizer.load(pos=True)
    out = d.diacritize("قال رسول الله صلى الله عليه وسلم")
    assert has_tashkeel(out)
    bare, _ = split_marks(out)
    assert bare == "قال رسول الله صلى الله عليه وسلم"


@needs_models
def test_diacritizer_v01_also_loads():
    d = Diacritizer.load(pos=False)
    assert not d.pos_aware
    assert has_tashkeel(d.diacritize("قال رسول الله"))


@needs_models
def test_fill_gaps_preserves_existing_marks_and_quran_spans():
    d = Diacritizer.load(pos=True)
    text = "قَالَ رسول الله ﴿الحمد لله رب العالمين﴾ وقال"
    out = d.fill_gaps(text)
    assert "قَالَ" in out                              # already-marked word kept
    assert "﴿الحمد لله رب العالمين﴾" in out            # Qurʾān span untouched
    assert split_marks(out)[0] == split_marks(text)[0]  # letters unchanged


@needs_models
def test_pos_tagger_returns_one_tag_per_word():
    t = PosTagger.load()
    pairs = t.tag("حدثنا قتيبة بن سعيد قال حدثنا سفيان عن الزهري")
    assert len(pairs) == 9
    assert all(tag in t.tags for _, tag in pairs)
    assert dict(pairs)["عن"] == "prep"


@needs_models
def test_structure_tagger_separates_chain_from_body():
    s = StructureTagger.load()
    labels = dict(s.tag(UNIT))
    assert labels["1248"] == "HNUM"
    assert labels["حدثنا"] == "ISNAD"
    assert labels["اقتنى"] == "MATN"


@needs_models
def test_structure_tagger_is_not_fooled_by_diacritics():
    """A vowelled edition must segment like its unvowelled twin.

    It did not: the training pages carry vowelled headings above unvowelled
    running text, so the model learned "carries marks" as a proxy for HEADING
    and labelled a fully vowelled isnād as a section title. The encoder is now
    shown the bare skeleton. Guarding the property rather than the mechanism,
    so a retrain that genuinely learns the distinction also passes.
    """
    s = StructureTagger.load()
    assert ([t for _, t in s.tag(UNIT_VOWELLED)]
            == [t for _, t in s.tag(strip_diacritics(UNIT_VOWELLED))])
    labels = dict(s.tag(UNIT_VOWELLED))
    assert labels["حَدَّثَنَا"] == "ISNAD"
    assert labels["اقْتَنَى"] == "MATN"
    assert "HEADING" not in labels.values()
    # The marks belong to the reader: only the encoder sees them removed.
    assert "".join(c for _, c in s.segments(UNIT_VOWELLED)).count("\u0651") > 0


@needs_models
def test_structure_segments_return_the_caller_s_own_text():
    s = StructureTagger.load()
    assert " ".join(c for _, c in s.segments(UNIT_VOWELLED)) == UNIT_VOWELLED


@needs_models
def test_structure_spans_are_ordered_and_in_bounds():
    s = StructureTagger.load()
    spans = s.spans(UNIT)
    assert spans
    for start, end, label in spans:
        assert 0 <= start < end <= len(UNIT)
        assert label in StructureTagger.LABELS
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
