"""Rule-based isnād parser: locates the sanad/matn boundary in a hadith unit
and splits the transmission chain into (verb, name-mention) hops.

This is the *labeler* behind the structure model. It is deliberately
high-precision rather than high-recall: it fires confidently on
well-behaved units and reports a low confidence elsewhere. Only its most
confident output (confidence ≥ 0.9) becomes training data, and the neural
model then generalizes the same boundary convention to text the rules parse
poorly. See `docs/PAPER.md` §4.3.

    >>> p = parse_isnad("حدثنا عبد الله بن يوسف قال أخبرنا مالك عن نافع "
    ...                 "عن ابن عمر أن رسول الله صلى الله عليه وسلم قال ...")
    >>> p.sanad_end_raw, p.confidence
"""
import re
from dataclasses import dataclass, field

TRANSMISSION_VERBS = [
    "حدثنا", "حدثني", "اخبرنا", "اخبرني", "انبانا", "انبأنا", "سمعت", "سمع",
    "قال", "عن", "ان",
]
# includes the copyists' abbreviations: ثنا/نا = حدثنا, انا/ابنا/انبا = اخبرنا
_VERB_RX = re.compile(
    r"\b(حدثنا|حدثني|اخبرنا|اخبرني|انبانا|ثنا|نا|انا|ابنا|انبا|سمعت|سمع|عن)\b")
# strong matn openers: explicit Prophet-speech markers
_MATN_START = re.compile(
    r"((قال|يقول|سمعت)\s+(رسول\s+الله|النبي)|ان\s+(رسول\s+الله|النبي)\s|"
    r"عن\s+النبي\s*صلي|يقول\s*:|قال\s*:\s*\")")
# generic speech opener — ends the chain when no strong marker exists
# (e.g. «عن صهيب صاحب رسول الله ﷺ قال : مررت...»)
_SPEECH = re.compile(r"\b(قال|قالت)\s*:")
# honorifics/descriptors that belong to the narrator's TITLE, not the name;
# stripped from mention tails so they never hide or bloat a narrator
_HONORIFIC_TAIL = re.compile(
    r"(?:\s*(?:صاحب\s+(?:رسول\s+الله|النبي)|صلي\s+الله\s+عليه\s+وسلم|"
    r"رضي\s+الله\s+عن(?:ه|ها|هم|هما)|رحمه\s+الله|رحمها\s+الله))+\s*$")
_CONTINUES_CHAIN = re.compile(
    r"^\s*(حدثنا|حدثني|اخبرنا|اخبرني|انبانا|ثنا|نا|انا|ابنا|انبا|سمعت|عن)\b")
# hard stops inside a name span: a narrator's name never contains sentence
# punctuation — «عن الزهري ، فذكر حديثا ، ثم» must yield «الزهري», not the
# whole span up to the next transmission verb. «:» is deliberately NOT a stop:
# «أبو محمد : عبد الله بن يوسف» is a kunya/name apposition, one narrator.
_MENTION_STOP = re.compile(r"[،,;؛.!؟?()\[\]{}«»\"']")
# residue before the name, e.g. «) فلان» left over from a تحويل (ح) marker
_MENTION_LEAD = re.compile(r"^[\s،,;؛:.!؟?()\[\]{}«»\"']+")


@dataclass
class Hop:
    verb: str
    mention: str                          # the name, normalized (for matching)
    pos: int                              # 0 = collector side
    # The same name as a span of the ORIGINAL text. `mention` is folded for
    # matching — عَائِشَة becomes عاءشه — which is right for an index and wrong
    # for a reader, so anything that displays a narrator should slice
    # `text[start:end]` and show the spelling the source actually used. The
    # verb has its own span for the same reason: it is stored here as اخبرني
    # but the page must show the أخبرني the author wrote.
    start: int = -1
    end: int = -1
    verb_start: int = -1
    verb_end: int = -1


