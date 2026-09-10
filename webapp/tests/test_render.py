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


# --- the narrator chain in the exports -------------------------------------

CHAIN = "حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله"
BODY = "ان رسول الله صلى الله عليه وسلم قال من اقتنى كلبا"


def structure_result(lang="en"):
    """A structure result with a chain in it, built without loading a model.

    The exporters are what is under test, so the model's job — deciding which
    span is the isnād — is stated here rather than run.
    """
    from webapp.app.extract import Document
    from webapp.app.process import Result, Unit, _format_pairs, _narrators

    pairs = [("ISNAD", CHAIN), ("MATN", BODY)]
    narrators = _narrators(pairs)
    text = CHAIN + " " + BODY
    return Result(task="structure", task_label="Hadith structure",
                  units=[Unit(1, text, _format_pairs("structure", pairs, lang,
                                                     narrators),
                              pairs, narrators)],
                  document=Document(units=[text]), elapsed_s=0.1, lang=lang)


def test_narrators_are_split_from_the_segment_the_model_marked():
    result = structure_result()
    names = [h.name for h in result.units[0].narrators]
    assert names == ["محمد بن بشار", "يحيى", "عبيد الله"]
    assert [h.verb for h in result.units[0].narrators] == ["حدثنا", "حدثنا", "عن"]
    # All from the ISNAD pair; the matn contributes none.
    assert {h.seg for h in result.units[0].narrators} == {0}


@pytest.mark.parametrize("fmt", ["txt", "csv", "md"])
def test_every_text_export_carries_the_chain(fmt):
    from webapp.app.render import render

    blob = render(structure_result(), fmt).decode("utf-8-sig")
    for name in ("محمد بن بشار", "يحيى", "عبيد الله"):
        assert name in blob
    assert "حدثنا" in blob


def test_csv_keeps_one_row_per_narrator_and_stays_rectangular():
    import csv
    import io

    from webapp.app.render import as_csv

    rows = list(csv.reader(io.StringIO(as_csv(structure_result())
                                       .decode("utf-8-sig"))))
    assert len({len(r) for r in rows}) == 1     # header and body agree
    body = rows[1:]
    assert [r[-1] for r in body[:3]] == ["محمد بن بشار", "يحيى", "عبيد الله"]
    assert [r[-4] for r in body[:3]] == ["ISNAD"] * 3
    # The matn is still one row, with the narrator columns left empty.
    assert body[-1][-4:] == ["MATN", "", "", ""]


@pytest.mark.skipif(find_font() is None, reason="no Arabic font installed")
def test_pdf_carries_the_chain():
    from webapp.app.render import as_pdf
    assert as_pdf(structure_result()).startswith(b"%PDF")


def test_switching_language_rewords_the_chain_without_losing_it():
    from webapp.app.process import relabel

    ar = relabel(structure_result("en"), "ar")
    assert "رواة الإسناد" in ar.units[0].output
    assert "محمد بن بشار" in ar.units[0].output
    # The names are spans of the source, so they never translate.
    assert [h.name for h in ar.units[0].narrators] == ["محمد بن بشار", "يحيى",
                                                       "عبيد الله"]
