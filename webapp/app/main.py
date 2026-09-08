"""HTTP surface for the Arabic model inference service.

Three endpoints do all the work: `/api/config` tells the page what it may
offer, `/api/process` accepts text or a file and returns a preview, and
`/api/download/...` hands back the same result in one of four formats.

The download step is served from a short-lived in-memory cache rather than by
re-uploading, so switching between TXT, CSV, Markdown and PDF costs nothing.
Results are keyed by an unguessable token, expire after `RESULT_TTL_S`, and
never touch disk.
"""
from __future__ import annotations

import logging
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartParser

from . import extract, integrations, labels, process, render, security
from .config import settings
from .integrations import FeedbackError
from .security import UnsafeUpload

log = logging.getLogger("arabicweb")

STATIC = Path(__file__).resolve().parent.parent / "static"

# Keep multipart bodies in RAM instead of letting Starlette spool them to a
# temporary file. Nothing an attacker sends should ever exist as a file on
# this host, and our own cap is well under any sane amount of memory.
_slack = settings.max_upload_bytes + 1024 * 1024
for _attr in ("spool_max_size", "max_part_size", "max_file_size"):
    if hasattr(MultiPartParser, _attr):
        setattr(MultiPartParser, _attr, _slack)

# How many units the JSON preview carries. The rest stay in the cache and
# come back through a download; a browser should not have to lay out 2,000
# paragraphs to show someone their result.
PREVIEW_UNITS = 300

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.preload:
        await run_in_threadpool(process.preload)
    yield


