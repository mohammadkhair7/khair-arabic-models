"""Donations and the feedback mailbox.

Both features mirror what hadith.chat already does, and both are configured
entirely from the process environment.

**Nothing in this module has a secret default, and no secret is ever
returned by an API route, written to a file, or included in a log line.**
The one credential involved - `SENDGRID_API_KEY` - is wrapped in `Secret`,
which renders as `***` in every repr, f-string and traceback, so it cannot
leak through a stray `log.info(settings)` or an unhandled exception.

Donations need no credential at all: Stripe *Payment Links* are public URLs
that Stripe hosts and processes. We never see a card, so there is no secret
key, no webhook signature and no PCI surface in this app.
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("arabicweb.integrations")


class Secret:
    """A string that refuses to render itself.

    Wrapping the API key in this type means the only way to obtain it is to
    call `.reveal()` at the moment of use. Every accidental path - logging
    the settings object, an f-string in an error message, a repr in a
    traceback frame - prints `Secret(set)` instead of the credential.
    """
    __slots__ = ("_value",)

    def __init__(self, value: str | None):
        self._value = value or ""

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(set)" if self._value else "Secret(unset)"

    __str__ = __repr__


def _env(*names: str, default: str = "") -> str:
    """First non-empty of `names`, so we accept hadith.chat's own spellings."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


# --------------------------------------------------------------------------
# donations - Stripe Payment Links (public URLs, not credentials)
# --------------------------------------------------------------------------

# The same two links hadith.chat ships, overridable per environment. The
# VITE_-prefixed spellings are accepted so one .env file can drive both apps.
DONATE_MONTHLY_URL = _env(
    "DONATE_MONTHLY_URL", "VITE_DONATE_MONTHLY_URL",
    default="https://buy.stripe.com/00g3eO7Mce0v3Cg7sy")
DONATE_ONETIME_URL = _env(
    "DONATE_ONETIME_URL", "VITE_DONATE_ONETIME_URL",
    default="https://buy.stripe.com/00g16GeaAbSn3CgcMV")

INSTITUTE_URL = _env("INSTITUTE_URL", default="https://qurancomputing.org")
PROJECT_URL = _env("PROJECT_URL", default="https://hadith.chat")
SITE_DOMAIN = _env("SITE_DOMAIN", default="alarabia.chat")


def donation_options() -> list[dict]:
    """The donation tiers the page offers, in display order."""
    # The amounts are in the labels because the Stripe Payment Links behind
    # them are fixed-price, and tajweed.chat words them the same way.
    return [
        {"key": "monthly", "url": DONATE_MONTHLY_URL,
         "label": "Monthly donation $5", "label_ar": "التبرع الشهري $5",
         "note": "A recurring gift that keeps the service free for everyone.",
         "note_ar": "تبرّع متكرر يبقي الخدمة مجانية للجميع."},
        {"key": "onetime", "url": DONATE_ONETIME_URL,
         "label": "One-time donation $10", "label_ar": "للتبرع $10",
         "note": "A single gift, on Stripe's own secure page.",
         "note_ar": "مساهمة واحدة عبر صفحة سترايب الآمنة."},
    ]


# --------------------------------------------------------------------------
# feedback - SendGrid v3, one POST, no SDK
# --------------------------------------------------------------------------

SENDGRID_API_KEY = Secret(os.environ.get("SENDGRID_API_KEY"))
SENDGRID_FROM_EMAIL = _env("SENDGRID_FROM_EMAIL", default="info@hadith.chat")
CONTACT_EMAIL = _env("CONTACT_EMAIL", default="") or SENDGRID_FROM_EMAIL
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def feedback_available() -> bool:
    """Whether the form can actually deliver. Drives the UI, safely: this is
    a boolean, so the config endpoint never has to touch the key itself."""
    return bool(SENDGRID_API_KEY) and bool(CONTACT_EMAIL)


class FeedbackError(Exception):
    """Delivery failed. The message is safe to show the sender."""


# ip -> [timestamps]. Same shape and budget as hadith.chat's contact form.
_RATE: dict[str, list[float]] = {}
RATE_MAX = 5
RATE_WINDOW = 3600.0


def rate_ok(ip: str) -> bool:
    now = time.time()
    times = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(times) >= RATE_MAX:
        _RATE[ip] = times
        return False
    times.append(now)
    _RATE[ip] = times
    return True


def _clean_header(value: str) -> str:
    """Strip CR/LF so a sender cannot inject extra headers via their name.

    SendGrid takes JSON rather than raw SMTP, so this is belt-and-braces -
    but a name is user input that ends up in a Subject line, and header
    injection is exactly the bug that costs you an open relay.
    """
    return re.sub(r"[\r\n]+", " ", value).strip()


def send_feedback(name: str, email: str, message: str) -> None:
    """Relay one visitor message to the institute mailbox.

    The visitor's address becomes `reply_to` rather than `from`, because
    sending *as* an unverified third party is what gets a domain's mail
    reputation destroyed.
    """
    if not feedback_available():
        raise FeedbackError(
            "Feedback email is not configured on this server "
            "(SENDGRID_API_KEY / SENDGRID_FROM_EMAIL).")

    name = _clean_header(name)[:120]
    email = _clean_header(email)[:200]
    if not _EMAIL.match(email):
        raise FeedbackError("That email address does not look valid.")
    if len(message.strip()) < 5:
        raise FeedbackError("Please write a slightly longer message.")

    payload = {
        "personalizations": [{"to": [{"email": CONTACT_EMAIL}]}],
        "from": {"email": SENDGRID_FROM_EMAIL, "name": "AlArabia.chat"},
        "subject": f"[{SITE_DOMAIN} feedback] {name}"[:200],
        "content": [{"type": "text/plain",
                     "value": f"From: {name} <{email}>\n\n{message}"[:20000]}],
        "reply_to": {"email": email},
    }

    import httpx
    try:
        response = httpx.post(
            SENDGRID_URL, json=payload, timeout=30,
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY.reveal()}"})
    except httpx.HTTPError as exc:
        # Log the class, never the request - the Authorization header is in it.
        log.error("sendgrid transport failure: %s", exc.__class__.__name__)
        raise FeedbackError("Could not reach the mail service. "
                            "Please try again shortly.") from exc

    if response.status_code not in (200, 202):
        log.error("sendgrid rejected the message: HTTP %s", response.status_code)
        raise FeedbackError(
            f"The mail service refused the message (HTTP {response.status_code}).")
