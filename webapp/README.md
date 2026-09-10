# alarabia.chat — inference web app

A free, no-account web front end for the four `khair-arabic-models`
checkpoints. Paste Arabic text or upload a document, pick a task, and take the
result away as text, CSV, Markdown or PDF.

```bash
pip install -e .                        # the models, from the repo root
pip install -r webapp/requirements.txt  # the web layer
python -m webapp                        # http://127.0.0.1:8000
```

The first request loads a model and takes a few seconds; every later one is
milliseconds. Pass `--preload` to pay that cost at startup instead.

---

## What it does

| Task | What you get |
| --- | --- |
| **Diacritize** | Every diacritic restored from the bare letters (grammar-aware v0.2). |
| **Fill missing diacritics** | Existing marks kept verbatim; only fully bare words are vowelled, and Qurʾānic citations are never touched. |
| **Part-of-speech tags** | Each word labelled with one of 24 tags. |
| **Hadith structure** | Text split into hadith number, isnād, matn and heading, with each isnād broken into its narrators. |

Under every isnād the page prints the chain of narrators in transmission
order, the verb that carries the report (`حدثنا`, `أخبرني`, `عن`) set apart
from the name that receives it. The chain is drawn from the isnād the model
marked, not from a second reading of the text, so the two never disagree on
screen; the names appear in the source's own spelling, diacritics and all.
They travel into all four downloads, and into the CSV as their own columns,
one row per narrator.

Tag names are shown in **Arabic by default, or English**, switchable on the
result itself.
The models emit codes (`noun_prop`, `MATN`); the language only chooses the
dictionary those codes are read through, so switching redraws the page without
running anything again, and `?lang=ar` re-renders a download the same way.
Exports keep a `tag_code` column so the file stays machine-readable either way.

**Input** — pasted text, or `.txt`, `.csv`, `.xlsx`, `.doc`, `.docx`.
For spreadsheets the app finds the Arabic column by itself (scoring each
column by how much Arabic script it holds) and you can override it by name or
number.

**Output** — `.txt` (just the text), `.csv` (your original columns plus a
results column, BOM-prefixed so Excel reads Arabic correctly), `.md`, or a
typeset right-to-left `.pdf`.

---

## Handling hostile uploads

The whole exposure of this app is that it opens documents sent by strangers,
and the parsers it hands them to — `zipfile`, `openpyxl`, `python-docx`,
`olefile` — are exactly where malicious files do their work. The defences are
layered in the order an attacker meets them, in
[`app/security.py`](app/security.py):

| Threat | Defence |
| --- | --- |
| Executable or script upload | Extension **allowlist** (never a denylist), applied to our own sanitised name rather than the browser's. |
| Renamed payload (`virus.exe` → `notes.txt`) | **Magic-byte sniffing** — the content must match the claimed extension. |
| Known malware | Optional **ClamAV** scan of the raw bytes, before any parser runs. Set `ARABICWEB_CLAMAV_REQUIRED=1` to fail closed. |
| Decompression bomb | `.docx`/`.xlsx` are zips: member count, total inflated size and per-member ratio are all capped. |
| Zip path traversal | Member names containing `..`, a leading `/` or a drive letter are refused. |
| Macro / embedded objects | `vbaProject.bin`, OLE object and ActiveX parts are refused outright. |
| XXE and billion-laughs | Any `<!DOCTYPE>` or `<!ENTITY>` in an OOXML part is refused before a parser sees it. |
| `.doc` macros | The OLE macro storages are detected and refused; only data streams are ever read. |
| Memory exhaustion | Upload size capped **while streaming**, plus caps on units, unit length and total processing time. |
| CSV injection | Cells starting `=`, `+`, `-` or `@` are quoted so Excel will not execute them. |
| Header injection via the feedback form | CR/LF stripped from the name and address. |
| Abuse | Per-IP rate limits: 30 requests/minute for processing, 5/hour for feedback, plus a honeypot field. |
| XSS | Strict CSP (`default-src 'none'`), no inline script or style, no third-party origins; the UI only ever writes `textContent`. |

The CSP is why the light/dark theme lives in its own `static/theme.js` loaded
render-blocking from `<head>` rather than in an inline script: it has to run
before the first paint or the page flashes light and then turns dark, and
inline script is not allowed. **Dark is the default** — a first visit is dark
whatever the operating system is set to — and the toggle records the reader's
own choice in `localStorage["theme"]`, the same key hadith.chat uses, so one
decision serves both sites.

Two structural choices matter more than any single check:

* **Nothing is written to disk.** Uploads are read into memory, parsed from a
  `BytesIO`, and dropped. There is no file for a later process to open and
  nothing to clean up when a request fails.
* **Nothing is executed.** The app reads document data. It does not launch
  Office, evaluate a formula, resolve an external reference or follow a link.

Run it in the container for the outer layer — non-root, read-only filesystem,
all capabilities dropped, `no-new-privileges`, a PID cap and a memory cap:

```bash
docker compose -f webapp/docker-compose.yml up --build
```

That compose file also brings up a ClamAV sidecar and points the app at it.

---

## Configuration

Copy `.env.example` to `.env` and edit. **`.env` is git-ignored and must never
be committed**; `.env.example` is the only tracked file and holds variable
names with empty values.

