"""Runtime limits for the inference service.

Every value can be overridden with an `ARABICWEB_`-prefixed environment
variable, so a deployment can tighten the limits without editing code:

    ARABICWEB_MAX_UPLOAD_MB=2 ARABICWEB_CLAMD_HOST=127.0.0.1 python -m webapp

The defaults are deliberately small. This is a free public service that runs
untrusted input through XML and OLE parsers, and every limit here exists to
bound what a single request can cost us.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MB = 1024 * 1024


def _int(name: str, default: int) -> int:
    raw = os.environ.get(f"ARABICWEB_{name}")
    return default if raw is None else int(raw)


def _str(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"ARABICWEB_{name}", default) or None


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(f"ARABICWEB_{name}")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- request size -----------------------------------------------------
    max_upload_bytes: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 8) * MB)
    max_text_chars: int = field(default_factory=lambda: _int("MAX_TEXT_CHARS", 200_000))

    # --- work per request -------------------------------------------------
    # A "unit" is one line, one paragraph or one table row.
    max_units: int = field(default_factory=lambda: _int("MAX_UNITS", 2_000))
    max_unit_chars: int = field(default_factory=lambda: _int("MAX_UNIT_CHARS", 4_000))
    process_timeout_s: int = field(default_factory=lambda: _int("PROCESS_TIMEOUT_S", 180))

    # --- OOXML (.docx/.xlsx are zip archives) zip-bomb guards -------------
    zip_max_members: int = field(default_factory=lambda: _int("ZIP_MAX_MEMBERS", 2_000))
    zip_max_uncompressed: int = field(
        default_factory=lambda: _int("ZIP_MAX_UNCOMPRESSED_MB", 100) * MB)
    zip_max_ratio: int = field(default_factory=lambda: _int("ZIP_MAX_RATIO", 200))

    # --- abuse ------------------------------------------------------------
    rate_limit_requests: int = field(default_factory=lambda: _int("RATE_LIMIT_REQUESTS", 30))
    rate_limit_window_s: int = field(default_factory=lambda: _int("RATE_LIMIT_WINDOW_S", 60))

    # --- result cache backing the download buttons ------------------------
    result_ttl_s: int = field(default_factory=lambda: _int("RESULT_TTL_S", 600))
    result_cache_size: int = field(default_factory=lambda: _int("RESULT_CACHE_SIZE", 64))

    # --- optional ClamAV ---------------------------------------------------
    # When a clamd endpoint is configured every upload is streamed to it
    # before any parser touches it. `clamav_required` makes a scan failure
    # fatal instead of a logged warning - turn it on in production.
    clamd_host: str | None = field(default_factory=lambda: _str("CLAMD_HOST"))
    clamd_port: int = field(default_factory=lambda: _int("CLAMD_PORT", 3310))
    clamav_required: bool = field(default_factory=lambda: _bool("CLAMAV_REQUIRED", False))

    # --- models ------------------------------------------------------------
    device: str | None = field(default_factory=lambda: _str("DEVICE"))
    # Preload keeps the first request fast at the cost of ~1 GB of RAM.
    preload: bool = field(default_factory=lambda: _bool("PRELOAD", False))

    # --- PDF ---------------------------------------------------------------
    # An Arabic-capable TrueType font. Left unset, `render.py` searches the
    # usual system locations; PDF export is disabled if it finds nothing.
    pdf_font: Path | None = field(
        default_factory=lambda: (Path(p) if (p := _str("PDF_FONT")) else None))


settings = Settings()