@dataclass
class IsnadParse:
    sanad_end: int                        # char offset where the matn starts (normalized text)
    hops: list[Hop] = field(default_factory=list)
    confidence: float = 0.0
    sanad_end_raw: int = -1               # same boundary as an offset into the RAW text
    flags: list[str] = field(default_factory=list)   # validation warnings


_DIACRITICS_ONE = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u0640]")
_ALEF_ONE = re.compile(r"[\u0622\u0623\u0625\u0671]")
_HAMZA_ONE = re.compile(r"[\u0624\u0626]")


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """normalize_arabic with an index map: map[i] = raw index of norm char i.

    Mirrors `arabicmodels.normalize.normalize_arabic` exactly, character by
    character, so a boundary found in normalized space can be reported as an
    offset into the untouched source text.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = True                     # collapses leading whitespace too
    for i, c in enumerate(text):
        if _DIACRITICS_ONE.match(c):
            continue
        if c.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        prev_space = False
        if _ALEF_ONE.match(c):
            c = "\u0627"
        elif c == "\u0629":
            c = "\u0647"
        elif c == "\u0649":
            c = "\u064A"
        elif _HAMZA_ONE.match(c):
            c = "\u0621"
        out.append(c)
        idx.append(i)
    # strip trailing space to match .strip()
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _speech_boundary(norm: str) -> int | None:
    """First «قال/قالت :» that actually starts speech (not a nested chain
    like «قال : حدثنا فلان») — the chain-termination fallback."""
    for sm in _SPEECH.finditer(norm):
        if sm.start() < 8:
            continue                       # speech at the very start is a heading, not a matn
        if _CONTINUES_CHAIN.match(norm[sm.end():]):
            continue
        return sm.start()
    return None


_STRIPPABLE = " ،:."
_LEADING_QALA = re.compile(r"^(قال|قالا|قالوا)\s+")
# Trailing words that open what comes next rather than close the name. «قال» is
# the speech verb; «ان» is the complementizer of «أن رسول الله ﷺ ...», and it
# lands on the last narrator whenever a span ends one word into the matn.
_TRAILING_FILLER = re.compile(r"\s+(قال|قالا|قالوا|ان)$")


def _mention_span(sanad: str, start: int, end: int) -> tuple[int, int]:
    """Narrow the text between two transmission verbs down to the name itself.

    Identical in effect to trimming the substring, but it returns the surviving
    span instead of the surviving string, so the name can be mapped back to the
    raw text afterwards. Every step below is a prefix trim or a suffix trim —
    that is what makes the offsets recoverable at all.
    """
    lead = _MENTION_LEAD.match(sanad, start, end)
    if lead:
        start = lead.end()
    stop = _MENTION_STOP.search(sanad, start, end)
    if stop:
        end = stop.start()
    start, end = _trim(sanad, start, end)

    qala = _LEADING_QALA.match(sanad, start, end)
    if qala:
        start = qala.end()
    filler = _TRAILING_FILLER.search(sanad[start:end])
    if filler:
        end = start + filler.start()
    tail = _HONORIFIC_TAIL.search(sanad[start:end])
    if tail:
        end = start + tail.start()
    return _trim(sanad, start, end)


def _trim(s: str, start: int, end: int) -> tuple[int, int]:
    """str.strip(_STRIPPABLE) expressed as offsets."""
    while start < end and s[start] in _STRIPPABLE:
        start += 1
    while end > start and s[end - 1] in _STRIPPABLE:
        end -= 1
    return start, end


def _raw_span(start: int, end: int, idx_map: list[int], text: str) -> tuple[int, int]:
    """Carry a normalized span back to the source text.

    The end needs the care: `idx_map[end]` would be the next surviving
    character, which reaches past the space between two words. Taking the last
    character of the span and then re-absorbing the diacritics that follow it
    keeps تَيْمِيَّةَ whole while stopping before the space after it.
    """
    if start >= end or end > len(idx_map):
        return -1, -1
    raw_start = idx_map[start]
    raw_end = idx_map[end - 1] + 1
    while raw_end < len(text) and _DIACRITICS_ONE.match(text[raw_end]):
        raw_end += 1
    return raw_start, raw_end


def _hops(sanad: str, idx_map: list[int], text: str) -> tuple[list[Hop], list[str]]:
    """Split a normalized chain into (verb, name) hops.

    `sanad` is a prefix of the normalized `text`, so offsets into one index the
    other and `idx_map` maps both back to the source.
    """
    hops: list[Hop] = []
    flags: list[str] = []
    matches = list(_VERB_RX.finditer(sanad))
    for i, vm in enumerate(matches):
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(sanad)
        start, end = _mention_span(sanad, vm.end(), nxt)
        mention = sanad[start:end]
        # pos = verb index (not dense): if a mention is ever rejected, the
        # position gap prevents joining its neighbours into a false direct pair
        if 2 <= len(mention) <= 60:
            raw_start, raw_end = _raw_span(start, end, idx_map, text)
            vs, ve = _raw_span(vm.start(), vm.end(), idx_map, text)
            hops.append(Hop(verb=vm.group(1), mention=mention, pos=i,
                            start=raw_start, end=raw_end,
                            verb_start=vs, verb_end=ve))
        elif mention:
            flags.append("dropped_mention")     # completeness warning — never silent
    return hops, flags


def narrator_hops(chain: str) -> list[Hop]:
    """Split a span that is ALREADY known to be an isnād into its narrators.

    The difference from `parse_isnad` is what the caller is claiming. Given a
    whole hadith, the rules must find the chain before they can split it, and
    they may find it in a different place than the structure model did — which
    on a page that draws both is a visible contradiction: a narrator
    highlighted outside the isnād the same page just shaded. So when the model
    has already marked the span, it is handed over intact and only the split
    is asked for. Offsets on the returned hops are relative to `chain`.
    """
    norm, idx_map = _normalize_with_map(chain)
    return _hops(norm, idx_map, chain)[0]


def parse_isnad(text: str) -> IsnadParse:
    """Rule-based isnād parse of a hadith unit.

    The boundary is the EARLIEST valid marker: a «قال/قالت :» speech opener
    that is not a nested chain can precede a strong Prophet marker («عن أبيه
    قال : لم أتخلف عن النبي ﷺ...» — the matn starts at قال, and the later
    عن النبي is part of the matn). Marker strength affects confidence, never
    position. Mentions never disappear silently: rejections are recorded in
    `flags`.
    """
    norm, idx_map = _normalize_with_map(text)
    flags: list[str] = []

    m = _MATN_START.search(norm)
    sp = _speech_boundary(norm)
    if m is not None and (sp is None or m.start() <= sp):
        sanad_end = m.start()
    elif sp is not None:
        sanad_end = sp
        if m is None:
            flags.append("speech_boundary")     # weaker marker than a strong opener
    else:
        sanad_end = min(len(norm), 300)
        flags.append("no_matn_marker")
    sanad_end_raw = idx_map[sanad_end] if sanad_end < len(idx_map) else -1
    if "no_matn_marker" in flags:
        sanad_end_raw = -1                      # a length cutoff is not a real boundary
    sanad = norm[:sanad_end]

    hops, hop_flags = _hops(sanad, idx_map, text)
    flags += hop_flags

    first_verb = _VERB_RX.search(sanad)
    conf = 0.0
    if hops:
        conf = 0.5
        if first_verb and first_verb.start() < 15:
            conf += 0.2                    # unit starts with a transmission verb
        if m:
            conf += 0.3                    # explicit matn opener found
        elif "speech_boundary" in flags:
            conf += 0.2                    # boundary from a generic speech opener
        if "dropped_mention" in flags:
            conf -= 0.2
    return IsnadParse(sanad_end=sanad_end, hops=hops,
                      confidence=round(max(conf, 0.0), 2),
                      sanad_end_raw=sanad_end_raw, flags=flags)
