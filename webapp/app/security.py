"""Everything an upload has to survive before a parser is allowed near it.

The threat model is a free, publicly reachable endpoint that accepts documents
from strangers. We never execute an upload, but the parsers we hand it to
(`zipfile`, `openpyxl`, `python-docx`, `olefile`) are C-backed XML and OLE
readers, and those are exactly the places where hostile files do damage. The
defences below are layered in the order an attacker would meet them:

1. `check_extension` - allowlist, never a denylist, and derived from our own
   sanitised name rather than the browser's `filename`.
2. `read_upload` - a hard byte cap enforced *while streaming*, so an
   attacker cannot make us allocate a gigabyte before we notice.
3. `scan` - optional ClamAV pass over the raw bytes, before any parsing.
4. `sniff` - the declared extension must agree with the magic bytes, which
   stops `payload.exe` renamed to `notes.txt` from reaching a text decoder.
5. `inspect_zip` - decompression bombs, path traversal and macro parts in
   the OOXML container.
6. `assert_no_xxe` - external entities and DTDs in the XML parts, which is
   how OOXML files exfiltrate server-side files.

Uploads are held in memory and never written to disk, so there is no file for
a later process (a backup job, an antivirus scanner, a shell glob) to trip
over, and nothing to clean up when a request fails.
"""
from __future__ import annotations

import re
import socket
import unicodedata
from pathlib import PurePosixPath

from fastapi import UploadFile

from .config import settings


class UnsafeUpload(Exception):
    """Rejected input. The message is safe to show the user verbatim."""


ALLOWED_EXTENSIONS = {".txt", ".csv", ".xlsx", ".doc", ".docx"}

# Containers we must see the right magic for. Anything else is a text format.
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Magic numbers that must never appear at the head of a "text" upload. A .txt
# that starts with `MZ` is a Windows executable wearing a hat.
_BINARY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xca\xfe\xba\xbe", "a Mach-O/Java binary"),
    (b"PK\x03\x04", "a zip archive"),
    (_OLE_MAGIC, "a legacy Office/OLE file"),
    (b"%PDF", "a PDF"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"Rar!", "a RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "a 7-Zip archive"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"\x89PNG", "a PNG image"),
)

# Parts an OOXML document has no business containing on this service.
_FORBIDDEN_ZIP_PARTS = re.compile(
    r"(vbaproject\.bin|vbadata\.xml|\.bin$|oleobject\d*\.bin"
    r"|externallink|activex)", re.IGNORECASE)

# The XML constructs behind XXE and billion-laughs.
_XML_DANGER = re.compile(rb"<!DOCTYPE|<!ENTITY|SYSTEM\s+[\"']|PUBLIC\s+[\"']",
                         re.IGNORECASE)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u0600-\u06FF -]")


def sanitize_filename(name: str | None, fallback: str = "input") -> str:
    """Reduce a browser-supplied filename to something safe to echo and store.

    Strips any directory component (including Windows `\\` and UNC forms),
    normalises Unicode so look-alike characters cannot smuggle a separator
    past us, and keeps only letters, digits, Arabic, and a few punctuation
    marks. The result is used for display and for the download filename - it
    is never used to build a path we open.
    """
    if not name:
        return fallback
    name = unicodedata.normalize("NFKC", name).replace("\\", "/")
    name = PurePosixPath(name).name          # drop every directory component
    name = _SAFE_NAME.sub("_", name).strip(" .")
    # Collapse runs so "....exe" cannot rebuild a double extension visually.
    name = re.sub(r"_{2,}", "_", name)
    return name[:120] or fallback


def check_extension(filename: str) -> str:
    """Return the lower-cased extension, or refuse. Allowlist only."""
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UnsafeUpload(
            f"'{ext or 'no extension'}' is not an accepted file type. "
            f"Accepted: {allowed}.")
    return ext


async def read_upload(upload: UploadFile) -> bytes:
    """Read an upload into memory, refusing to exceed the configured cap.

    The cap is checked per chunk rather than after the fact: a client that
    lies in `Content-Length` still cannot make us buffer more than the limit
    plus one chunk.
    """
    cap = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > cap:
            raise UnsafeUpload(
                f"File is larger than the {cap // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    if not total:
        raise UnsafeUpload("The uploaded file is empty.")
    return b"".join(chunks)


def scan(data: bytes) -> str:
    """Stream the bytes past ClamAV, if one is configured.

    Returns a short status for the response so the caller can see whether a
    scan actually happened. With `ARABICWEB_CLAMAV_REQUIRED=1` an unreachable
    scanner fails the request instead of waving it through, which is what you
    want in production; the default is lenient so the app still runs on a
    laptop with no clamd.
    """
    host = settings.clamd_host
    if not host:
        return "skipped (no scanner configured)"
    try:
        with socket.create_connection((host, settings.clamd_port), timeout=10) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 8192):
                block = data[i:i + 8192]
                sock.sendall(len(block).to_bytes(4, "big") + block)
            sock.sendall((0).to_bytes(4, "big"))
            reply = sock.recv(4096).decode("utf-8", "replace").strip("\0 \n")
    except OSError as exc:
        if settings.clamav_required:
            raise UnsafeUpload(
                "The virus scanner is unavailable, so this upload was "
                "refused. Please try again later.") from exc
        return f"unavailable ({exc.__class__.__name__})"

    if reply.endswith("OK"):
        return "clean"
    if "FOUND" in reply:
        signature = reply.split(":", 1)[-1].replace("FOUND", "").strip()
        raise UnsafeUpload(f"This file was flagged by the virus scanner ({signature}).")
    raise UnsafeUpload(f"The virus scanner could not process this file ({reply}).")


