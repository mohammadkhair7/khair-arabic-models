"""Every input type reaches a model, and every output format comes back.

The model tests need the checkpoints, so they skip cleanly when the repo has
not had `git lfs pull` run against it.
"""
import csv
import io

import pytest

MODELS_PRESENT = True
try:
    from arabicmodels import Diacritizer  # noqa: F401
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ckpt = root / "models" / "tashkeel_bilstm_pos.pt"
    # An LFS pointer is ~130 bytes; the real checkpoint is 20 MB.
    MODELS_PRESENT = ckpt.is_file() and ckpt.stat().st_size > 1_000_000
except Exception:                                    # noqa: BLE001
    MODELS_PRESENT = False

needs_models = pytest.mark.skipif(
    not MODELS_PRESENT, reason="checkpoints missing — run `git lfs pull`")


def upload(client, name, data, task="tashkeel", **extra):
    return client.post("/api/process", data={"task": task, **extra},
                       files={"file": (name, io.BytesIO(data))})


# ------------------------------------------------------------------ config

def test_config_lists_tasks_and_formats(client):
    body = client.get("/api/config").json()
    keys = {t["key"] for t in body["tasks"]}
    assert {"tashkeel", "tashkeel_gaps", "pos", "structure"} <= keys
    assert {f["key"] for f in body["formats"]} == {"txt", "csv", "md", "pdf"}
    assert set(body["accept"]) == {".txt", ".csv", ".xlsx", ".doc", ".docx"}


def test_config_never_exposes_the_sendgrid_key(client):
    """The single most important assertion in this file."""
    raw = client.get("/api/config").text
    assert "SENDGRID" not in raw.upper()
    assert "SG." not in raw
    body = client.get("/api/config").json()
    assert isinstance(body["feedback"], bool)


def test_config_offers_both_donation_types(client):
    donate = client.get("/api/config").json()["donate"]
    assert {d["key"] for d in donate} == {"monthly", "onetime"}
    assert all(d["url"].startswith("https://buy.stripe.com/") for d in donate)
    assert all(d["label_ar"] for d in donate)


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "alarabia.chat" in response.text
    # The open-source link the About tab promises.
    assert "github.com/qurancomp/khair-arabic-models" in response.text


# ------------------------------------------------------------- input types

