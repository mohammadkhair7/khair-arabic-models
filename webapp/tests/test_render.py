"""Exporter behaviour, especially the right-to-left PDF line breaking.

The wrap-order bug these guard against is invisible in a byte comparison and
obvious to any Arabic reader: the sentence comes out back to front.
"""
import pytest

from webapp.app.render import find_font, wrap_words

HADITH = ("1248 - حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني "
          "نافع عن ابن عمر ان رسول الله صلى الله عليه وسلم")


def fits_n_words(n):
    """A width test that admits at most `n` words, for deterministic tests."""
    return lambda s: len(s.split()) <= n


def test_wrapping_preserves_word_order():
    lines = wrap_words(HADITH, fits_n_words(5))
    assert " ".join(lines).split() == HADITH.split()


def test_first_line_starts_with_the_first_word():
    """The regression: '1248' must not be pushed onto a later line."""
    lines = wrap_words(HADITH, fits_n_words(6))
    assert lines[0].split()[0] == "1248"


def test_every_line_respects_the_width_test():
    lines = wrap_words(HADITH, fits_n_words(4))
    assert all(len(line.split()) <= 4 for line in lines)


def test_a_word_longer_than_a_line_is_emitted_rather_than_looping():
    lines = wrap_words("short averyveryverylongtoken", lambda s: len(s) <= 6)
    assert "averyveryverylongtoken" in " ".join(lines)


def test_empty_text_yields_one_empty_line():
    assert wrap_words("   ", lambda s: True) == [""]


def test_text_that_fits_is_left_alone():
    assert wrap_words("قال رسول الله", lambda s: True) == ["قال رسول الله"]


# --------------------------------------------------------------------------

def test_font_search_finds_something_or_says_so():
    """PDF export is allowed to be unavailable, but never to lie about it."""
    from webapp.app.render import pdf_available
    assert pdf_available() == (find_font() is not None)


@pytest.mark.skipif(find_font() is None, reason="no Arabic font installed")
def test_pdf_is_a_pdf_and_embeds_the_text():
    from webapp.app.extract import Document
    from webapp.app.process import Result, Unit
    from webapp.app.render import as_pdf

    result = Result(task="tashkeel", task_label="Diacritize",
                    units=[Unit(1, HADITH, HADITH)],
                    document=Document(units=[HADITH]), elapsed_s=0.1)
    blob = as_pdf(result)
    assert blob.startswith(b"%PDF")
    assert len(blob) > 10_000          # a real font got embedded