def sniff(data: bytes, ext: str) -> None:
    """Require the file's real shape to match its claimed extension."""
    head = data[:8]
    if ext in {".docx", ".xlsx"}:
        if not data.startswith(_ZIP_MAGIC):
            raise UnsafeUpload(
                f"This does not look like a real {ext} file (a {ext} is a zip "
                "archive and this one is not). If you renamed the file, save "
                "it again from Word or Excel instead.")
    elif ext == ".doc":
        if data.startswith(_ZIP_MAGIC):
            raise UnsafeUpload(
                "This is actually a .docx file saved with a .doc name. "
                "Rename it to .docx and upload it again.")
        if not data.startswith(_OLE_MAGIC):
            raise UnsafeUpload("This does not look like a real Word .doc file.")
    else:                                     # .txt / .csv
        for magic, human in _BINARY_MAGIC:
            if head.startswith(magic):
                raise UnsafeUpload(
                    f"That {ext} file is really {human}. Only plain text is "
                    "accepted here.")
        # A stray NUL is the cheapest tell that a "text" file is binary - but
        # only for 8-bit encodings. UTF-16 and UTF-32 are full of NULs by
        # design (every ASCII space is `20 00`), and Excel exports Arabic CSVs
        # as UTF-16 all the time, so a BOM exempts the file from this check.
        if not data.startswith((b"\xff\xfe", b"\xfe\xff")) \
                and b"\x00" in data[:4096]:
            raise UnsafeUpload(
                f"That {ext} file contains binary data, not text.")


def decode_text(data: bytes) -> str:
    """Decode a text upload, trying the encodings Arabic files actually use.

    Order matters: UTF-8 first (with BOM stripping), then the UTF-16 variants
    Excel likes to emit, then Windows-1256, which is what older Arabic
    Windows tools produce and which will happily decode any byte string - so
    it has to be last.
    """
    for encoding in ("utf-8-sig", "utf-16", "utf-32"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return data.decode("cp1256")
    except UnicodeDecodeError as exc:
        raise UnsafeUpload("The file's text encoding could not be read.") from exc


def inspect_zip(data: bytes, expect_prefix: str) -> None:
    """Vet an OOXML container before `openpyxl`/`python-docx` opens it.

    `expect_prefix` is `word/` or `xl/`: a file claiming to be a document
    must actually carry that part, which weeds out archives that are only
    wearing an Office extension.

    Guards, in order of how much damage they prevent: a decompression bomb
    (member count, total inflated size, and per-member ratio - a 200:1 entry
    is not a real spreadsheet), path traversal in member names, and macro or
    embedded-object parts we have no reason to accept.
    """
    import zipfile           # local: keeps the import off the healthy path
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > settings.zip_max_members:
                raise UnsafeUpload(
                    f"This file contains {len(infos)} internal parts, well "
                    "past what a real document needs. It was refused as a "
                    "possible decompression bomb.")

            total = 0
            for info in infos:
                name = info.filename
                if name.startswith(("/", "\\")) \
                        or re.match(r"^[A-Za-z]:", name) \
                        or ".." in PurePosixPath(name.replace("\\", "/")).parts:
                    raise UnsafeUpload(
                        "This file contains an internal path that tries to "
                        "escape its own folder. It was refused.")
                if _FORBIDDEN_ZIP_PARTS.search(name):
                    raise UnsafeUpload(
                        "This document embeds macros or external objects "
                        f"('{PurePosixPath(name).name}'). Please re-save it "
                        "as a plain .docx/.xlsx without macros.")
                total += info.file_size
                if total > settings.zip_max_uncompressed:
                    raise UnsafeUpload(
                        "This file expands to more than "
                        f"{settings.zip_max_uncompressed // (1024 * 1024)} MB "
                        "when opened. It was refused as a decompression bomb.")
                if info.compress_size > 0 and \
                        info.file_size / info.compress_size > settings.zip_max_ratio:
                    raise UnsafeUpload(
                        "This file contains a part that expands over "
                        f"{settings.zip_max_ratio}x. It was refused as a "
                        "decompression bomb.")

            names = {i.filename for i in infos}
            if not any(n.startswith(expect_prefix) for n in names):
                raise UnsafeUpload(
                    "This archive is not a valid Office document - it has no "
                    f"'{expect_prefix}' content.")

            # XXE lives in the XML parts, so check them before a parser does.
            for info in infos:
                if info.filename.lower().endswith((".xml", ".rels")) \
                        and info.file_size <= 8 * 1024 * 1024:
                    assert_no_xxe(zf.read(info))
    except zipfile.BadZipFile as exc:
        raise UnsafeUpload("This file is not a readable Office document.") from exc


def assert_no_xxe(xml: bytes) -> None:
    """Refuse XML carrying a DTD or entity declaration.

    We check the raw bytes rather than trusting a parser's flags, because the
    upload passes through several parsers with different defaults. A DOCTYPE
    has no legitimate use inside OOXML, so rejecting outright costs us
    nothing and closes off both XXE file disclosure and entity expansion.
    """
    if _XML_DANGER.search(xml[:65536]):
        raise UnsafeUpload(
            "This document contains an XML doctype or external entity "
            "reference, which is a known attack pattern. It was refused.")


_CSV_INJECTION = re.compile(r"^[=+\-@\t\r]")


def csv_safe(value: str) -> str:
    """Neutralise a cell that a spreadsheet would treat as a formula.

    Our output is meant to be opened in Excel, and Excel executes anything
    starting with `=`, `+`, `-` or `@` - including `=cmd|...`. Arabic text
    never legitimately starts with those, so prefixing a single quote is a
    free fix that keeps the visible text intact.
    """
    return "'" + value if _CSV_INJECTION.match(value) else value