### Secrets

`SENDGRID_API_KEY` is the only credential this app uses. It is read from the
environment, wrapped in a `Secret` type that renders as `***` in every repr
and traceback, and never appears in an API response — `/api/config` returns a
boolean saying whether feedback is configured, not the key. In production,
inject it as a platform or container environment variable rather than using a
`.env` file at all.

Donations need **no** credential: they are Stripe *Payment Links*, public URLs
that Stripe hosts. This app never sees a card number, so there is no secret
key, no webhook signature and no PCI surface here.

A pre-commit hook (`git config core.hooksPath .githooks`) runs
`scripts/check_secrets.py` over staged changes and refuses anything that looks
like a credential.

### Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `SENDGRID_API_KEY` | *(unset)* | **SECRET.** Scope it to "Mail Send" only. Feedback form is disabled without it. |
| `SENDGRID_FROM_EMAIL` | `info@hadith.chat` | Must be a verified sender. |
| `CONTACT_EMAIL` | `info@hadith.chat` | Where feedback lands. |
| `DONATE_MONTHLY_URL` | hadith.chat's link | Stripe Payment Link, public. |
| `DONATE_ONETIME_URL` | hadith.chat's link | Stripe Payment Link, public. |
| `SITE_DOMAIN` | `alarabia.chat` | Used in the feedback subject line. |
| `ARABICWEB_MAX_UPLOAD_MB` | `8` | Enforced while streaming. |
| `ARABICWEB_MAX_UNITS` | `2000` | Lines/paragraphs/rows per request. |
| `ARABICWEB_PROCESS_TIMEOUT_S` | `180` | Partial results are returned with a note. |
| `ARABICWEB_CLAMD_HOST` / `_PORT` | *(unset)* / `3310` | Enables virus scanning. |
| `ARABICWEB_CLAMAV_REQUIRED` | `0` | `1` = refuse uploads if the scanner is down. |
| `ARABICWEB_DEVICE` | auto | `cuda` or `cpu`. |
| `ARABICWEB_PRELOAD` | `0` | Load all models at startup. |
| `ARABICWEB_PDF_FONT` | auto-detected | Path to an Arabic `.ttf`. |
| `ARABICWEB_RESULT_TTL_S` | `600` | How long a result stays downloadable. |

The `VITE_DONATE_*_URL` spellings hadith.chat uses are also accepted, so one
`.env` can drive both apps.

---

## Deployment

The public alarabia.chat runs on an OVH host shared with quran.chat,
hadith.chat and tajweed.chat, using the same pull-based deploy framework: a
systemd timer polls `main`, a read-only GitHub deploy key fetches it, secrets
come from OVH Secret Manager just-in-time, and a shared Caddy terminates TLS
and routes the hostname to the container. Nothing is published on a host port.

Everything needed is in [`deploy/`](../deploy): the production compose file,
the on-host orchestrator, and the scripts that onboard a machine, load the
secrets, and verify a deploy. The image is this directory's `Dockerfile`, so
what runs in production is what `docker compose -f webapp/docker-compose.yml
up` gives you locally.

Two things are worth knowing before touching it. The model weights are Git LFS
objects, and a checkout without `git-lfs` leaves pointer files that build,
start and pass a health check before failing on the first real request — the
deploy fetches `models/**` explicitly and refuses to build if a checkpoint is
still pointer-sized. And the app keeps no state at all, which is what makes
moving it between servers a redeploy rather than a data migration.

Full configuration inventory, DNS, and the runbook for moving to another
server: [`docs/ALARABIA_CHAT_OVH_MIGRATION_PLAN.md`](../docs/ALARABIA_CHAT_OVH_MIGRATION_PLAN.md).

---

## Notes on the PDF export

A PDF has no text engine — it places glyphs at coordinates — so Arabic has to
be shaped into its joined forms and reordered right-to-left before it is
written. With `uharfbuzz` installed, fpdf2 does this properly, including
line-breaking. Without it we fall back to `arabic-reshaper` + `python-bidi`
and break lines ourselves *before* reordering, because reordering first and
wrapping second makes a paragraph read back to front.

PDF export needs an Arabic TrueType font. The app searches the usual system
locations; if it finds none, the PDF button is disabled and the other three
formats still work. The Docker image installs `fonts-noto-core`.

---

## Tests

```bash
python -m pytest webapp/tests -q          # 80+ tests, in-process
python -m webapp --port 8011 &            # or drive a live server
python webapp/tests/live_check.py
```

`test_security.py` covers every rejection path above; `test_pipeline.py`
covers each input type, task and output format; `test_render.py` covers the
right-to-left line-breaking. Model-dependent tests skip cleanly if the
checkpoints have not been fetched with `git lfs pull`.

---

## Credits

Developed by **Mohammad Mohammad Khair, MS, EMBA** —
[International Computing Institute for Quran and Islamic Sciences](https://QuranComputing.org)

المهندس محمد محمد خير — المعهد العالمي لحوسبة القرآن والعلوم الإسلامية

Part of the [Hadith.chat](https://Hadith.chat) project. Open source under the
Apache License 2.0 —
[github.com/qurancomp/khair-arabic-models](https://github.com/qurancomp/khair-arabic-models).
