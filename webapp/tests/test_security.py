"""Every hostile upload must be refused before a parser is reached.

These are the tests that matter most: the app's whole exposure is that it
opens documents from strangers. Each case names the attack it stands for.
"""
import io

import pytest

from webapp.app import security
from webapp.app.security import UnsafeUpload


def post(client, filename, data, task="tashkeel"):
    return client.post("/api/process", data={"task": task},
                       files={"file": (filename, io.BytesIO(data),
                                       "application/octet-stream")})


# --------------------------------------------------------------- allowlist

@pytest.mark.parametrize("name", [
    "payload.exe", "run.sh", "shell.php", "archive.zip", "sheet.xlsm",
    "doc.docm", "image.svg", "noextension",
])
def test_extension_allowlist_refuses_everything_else(name):
    with pytest.raises(UnsafeUpload):
        security.check_extension(name)


@pytest.mark.parametrize("name", [".txt", ".csv", ".xlsx", ".doc", ".docx"])
def test_allowlist_accepts_the_five_documented_types(name):
    assert security.check_extension("file" + name) == name


def test_extension_check_is_case_insensitive():
    assert security.check_extension("REPORT.XLSX") == ".xlsx"


# ------------------------------------------------------- filename handling

@pytest.mark.parametrize("hostile,forbidden", [
    ("../../../../etc/passwd", ".."),
    (r"..\..\windows\system32\config", ".."),
    ("/absolute/path.txt", "/"),
    (r"C:\Users\admin\secret.txt", "\\"),
    ("//server/share/file.txt", "/"),
])
def test_sanitize_filename_strips_every_path_component(hostile, forbidden):
    safe = security.sanitize_filename(hostile)
    assert forbidden not in safe
    assert "/" not in safe and "\\" not in safe


def test_sanitize_filename_keeps_arabic_and_drops_control_characters():
    assert "حديث" in security.sanitize_filename("حديث.txt")
    assert "\n" not in security.sanitize_filename("a\nb.txt")


def test_sanitize_filename_survives_unicode_lookalike_separators():
    """NFKC folds fullwidth solidus to '/', which must then be stripped."""
    safe = security.sanitize_filename("evil\uff0f..\uff0fpasswd.txt")
    assert "/" not in safe


def test_empty_filename_falls_back_rather_than_crashing():
    assert security.sanitize_filename("") == "input"
    assert security.sanitize_filename(None) == "input"


# ------------------------------------------------------------- magic bytes

def test_executable_renamed_to_txt_is_refused(client, exe_disguised_as_txt):
    response = post(client, "notes.txt", exe_disguised_as_txt)
    assert response.status_code == 400
    assert "executable" in response.json()["error"].lower()


def test_txt_containing_nul_bytes_is_refused(client):
    response = post(client, "notes.txt", b"hello\x00\x00world" + b"\x00" * 100)
    assert response.status_code == 400
    assert "binary" in response.json()["error"].lower()


def test_docx_that_is_not_a_zip_is_refused(client):
    response = post(client, "report.docx", b"just some plain text, honest")
    assert response.status_code == 400
    assert "zip archive" in response.json()["error"]


def test_docx_renamed_to_doc_gets_a_useful_message(client, docx_bytes):
    response = post(client, "report.doc", docx_bytes)
    assert response.status_code == 400
    assert ".docx" in response.json()["error"]


def test_pdf_renamed_to_csv_is_refused(client):
    response = post(client, "data.csv", b"%PDF-1.7\n" + b"x" * 500)
    assert response.status_code == 400


# ----------------------------------------------------------------- OOXML

def test_zip_bomb_is_refused(client, zip_bomb_xlsx):
    response = post(client, "book.xlsx", zip_bomb_xlsx)
    assert response.status_code == 400
    assert "decompression bomb" in response.json()["error"]


def test_xxe_external_entity_is_refused(client, xxe_docx):
    response = post(client, "report.docx", xxe_docx)
    assert response.status_code == 400
    assert "entity" in response.json()["error"].lower()


def test_macro_part_is_refused(client, macro_docx):
    response = post(client, "report.docx", macro_docx)
    assert response.status_code == 400
    assert "macro" in response.json()["error"].lower()


def test_zip_path_traversal_is_refused(client, traversal_xlsx):
    response = post(client, "book.xlsx", traversal_xlsx)
    assert response.status_code == 400
    assert "escape" in response.json()["error"].lower()


def test_archive_without_office_parts_is_refused(client):
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("hello.txt", "not an office document")
    response = post(client, "book.xlsx", buffer.getvalue())
    assert response.status_code == 400


# -------------------------------------------------------------- size caps

def test_oversized_upload_is_refused(client):
    from webapp.app.config import settings
    oversize = b"a" * (settings.max_upload_bytes + 2048)
    response = post(client, "big.txt", oversize)
    assert response.status_code == 400
    assert "larger than" in response.json()["error"]


def test_empty_upload_is_refused(client):
    response = post(client, "empty.txt", b"")
    assert response.status_code == 400


def test_overlong_pasted_text_is_refused(client):
    from webapp.app.config import settings
    response = client.post("/api/process", data={
        "task": "tashkeel", "text": "ا" * (settings.max_text_chars + 10)})
    assert response.status_code == 400
    assert "limit" in response.json()["error"]


# ----------------------------------------------------------- CSV injection

@pytest.mark.parametrize("payload", [
    "=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(1:1)", "\t=1+1", "\r=1+1",
])
def test_formula_cells_are_neutralised(payload):
    assert security.csv_safe(payload).startswith("'")


def test_ordinary_arabic_is_not_altered():
    text = "قال رسول الله"
    assert security.csv_safe(text) == text


# ------------------------------------------------------------ XML guards

def test_assert_no_xxe_rejects_doctype_and_entity():
    for payload in (b"<!DOCTYPE foo>", b"<!ENTITY x SYSTEM 'file:///etc/passwd'>",
                    b"<a SYSTEM \"http://evil\">"):
        with pytest.raises(UnsafeUpload):
            security.assert_no_xxe(payload)


def test_assert_no_xxe_allows_ordinary_ooxml():
    security.assert_no_xxe(b'<?xml version="1.0"?><w:document><w:p/></w:document>')


# ------------------------------------------------------------ HTTP surface

def test_security_headers_are_set(client):
    headers = client.get("/").headers
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_interactive_api_docs_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_unknown_task_is_refused(client):
    response = client.post("/api/process", data={"task": "../../etc", "text": "نص"})
    assert response.status_code == 400


def test_download_token_must_exist(client):
    assert client.get("/api/download/not-a-real-token/txt").status_code == 404


def test_download_format_is_allowlisted(client):
    assert client.get("/api/download/anything/exe").status_code == 404


def test_rate_limit_kicks_in(client):
    """The limiter must actually fire; `fresh_rate_limit` resets it per test."""
    from webapp.app.config import settings
    codes = [client.post("/api/process", data={"task": "tashkeel", "text": ""}).status_code
             for _ in range(settings.rate_limit_requests + 3)]
    assert 429 in codes
    assert codes[0] != 429


def test_feedback_rate_limit_is_separate_and_tight(client):
    """Five messages an hour, matching hadith.chat's contact form."""
    from webapp.app import integrations
    allowed = [integrations.rate_ok("10.0.0.9") for _ in range(integrations.RATE_MAX + 2)]
    assert allowed[:integrations.RATE_MAX] == [True] * integrations.RATE_MAX
    assert allowed[-1] is False