@needs_models
def test_pasted_text(client):
    response = client.post("/api/process", data={
        "task": "tashkeel", "text": "قال رسول الله صلى الله عليه وسلم"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_units"] == 1
    # Diacritics were actually added.
    assert any("\u064b" <= ch <= "\u0652" for ch in body["units"][0]["output"])


@needs_models
def test_txt_upload(client, txt_bytes):
    body = upload(client, "hadith.txt", txt_bytes).json()
    assert body["total_units"] == 3
    assert body["source"] == "hadith.txt"
    assert body["is_table"] is False


@needs_models
def test_csv_upload_detects_the_arabic_column(client, csv_bytes):
    body = upload(client, "hadith.csv", csv_bytes).json()
    assert body["is_table"] is True
    assert body["columns"] == ["id", "page", "matn"]
    assert any("column 'matn'" in n for n in body["notes"])
    assert body["total_units"] == 3


@needs_models
def test_csv_column_can_be_chosen_explicitly(client, csv_bytes):
    body = upload(client, "h.csv", csv_bytes, column="page").json()
    assert any("column 'page'" in n for n in body["notes"])


@needs_models
def test_xlsx_upload(client, xlsx_bytes):
    body = upload(client, "hadith.xlsx", xlsx_bytes).json()
    assert body["is_table"] is True
    assert body["columns"] == ["id", "page", "matn"]
    assert any("sheet 'hadith'" in n for n in body["notes"])


@needs_models
def test_docx_upload(client, docx_bytes):
    body = upload(client, "hadith.docx", docx_bytes).json()
    assert body["total_units"] == 3


@needs_models
def test_utf16_and_cp1256_text_files_decode(client):
    line = "قال رسول الله"
    for encoding in ("utf-16", "cp1256"):
        body = upload(client, "x.txt", line.encode(encoding)).json()
        assert body["units"][0]["source"].strip().startswith("قال")


# ----------------------------------------------------------------- tasks

@needs_models
def test_pos_returns_word_tag_pairs(client):
    body = client.post("/api/process", data={
        "task": "pos", "text": "حدثنا قتيبة بن سعيد"}).json()
    pairs = body["units"][0]["pairs"]
    assert [p[0] for p in pairs] == ["حدثنا", "قتيبة", "بن", "سعيد"]
    assert all(isinstance(p[1], str) and p[1] for p in pairs)


@needs_models
def test_structure_returns_labelled_segments(client):
    unit = ("1248 - حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني "
            "نافع عن ابن عمر ان رسول الله صلى الله عليه وسلم قال من اقتنى كلبا")
    body = client.post("/api/process", data={"task": "structure", "text": unit}).json()
    labels = {label for label, _ in body["units"][0]["pairs"]}
    assert labels <= {"HNUM", "ISNAD", "MATN", "HEADING"}
    assert "ISNAD" in labels


@needs_models
def test_fill_gaps_preserves_existing_diacritics(client):
    body = client.post("/api/process", data={
        "task": "tashkeel_gaps", "text": "قَالَ رسول الله"}).json()
    assert body["units"][0]["output"].startswith("قَالَ")


@needs_models
def test_blank_lines_are_preserved_so_output_stays_aligned(client):
    body = client.post("/api/process", data={
        "task": "tashkeel", "text": "قال رسول الله\n\nوقال ابن عمر"}).json()
    assert body["total_units"] == 3
    assert body["units"][1]["output"] == ""


# -------------------------------------------------------------- downloads

@needs_models
@pytest.mark.parametrize("fmt,sniff", [
    ("txt", None),
    ("csv", None),
    ("md", b"#"),
    ("pdf", b"%PDF"),
])
def test_every_format_downloads(client, txt_bytes, fmt, sniff):
    token = upload(client, "hadith.txt", txt_bytes).json()["token"]
    response = client.get(f"/api/download/{token}/{fmt}")
    if fmt == "pdf" and response.status_code == 503:
        pytest.skip("no Arabic font on this machine")
    assert response.status_code == 200
    assert response.content
    if sniff:
        assert response.content.startswith(sniff)
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


@needs_models
def test_csv_export_keeps_the_users_own_columns(client, csv_bytes):
    token = upload(client, "hadith.csv", csv_bytes).json()["token"]
    body = client.get(f"/api/download/{token}/csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0] == ["id", "page", "matn", "tashkeel"]
    assert len(rows) == 4


@needs_models
def test_csv_export_of_pos_is_token_level(client):
    token = client.post("/api/process", data={
        "task": "pos", "text": "حدثنا قتيبة بن سعيد"}).json()["token"]
    body = client.get(f"/api/download/{token}/csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0] == ["line", "word", "tag"]
    assert len(rows) == 5


@needs_models
def test_csv_export_starts_with_a_bom_so_excel_reads_arabic(client, txt_bytes):
    token = upload(client, "h.txt", txt_bytes).json()["token"]
    assert client.get(f"/api/download/{token}/csv").content.startswith(b"\xef\xbb\xbf")


@needs_models
def test_download_filename_is_derived_safely(client, txt_bytes):
    token = upload(client, "../../etc/passwd.txt", txt_bytes).json()["token"]
    disposition = client.get(f"/api/download/{token}/txt").headers["content-disposition"]
    assert ".." not in disposition
    assert "/etc/" not in disposition


@needs_models
def test_markdown_export_carries_the_credit_line(client, txt_bytes):
    token = upload(client, "h.txt", txt_bytes).json()["token"]
    body = client.get(f"/api/download/{token}/md").content.decode("utf-8")
    assert "Mohammad Mohammad Khair" in body
    assert "Apache 2.0" in body


# --------------------------------------------------------------- feedback

def test_feedback_honeypot_is_silently_accepted(client):
    response = client.post("/api/feedback", data={
        "name": "bot", "email": "bot@example.com",
        "message": "buy something", "website": "http://spam"})
    assert response.status_code == 200


def test_feedback_requires_a_name(client):
    response = client.post("/api/feedback", data={
        "name": "  ", "email": "a@b.com", "message": "hello there"})
    assert response.status_code == 400


def test_feedback_reports_missing_configuration_without_leaking(client):
    """With no key set the form must fail closed and stay quiet about why."""
    from webapp.app import integrations
    if integrations.feedback_available():
        pytest.skip("SendGrid is configured in this environment")
    response = client.post("/api/feedback", data={
        "name": "Reader", "email": "reader@example.com",
        "message": "The diacritics on page 3 look wrong."})
    assert response.status_code == 400
    assert "SG." not in response.text


def test_secret_type_never_renders_its_value():
    from webapp.app.integrations import Secret
    secret = Secret("SG." + "x" * 40)
    assert "x" * 40 not in repr(secret)
    assert "x" * 40 not in str(secret)
    assert "x" * 40 not in f"{secret}"
    assert "x" * 40 not in "{}".format(secret)          # noqa: UP032
    assert secret.reveal().endswith("x")


def test_header_injection_in_feedback_name_is_stripped():
    from webapp.app.integrations import _clean_header
    assert "\n" not in _clean_header("Bob\nBcc: victim@example.com")
    assert "\r" not in _clean_header("Bob\r\nSubject: spam")