# The interactive docs are switched off: they are a map of the attack surface
# and this service has no API users who need them.
app = FastAPI(title="alarabia.chat", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------
# result cache
# --------------------------------------------------------------------------

_results: "OrderedDict[str, tuple[float, process.Result]]" = OrderedDict()


def _cache(result: process.Result) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    for key, (expires, _) in list(_results.items()):
        if expires < now:
            del _results[key]
    while len(_results) >= settings.result_cache_size:
        _results.popitem(last=False)
    _results[token] = (now + settings.result_ttl_s, result)
    return token


def _uncache(token: str) -> process.Result:
    entry = _results.get(token)
    if entry is None or entry[0] < time.time():
        _results.pop(token, None)
        raise HTTPException(404, "That result has expired. Please run it again.")
    return entry[1]


# --------------------------------------------------------------------------
# middleware
# --------------------------------------------------------------------------

_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Rate-limit writes and lock down the browser's capabilities.

    The CSP is the important half: the page renders text that came from an
    uploaded file, so even though the frontend only ever assigns to
    `textContent`, a strict policy with no inline script and no external
    origins means a markup-injection bug could not be turned into anything.
    """
    if request.method == "POST":
        client = request.client.host if request.client else "unknown"
        window = settings.rate_limit_window_s
        now = time.time()
        hits = _hits[client]
        while hits and hits[0] < now - window:
            hits.popleft()
        if len(hits) >= settings.rate_limit_requests:
            return JSONResponse(
                {"error": f"Too many requests. Please wait {window} seconds."},
                status_code=429)
        hits.append(now)

    response = await call_next(request)
    response.headers.update({
        "Content-Security-Policy":
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    })
    return response


@app.exception_handler(UnsafeUpload)
async def _rejected(_request: Request, exc: UnsafeUpload):
    return JSONResponse({"error": str(exc)}, status_code=400)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "models_loaded": process.loaded_tasks()}


@app.get("/api/config")
async def config() -> dict:
    """Everything the page needs to build itself and set expectations."""
    return {
        "tasks": [
            {"key": t.key, "label": t.label, "label_ar": t.label_ar,
             "description": t.description, "description_ar": t.description_ar,
             "tag_kind": t.tag_kind}
            for t in process.TASKS.values()
        ],
        "formats": [
            {"key": "txt", "label": "Text (.txt)", "available": True},
            {"key": "csv", "label": "Spreadsheet (.csv)", "available": True},
            {"key": "md", "label": "Markdown (.md)", "available": True},
            {"key": "pdf", "label": "PDF (.pdf)", "available": render.pdf_available()},
        ],
        "accept": sorted(security.ALLOWED_EXTENSIONS),
        # The page relabels a finished result client-side, so it needs the
        # whole tag set up front rather than a round trip per switch.
        "languages": [
            {"key": "en", "label": "English"},
            {"key": "ar", "label": "العربية"},
        ],
        "tag_sets": {kind: labels.glossary(kind) for kind in labels.SETS},
        "limits": {
            "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
            "max_text_chars": settings.max_text_chars,
            "max_units": settings.max_units,
            "result_ttl_minutes": settings.result_ttl_s // 60,
        },
        "virus_scanner": bool(settings.clamd_host),
        # Stripe Payment Links are public URLs, safe to hand to the browser.
        "donate": integrations.donation_options(),
        # A boolean, deliberately: the API key itself is never serialised.
        "feedback": integrations.feedback_available(),
        "contact_email": integrations.CONTACT_EMAIL,
        "site_domain": integrations.SITE_DOMAIN,
    }


@app.post("/api/feedback")
async def feedback(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    message: str = Form(""),
    website: str = Form(""),
):
    """Relay a visitor message to the institute mailbox.

    `website` is a honeypot: the field is hidden from people, so anything
    that fills it in is a bot. We answer 200 rather than an error so the
    bot records a success and does not come back to probe for the real
    validation rules.
    """
    if website.strip():
        return {"ok": True}

    client = request.client.host if request.client else "unknown"
    if not integrations.rate_ok(client):
        raise HTTPException(429, "Too many messages — please try again later.")
    if not name.strip():
        raise HTTPException(400, "Please tell us your name.")

    try:
        await run_in_threadpool(integrations.send_feedback, name, email, message)
    except FeedbackError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/process")
async def run(
    task: str = Form(...),
    text: str | None = Form(None),
    column: str | None = Form(None),
    lang: str = Form(labels.DEFAULT_LANGUAGE),
    file: UploadFile | None = None,
):
    if task not in process.TASKS:
        raise HTTPException(400, "Unknown task.")
    lang = labels.normalize(lang)

    scan_status = "not applicable"
    if file is not None and file.filename:
        name = security.sanitize_filename(file.filename)
        ext = security.check_extension(name)
        data = await security.read_upload(file)
        # Order matters: scan the raw bytes, confirm the file is what it says
        # it is, and only then let a parser see it.
        scan_status = security.scan(data)
        security.sniff(data, ext)
        document = await run_in_threadpool(extract.extract, data, ext, column)
        source = name
    elif text and text.strip():
        document = extract.from_text(text)
        source = "pasted text"
    else:
        raise HTTPException(400, "Please paste some text or choose a file.")

    try:
        result = await run_in_threadpool(process.process, document, task, lang)
    except UnsafeUpload:
        raise
    except Exception:                        # noqa: BLE001
        log.exception("inference failed for task=%s source=%s", task, source)
        raise HTTPException(500, "The model failed on this input. "
                                 "Please try a smaller or simpler sample.")

    result.scan_status = scan_status
    result.source_name = source
    token = _cache(result)

    shown = result.units[:PREVIEW_UNITS]
    return {
        "token": token,
        "task": result.task,
        "task_label": result.task_label,
        "source": source,
        "scan": scan_status,
        "elapsed_s": round(result.elapsed_s, 2),
        "total_units": len(result.units),
        "shown_units": len(shown),
        "truncated": result.document.truncated,
        "notes": result.notes,
        "is_table": result.document.is_table,
        "columns": (result.document.table.columns if result.document.table else None),
        "lang": result.lang,
        "tag_kind": process.TASKS[task].tag_kind,
        "units": [
            {"n": u.n, "source": u.source, "output": u.output,
             # Codes, not names: the page holds the glossary and renders the
             # names itself, so the language toggle costs no round trip.
             "pairs": [list(p) for p in u.pairs]}
            for u in shown
        ],
    }


@app.get("/api/download/{token}/{fmt}")
async def download(token: str, fmt: str, lang: str | None = None) -> Response:
    if fmt not in render.RENDERERS:
        raise HTTPException(404, "Unknown format.")
    result = _uncache(token)
    if lang is not None:
        result = process.relabel(result, lang)
    try:
        body = await run_in_threadpool(render.render, result, fmt)
    except RuntimeError as exc:              # PDF font missing
        raise HTTPException(503, str(exc)) from exc

    name = render.filename(result, fmt)
    return Response(
        content=body,
        media_type=render.MEDIA_TYPES[fmt],
        headers={
            # `filename*` carries the Arabic correctly; the plain `filename`
            # is the ASCII fallback for older clients.
            "Content-Disposition":
                f'attachment; filename="result.{result.task}.{fmt}"; '
                f"filename*=UTF-8''{_quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
