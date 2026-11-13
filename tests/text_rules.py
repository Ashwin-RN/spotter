"""The text rules of stage 6, as check functions and word lists. Imported by T1, T2 and T3.

Every check takes a string and returns a list of Finding. An empty list means clean.
One Finding names its rule, the exact text that broke it, and a short window of context.

Surfaces: where a text is read decides which rules hold it.
  PAGE      visible text of a page or a template
  TITLE     a page title (the one place U+00B7 is allowed)
  SUMMARY   a service's summary or note
  MESSAGE   a service's message or fix (text for Claude)
  CLAUDE    other text for Claude: the server instructions, schema descriptions
  DOC       README.md and docs/
  SOURCE    a string constant in app/ whose surface is not known

check(text, surface) runs every rule RULES[surface] names. Each rule also has its own function.

Readers of source:
  template_text(source)      the human-read text of a Jinja template, through Jinja's own parser
  template_format_lines(source) the lines of a template that format or round a number itself
  html_text(markup)          the human-read text of rendered HTML, one piece per block
  string_constants(source)   the string constants of a Python file, docstrings skipped
  format_spec_lines(source)  the lines of a Python file that format a number with a format spec
  doc_blocks(text)           a Markdown doc as paragraphs, list items, headings, rows and code lines
  source_surface(path)       CLAUDE for the two MCP files, else SOURCE

Exemptions are data: EXEMPT_LINES (a path and the line's text, exempt from every rule),
EXEMPT_CHARACTERS (a path and a character), and KEPT from tests/text_rules_kept.py when that file
exists (a line that keeps its format spec, and only that). exempt(path, line) and
exempt_format(path, line) ask. mask_entered(text, entered) hides text the person entered, for T2.

Banned characters are written as escapes in this file, so a sweep of tests/ stays simple.
"""

from __future__ import annotations

import ast
import functools
import re
import string
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
KEPT_PATH = Path(__file__).resolve().parent / "text_rules_kept.py"

# What stands for an expression in a reassembled text: a Jinja {{ }} or an f-string's {}.
# It trips no rule: no banned character, no word, no digit.
HOLE = "{}"
# What stands for text the person entered, after mask_entered. It trips no rule either.
ENTERED = "entered"
# Where one branch of a Jinja if ends and the next begins, in a reassembled template: a piece ends there.
BREAK = "\x1e"

# ---------------------------------------------------------------- findings


class Finding(NamedTuple):
    rule: str      # one of the rule names below
    match: str     # the exact text that broke the rule
    context: str   # the text around it, cut to a short window

    def __str__(self) -> str:
        return f"{self.rule}: {self.match!r} in {self.context!r}"


class TextPiece(NamedTuple):
    text: str   # whitespace collapsed, stripped
    kind: str   # "text", "title", "attr" (a title, aria-label, alt or placeholder) or "literal"
    line: int   # the line it starts on, in the template or the markup (approximate across Jinja tags)
    tag: str    # the element it sits in: the nearest block element ("td", "dd", "option", "h2", "button"),
                # the element carrying an "attr" piece, "" for a "literal"


# Rule names. A caller filters findings by these, and by match.
DASH = "dash"                    # U+2014, U+2013 and their kin, U+2212, or " - " between words
ARROW = "arrow"                  # an arrow character, or "->", "=>", "<-"
ELLIPSIS = "ellipsis"            # U+2026 or three dots
CURLY_QUOTE = "curly_quote"      # U+2018, U+2019, U+201C, U+201D and their kin
EMOJI = "emoji"
EXCLAMATION = "exclamation"      # "!", except "!="
TILDE = "tilde"                  # "~" before a number, standing for "about"
CHARACTER = "character"          # any other non-ASCII character off the surface's allowlist
FILLER = "filler"
CHAT_PHRASE = "chat_phrase"
AI_VOCABULARY = "ai_vocabulary"
SHAPE = "sentence_shape"         # negative parallelism; "serves as", "stands as", "acts as"
CONTRACTION = "contraction"      # a negative contraction: can't, don't, won't
SEMICOLON = "semicolon"
AT_AMPERSAND = "at_or_ampersand"  # "@" or "&" standing for a word
BRACKETS = "brackets"            # words in round or square brackets
ID = "id"                        # an entry's id: "lift session 12", "#12"
ISO_DATE = "iso_date"
PLUS_ZERO = "plus_zero"
TRAILING_ZERO = "trailing_zero"  # a decimal that ends in 0: "80.0", "7.50"
THOUSANDS = "thousands"          # four or more digits with no comma
CODE_WORD = "code_word"          # snake_case, or a function name from mcp_server.NEEDS
SENTENCE_LENGTH = "sentence_length"
EMPTY_FIGURE = "empty_figure"    # "none", "0" or a dash standing for a figure
FORMAT_SPEC = "format_spec"      # used by T1 for format_spec_lines findings

CONTEXT = 30  # characters of context each side of a match


_SPACE = re.compile(r"[ \t\n\r\f\v]+")


def squash(text: str) -> str:
    """ASCII whitespace runs as one space, stripped. A no-break space (U+00A0) is kept: it is a
    character the page rules read, and str.split() would swallow it."""
    return _SPACE.sub(" ", text).strip(" ")


def _context(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - CONTEXT), min(len(text), end + CONTEXT)
    return squash(text[lo:hi])


def _findings(rule: str, pattern: re.Pattern, text: str) -> list[Finding]:
    return [Finding(rule, m.group(0), _context(text, m.start(), m.end())) for m in pattern.finditer(text)]


# ---------------------------------------------------------------- characters

TIMES = "\u00d7"     # between a count and a thing: "3 \u00d7 5 at 60 kg", "Roti \u00d7 2"
MIDDOT = "\u00b7"    # in page titles: "Progress \u00b7 Spotter"
NBSP = "\u00a0"      # before a unit, on a page only (formatting.show(page=True))

PAGE = "page"
TITLE = "title"
SUMMARY = "summary"
MESSAGE = "message"
CLAUDE = "claude"
DOC = "doc"
SOURCE = "source"
SURFACES = (PAGE, TITLE, SUMMARY, MESSAGE, CLAUDE, DOC, SOURCE)

# The non-ASCII characters each surface allows. Everything else non-ASCII is a finding.
# A service label keeps a plain space, so a summary or a message allows no NBSP.
# SOURCE is a string constant whose surface is unknown: the union.
ALLOWED = {
    PAGE: frozenset({TIMES, NBSP}),
    TITLE: frozenset({TIMES, MIDDOT, NBSP}),
    SUMMARY: frozenset({TIMES}),
    MESSAGE: frozenset({TIMES}),
    CLAUDE: frozenset({TIMES}),
    DOC: frozenset({TIMES}),
    SOURCE: frozenset({TIMES, MIDDOT, NBSP}),
}

_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2e3a\u2e3b\ufe58\ufe63\uff0d")
_QUOTES = frozenset("\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u2032\u2033\u00ab\u00bb\u2039\u203a")
_ELLIPSES = frozenset("\u2026\u22ef")


def _is_arrow(c: str) -> bool:
    n = ord(c)
    return 0x2190 <= n <= 0x21FF or 0x27F0 <= n <= 0x27FF or 0x2900 <= n <= 0x297F or 0x2B00 <= n <= 0x2BFF


def _is_emoji(c: str) -> bool:
    n = ord(c)
    return (0x1F000 <= n <= 0x1FAFF or 0x2600 <= n <= 0x27BF or n in (0xFE0F, 0x200D, 0x20E3)
            or 0xE0020 <= n <= 0xE007F)


def _char_rule(c: str) -> str:
    if c in _DASHES:
        return DASH
    if c in _QUOTES:
        return CURLY_QUOTE
    if c in _ELLIPSES:
        return ELLIPSIS
    if _is_arrow(c):
        return ARROW
    if _is_emoji(c):
        return EMOJI
    return CHARACTER


# ASCII forms of the banned marks.
# A dash between words: spaces or no-break spaces each side, after a word, a bracket, a quote or a full
# stop, before a word, a bracket or a quote.
_GAP = "[ " + NBSP + "]+"
_SPACED_HYPHEN = re.compile(r"(?<=[\w)}%\"'.])" + _GAP + "-{1,2}" + _GAP + r"(?=[\w({\"'])|(?<=\w)--(?=\w)")
_ASCII_ARROW = re.compile(r"-+>|=>|<-+(?![\d.])")
_THREE_DOTS = re.compile(r"\.\.\.|\.(?: \.){2}")
_BANG = re.compile(r"(?<![?<])!(?!=)")  # "(?!" and "(?<!" in a regular expression are not marks
_TILDE_ABOUT = re.compile(r"~\s?[\d.]")


def check_characters(text: str, surface: str, allow: Iterable[str] = ()) -> list[Finding]:
    """Every character outside the surface's allowlist, and the ASCII forms of the banned marks:
    a dash between words, an arrow, three dots, an exclamation mark, a tilde for "about".
    allow adds characters for this one call (EXEMPT_CHARACTERS of a file)."""
    allowed = ALLOWED[surface] | frozenset(allow)
    out = []
    for i, c in enumerate(text):
        if ord(c) > 127 and c not in allowed:
            out.append(Finding(_char_rule(c), c, _context(text, i, i + 1)))
    out += _findings(DASH, _SPACED_HYPHEN, text)
    out += _findings(ARROW, _ASCII_ARROW, text)
    out += _findings(ELLIPSIS, _THREE_DOTS, text)
    out += _findings(EXCLAMATION, _BANG, text)
    out += _findings(TILDE, _TILDE_ABOUT, text)
    return out


# ---------------------------------------------------------------- word lists
# Whole words and phrases, case-blind. Every inflection is listed: nothing matches a prefix.

WORDS_FILLER = (
    "please", "simply", "just", "easy", "easily", "quickly", "really", "actually", "basically",
    "note that",
)

WORDS_CHAT = (
    "certainly", "of course", "great", "i hope this helps", "let me know", "would you like",
    "feel free", "in summary", "in conclusion", "overall",
)

WORDS_AI = (
    # Signs of AI writing (as extracted). "underscore" and "underscores" are left out: they name the
    # character, which the app forgives in kind and zone names. Only the verb forms are listed.
    "delve", "delves", "delved", "delving",
    "tapestry", "tapestries",
    "testament",
    "underscored", "underscoring",
    "pivotal",
    "crucial", "crucially",
    "showcase", "showcases", "showcased", "showcasing",
    "foster", "fosters", "fostered", "fostering",
    "vibrant",
    "robust",
    "meticulous", "meticulously",
    "intricate",
    "landscape",
    "enhance", "enhances", "enhanced", "enhancing", "enhancement",
    "boast", "boasts", "boasted", "boasting",
    "garner", "garners", "garnered",
    "interplay",
    "enduring",
    "bolster", "bolsters", "bolstered",
    "align with", "aligns with", "aligned with", "aligning with",
    "highlight", "highlights", "highlighted", "highlighting",
    "valuable",
    "additionally",
    # The GOV.UK words to avoid that fit an app. "key" as an adjective is left out: a word match
    # cannot tell it from the noun, and the app says "key" as a noun (an API key, the dedupe key).
    "leverage", "leverages", "leveraged", "leveraging",
    "utilise", "utilises", "utilised", "utilising", "utilize", "utilizes", "utilized", "utilizing",
    "empower", "empowers", "empowered", "empowering",
    "facilitate", "facilitates", "facilitated", "facilitating",
    "streamline", "streamlines", "streamlined", "streamlining",
    "in order to",
    "going forward",
    "deliver", "delivers", "delivered", "delivering",
    "transform", "transforms", "transformed", "transforming",
)


def _word_pattern(words: Iterable[str]) -> re.Pattern:
    alternatives = sorted({w.strip() for w in words}, key=len, reverse=True)
    body = "|".join(r"\s+".join(re.escape(part) for part in w.split()) for w in alternatives)
    # A word directly followed by = and a quote is an attribute name in markup (transform="rotate(90)"), not prose.
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])(?!=[\"'])", re.IGNORECASE)


# Each rule name and its list.
WORD_LISTS = {FILLER: WORDS_FILLER, CHAT_PHRASE: WORDS_CHAT, AI_VOCABULARY: WORDS_AI}

_FILLER = _word_pattern(WORDS_FILLER)
_CHAT = _word_pattern(WORDS_CHAT)
_AI = _word_pattern(WORDS_AI)


def check_filler(text: str) -> list[Finding]:
    return _findings(FILLER, _FILLER, text)


def check_chat_phrases(text: str) -> list[Finding]:
    return _findings(CHAT_PHRASE, _CHAT, text)


def check_ai_vocabulary(text: str) -> list[Finding]:
    return _findings(AI_VOCABULARY, _AI, text)


def check_words(text: str) -> list[Finding]:
    """The three word lists: filler, chat phrases, AI vocabulary."""
    return check_filler(text) + check_chat_phrases(text) + check_ai_vocabulary(text)


# ---------------------------------------------------------------- sentence shapes
# Only shapes precise enough to state as a rule. A plain contrast ("a setting, not a goal") is not
# one of them: D19 keeps a contrast that removes a real ambiguity, and a slogan goes by eye.

SHAPES = (
    r"\bnot\s+only\b[^.?!:]*?\bbut\b",                                    # not only X but Y
    r"\bnot\s+just\b",                                                    # not just
    r"\bit(?:'s|\s+is)\s+not\b[^.?!]*?[,;]\s*(?:but\s+)?it(?:'s|\s+is)\b",  # it's not X, it's Y
    r"\b(?:serves|stands|acts)\s+as\b",                                   # where "is" works
)
_SHAPES = re.compile("|".join(f"(?:{s})" for s in SHAPES), re.IGNORECASE)
_CONTRACTION = re.compile(r"\b[a-z]+n['\u2019]t\b", re.IGNORECASE)


def check_shapes(text: str) -> list[Finding]:
    return _findings(SHAPE, _SHAPES, text)


def check_contractions(text: str) -> list[Finding]:
    """A negative contraction ("can't", "don't"). No surface runs it: the contract has no such rule.
    A caller can still ask for it by name, with check(text, surface, rules=["contractions"])."""
    return _findings(CONTRACTION, _CONTRACTION, text)


# ---------------------------------------------------------------- punctuation

_SEMICOLON = re.compile(";")
# "&" touching a word on one side counts too ("Lifts &cardio"), but never an HTML entity ("&amp;").
_NOT_ENTITY = r"(?![A-Za-z]+;|#\d+;)"
_AT_AMP = re.compile(r"(?<!\S)[@&](?!\S)|@(?=\s*\d)|(?<=[A-Za-z])&" + _NOT_ENTITY + r"(?=[A-Za-z])"
                     r"|(?<!\S)&" + _NOT_ENTITY + r"(?=[A-Za-z])|(?<=[A-Za-z])&(?!\S)")
_BRACKETS = re.compile(r"\([^()]*[A-Za-z][^()]*\)|\[[^\[\]]*[A-Za-z][^\[\]]*\]")


def check_semicolons(text: str) -> list[Finding]:
    return _findings(SEMICOLON, _SEMICOLON, text)


def check_at_ampersand(text: str) -> list[Finding]:
    """"@" or "&" standing for a word: alone between spaces, "@" before a number, "&" between letters."""
    return _findings(AT_AMPERSAND, _AT_AMP, text)


def check_brackets(text: str) -> list[Finding]:
    """Words inside round or square brackets. "(38%)" holds no word and passes. Mask entered text first."""
    return _findings(BRACKETS, _BRACKETS, text)


# ---------------------------------------------------------------- numbers and ids

_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_PLUS_ZERO = re.compile(r"(?<![\w.])\+0(?:\.0*)?(?![\d.:])")
_TRAILING_ZERO = re.compile(r"(?<!\d)(?<!\d\.)\d+\.\d*0(?!\d|\.\d)")
_LONG_NUMBER = re.compile(r"(?<!\d)(?<!\d[.,])\d{4,}(?!\d)")
_MONTHS = ("Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|June|"
           "July|August|September|October|November|December")
_BEFORE_YEAR = re.compile(rf"\b(?:{_MONTHS})\s+$")
# An entry word, then a whole number that is not a figure: no decimal, no unit after it.
_UNITS = r"(?:kg|kcal|g|cm|bpm|km|km/h|min|minutes|h|ml|steps|reps|sets|%)"
_ID = re.compile(r"\b(?:lift\s+session|cardio\s+session|session|food\s+entry|cardio\s+entry|entry|weigh-in|"
                 r"measurement|goal|saved\s+food|id)\s*#?\s*\d+(?!\d|[.,:]\d)(?!\s*" + _UNITS + r"(?![\w/]))"
                 r"|(?<![\w&])#\d+\b", re.IGNORECASE)


def check_iso_dates(text: str) -> list[Finding]:
    return _findings(ISO_DATE, _ISO_DATE, text)


def check_plus_zero(text: str) -> list[Finding]:
    """"+0" or "+0.0": a zero carries no sign."""
    return _findings(PLUS_ZERO, _PLUS_ZERO, text)


def check_trailing_zero(text: str, widened: bool = False) -> list[Finding]:
    """A decimal that ends in 0: "80.0", "7.50", "29:30.0". widened=True allows them all, for a text
    that carries a widened figure (80.10, 29:30.0)."""
    return [] if widened else _findings(TRAILING_ZERO, _TRAILING_ZERO, text)


def check_thousands(text: str) -> list[Finding]:
    """A whole part of four or more digits with no comma. A year after a month name passes: "Tue 10 Nov
    2025", "Sunday 8 November 2025", "3 to 9 Nov 2025"."""
    out = []
    for m in _LONG_NUMBER.finditer(text):
        if len(m.group(0)) == 4 and _BEFORE_YEAR.search(text, 0, m.start()):
            continue
        out.append(Finding(THOUSANDS, m.group(0), _context(text, m.start(), m.end())))
    return out


def check_ids(text: str) -> list[Finding]:
    """An entry's id in text a person reads: an entry word followed by a number ("lift session 12",
    "Food entry 3:"), or "#12". Mask entered text first."""
    return _findings(ID, _ID, text)


# ---------------------------------------------------------------- page rules

_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")


@functools.lru_cache(maxsize=1)
def function_names() -> tuple[str, ...]:
    """The MCP function names, from app.mcp_server.NEEDS, imported on first use."""
    from app.mcp_server import NEEDS
    return tuple(sorted(NEEDS))


@functools.lru_cache(maxsize=1)
def _function_pattern() -> re.Pattern | None:
    names = [n for n in function_names() if "_" not in n]  # the rest are snake_case already
    return _word_pattern(names) if names else None


def check_code_words(text: str) -> list[Finding]:
    """snake_case, and any MCP function name ("whoami"), on a page."""
    out = _findings(CODE_WORD, _SNAKE, text)
    pattern = _function_pattern()
    return out + (_findings(CODE_WORD, pattern, text) if pattern else [])


SENTENCE_WORDS = 25
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_WORD = re.compile(r"\S*[A-Za-z0-9]\S*")


def check_sentence_length(text: str, limit: int = SENTENCE_WORDS) -> list[Finding]:
    """A sentence of more than limit words. Call it once per piece of a page: a piece never runs across
    a block element. A word is a token with a letter or a digit in it."""
    out = []
    for sentence in _SENTENCE_END.split(text):
        count = len(_WORD.findall(sentence))
        if count > limit:
            out.append(Finding(SENTENCE_LENGTH, sentence.strip(), f"{count} words"))
    return out


# A piece that is only one of these stands for a figure Spotter could not work out.
EMPTY_MARKS = frozenset({"none", "none yet", "0", "-", "--", "\u2013", "\u2014", "n/a", "na", "null", "nil"})


def check_empty_figure(text: str) -> list[Finding]:
    """A whole piece that is "none", "none yet", "0" or a dash. Call it once per piece of a page (a
    table cell, a dd): "None logged in the last 7 days" is words and passes."""
    piece = squash(text)
    return [Finding(EMPTY_FIGURE, piece, piece)] if piece.casefold() in EMPTY_MARKS else []


# ---------------------------------------------------------------- surfaces


# What each surface is checked for, from the contract's T1, T2 and T3 rows.
RULES = {
    PAGE: ("characters", "words", "shapes", "semicolons", "at_ampersand", "iso_dates",
           "code_words", "sentence_length", "empty_figure"),
    TITLE: ("characters", "words", "shapes", "semicolons", "at_ampersand", "iso_dates",
            "code_words"),
    SUMMARY: ("characters", "words", "shapes", "semicolons", "at_ampersand", "brackets", "ids", "iso_dates",
              "plus_zero", "trailing_zero", "thousands"),
    MESSAGE: ("characters", "words"),
    CLAUDE: ("characters", "words", "shapes"),
    DOC: ("characters", "words", "shapes"),
    SOURCE: ("characters", "words", "shapes"),
}

_CHECKS = {
    "words": check_words,
    "shapes": check_shapes,
    "contractions": check_contractions,
    "semicolons": check_semicolons,
    "at_ampersand": check_at_ampersand,
    "brackets": check_brackets,
    "ids": check_ids,
    "iso_dates": check_iso_dates,
    "plus_zero": check_plus_zero,
    "thousands": check_thousands,
    "code_words": check_code_words,
    "sentence_length": check_sentence_length,
    "empty_figure": check_empty_figure,
}
RULE_NAMES = ("characters", "trailing_zero") + tuple(_CHECKS)

# What T1 holds each source to (the contract's T1 row). Templates are pages: semicolons too.
T1_TEMPLATE_RULES = ("characters", "words", "shapes", "semicolons")
T1_SOURCE_RULES = ("characters", "words", "shapes")


def check(text: str, surface: str, *, widened: bool = False, allow: Iterable[str] = (),
          rules: Iterable[str] | None = None) -> list[Finding]:
    """Every rule RULES[surface] names, on one text. rules names a subset (or other rules) instead, from
    the names in RULES; the character allowlist is still the surface's. widened switches off the
    trailing-zero rule. allow adds characters to the allowlist for this call. On a page, call it once
    per piece."""
    if surface not in RULES:
        raise ValueError(f"unknown surface {surface!r}: one of {', '.join(SURFACES)}")
    names = RULES[surface] if rules is None else tuple(rules)
    unknown = [n for n in names if n not in RULE_NAMES]
    if unknown:
        raise ValueError(f"unknown rule {unknown[0]!r}: one of {', '.join(RULE_NAMES)}")
    out = []
    for name in names:
        if name == "characters":
            out += check_characters(text, surface, allow)
        elif name == "trailing_zero":
            out += check_trailing_zero(text, widened)
        else:
            out += _CHECKS[name](text)
    return out


# ---------------------------------------------------------------- HTML text

INLINE = frozenset({"a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark",
                    "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr"})
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                  "source", "track", "wbr"})
READ_ATTRIBUTES = ("title", "aria-label", "alt", "placeholder", "label")
# A meta tag whose content a person reads: a description and the link-preview tags.
META_TEXT = frozenset({"description", "og:title", "og:description", "og:site_name", "og:image:alt",
                       "twitter:title", "twitter:description", "twitter:image:alt"})


def _hidden_roll(attrs: dict) -> bool:
    return attrs.get("aria-hidden") == "true" and "roll" in (attrs.get("class") or "").split()


class _Walker(HTMLParser):
    def __init__(self, line_offset: int = 0):
        super().__init__(convert_charrefs=True)
        self.line_offset = line_offset
        self.pieces: list[TextPiece] = []
        self.stack: list[str] = []
        self.skip_at: int | None = None  # stack depth of an aria-hidden roll element, or of script/style
        self.buffer: list[str] = []
        self.start_line = 0

    def _line(self) -> int:
        return self.getpos()[0] + self.line_offset

    def _block(self) -> str:
        return next((t for t in reversed(self.stack) if t not in INLINE), "")

    def _kind(self) -> str:
        return "title" if "title" in self.stack and "svg" not in self.stack else "text"

    def flush(self):
        text = squash("".join(self.buffer))
        self.buffer = []
        if text and text.replace(HOLE, "").strip():
            self.pieces.append(TextPiece(text, self._kind(), self.start_line, self._block()))

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v or "") for k, v in attrs}
        if tag not in INLINE or tag == "br":
            self.flush()
        skipping = tag not in VOID and self.skip_at is None and (_hidden_roll(attrs) or tag in ("script", "style"))
        if self.skip_at is None and not skipping:
            values = [attrs.get(name, "") for name in READ_ATTRIBUTES]
            if tag == "input" and attrs.get("type") in ("submit", "button", "reset"):
                values.append(attrs.get("value", ""))
            if tag == "meta" and (attrs.get("name") in META_TEXT or attrs.get("property") in META_TEXT):
                values.append(attrs.get("content", ""))
            for value in values:
                value = squash(value.replace(BREAK, " "))
                if value.replace(HOLE, "").strip():
                    self.pieces.append(TextPiece(value, "attr", self._line(), tag))
        if tag in VOID:
            return
        self.stack.append(tag)
        if skipping:
            self.skip_at = len(self.stack)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in INLINE:
            self.flush()
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:  # close every element left open inside this one
            depth = len(self.stack)
            popped = self.stack.pop()
            if self.skip_at is not None and depth <= self.skip_at:
                self.skip_at = None
            if popped == tag:
                break

    def handle_data(self, data):
        if self.skip_at is not None:
            return
        line = self._line()
        for n, part in enumerate(data.split(BREAK)):
            if n:
                self.flush()
            if not "".join(self.buffer).strip():
                lead = part[:len(part) - len(part.lstrip())]
                self.start_line = line + lead.count("\n")
            self.buffer.append(part)
            line += part.count("\n")

    def close(self):
        super().close()
        self.flush()


def html_text(markup: str, line_offset: int = 0) -> list[TextPiece]:
    """The text a person reads in HTML, one piece per block element (a cell, a dd, a paragraph, a
    button, an option): inline elements (a, strong, span, code and the like) stay inside their piece. Entities
    are decoded. Script and style are left out, and so is every element with aria-hidden="true" and the
    class roll. The title, aria-label, alt, placeholder and label attributes, a submit input's value and
    the content of a meta tag in META_TEXT come back as pieces of kind "attr"; no other attribute is
    read. The document title comes back as kind "title"."""
    walker = _Walker(line_offset)
    walker.feed(markup)
    walker.close()
    return walker.pieces


# ---------------------------------------------------------------- template text


def _literals(node) -> list:
    """The string constants in a Jinja expression that can reach the page: through an inline if, an or,
    a concatenation, a filter's argument, a call's argument, a list, the object of an attribute access
    ({"a": "Great"}.items(), "x".upper()). Never from a test, a comparison or a subscript."""
    from jinja2 import nodes
    if isinstance(node, nodes.Const):
        return [node] if isinstance(node.value, str) else []
    if isinstance(node, nodes.CondExpr):
        return _literals(node.expr1) + (_literals(node.expr2) if node.expr2 is not None else [])
    if isinstance(node, (nodes.Compare, nodes.Test, nodes.Not, nodes.Slice)):
        return []
    if isinstance(node, (nodes.Getitem, nodes.Getattr)):  # the attribute's name is not a node
        return _literals(node.node)
    out = []
    for child in node.iter_child_nodes():
        out += _literals(child)
    return out


class _Stream:
    """A template written out again as markup: its data as written, a HOLE for each expression, and line
    breaks put back where a Jinja tag swallowed them, so html.parser's lines are the template's lines."""

    def __init__(self):
        self.parts: list[str] = []
        self.line = 1
        self.literals: list[TextPiece] = []

    def write(self, text: str, lineno: int):
        if lineno > self.line:
            self.parts.append("\n" * (lineno - self.line))
            self.line = lineno
        self.parts.append(text)
        self.line += text.count("\n")

    def literal(self, nodes_):
        for const in nodes_:
            text = squash(const.value)
            if text:
                self.literals.append(TextPiece(text, "literal", const.lineno, ""))

    def walk(self, node):
        from jinja2 import nodes
        if isinstance(node, (nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport)):
            return
        if isinstance(node, nodes.Output):
            for child in node.nodes:
                if isinstance(child, nodes.TemplateData):
                    self.write(child.data, child.lineno)
                else:
                    self.write(HOLE, child.lineno)
                    self.literal(_literals(child))
            return
        if isinstance(node, nodes.Block) and node.name == "title":
            self.write("<title>", node.lineno)
            for child in node.body:
                self.walk(child)
            self.write("</title>", self.line)
            return
        if isinstance(node, nodes.For):
            self.literal(_literals(node.iter))
            for child in node.body + node.else_:
                self.walk(child)
            return
        if isinstance(node, nodes.If):  # each branch is its own text: "kcal" then "none", never "kcalnone"
            for child in node.body:
                self.walk(child)
            for elif_ in node.elif_:
                self.write(BREAK, elif_.lineno)
                self.walk(elif_)
            if node.else_:
                self.write(BREAK, self.line)
            for child in node.else_:
                self.walk(child)
            return
        if isinstance(node, nodes.Assign):
            self.literal(_literals(node.node))
            return
        if isinstance(node, nodes.With):
            for value in node.values:
                self.literal(_literals(value))
        if isinstance(node, nodes.Macro):
            for default in node.defaults:
                self.literal(_literals(default))
        if isinstance(node, (nodes.CallBlock, nodes.FilterBlock)):
            self.literal(_literals(node.call if isinstance(node, nodes.CallBlock) else node.filter))
        for child in node.iter_child_nodes():
            if isinstance(child, nodes.Stmt) or isinstance(child, nodes.Template):
                self.walk(child)


def template_text(source: str) -> list[TextPiece]:
    """The text a person reads in a Jinja template, read through Jinja's own parser. The template's data
    is written out again with a HOLE for each expression, then read by html_text: tags removed, entities
    decoded, an aria-hidden roll element left out. A block named title comes back as kind "title".
    String constants inside expressions that can reach the page ({{ "Replace" if x else "Save" }}, a
    macro's label argument or default, the list a for loop walks, a with or set value) come back as
    pieces of kind "literal"."""
    from jinja2 import Environment
    tree = Environment().parse(source)
    stream = _Stream()
    stream.walk(tree)
    return html_text("".join(stream.parts)) + stream.literals


# The Jinja filters that format or round a number. The web gets formatting.show and its kin instead.
TEMPLATE_NUMBER_FILTERS = ("format", "round")


def template_format_lines(source: str) -> list[tuple[int, str]]:
    """Each line of a Jinja template that formats a number itself: the format or round filter, or a
    .format() call in an expression. Comments are never read. Returns (line number, the line's text
    stripped), one per line, in order."""
    from jinja2 import Environment, nodes
    tree = Environment().parse(source)
    lines = source.splitlines()
    hits = {f.lineno for f in tree.find_all(nodes.Filter) if f.name in TEMPLATE_NUMBER_FILTERS}
    hits |= {c.lineno for c in tree.find_all(nodes.Call)
             if isinstance(c.node, nodes.Getattr) and c.node.attr == "format"}
    return [(n, lines[n - 1].strip()) for n in sorted(hits) if 0 < n <= len(lines)]


# ---------------------------------------------------------------- Python source


class StringConstant(NamedTuple):
    line: int      # the line it starts on
    end_line: int  # the line it ends on
    text: str      # an f-string comes back whole, with a HOLE for each expression


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def string_constants(source: str) -> list[StringConstant]:
    """Every string constant in a Python file, in order. Docstrings are skipped, and so are comments (the
    syntax tree has none) and the format spec inside an f-string. An f-string is one constant, with a
    HOLE for each expression; a string inside one of its expressions comes back on its own."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    out: list[StringConstant] = []

    def visit(node):
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else HOLE for v in node.values)
            out.append(StringConstant(node.lineno, node.end_lineno, text))
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    visit(v.value)  # not v.format_spec: ".2f" is not text
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip:
                out.append(StringConstant(node.lineno, node.end_lineno, node.value))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return out


# The format spec mini-language: [[fill]align][sign][z][#][0][width][grouping][.precision][type]
_SPEC = re.compile(r"^(?:.?[<>=^])?(?P<sign>[-+ ])?z?#?(?P<zero>0)?(?P<width>\d+)?(?P<group>[,_])?"
                   r"(?:\.(?P<prec>\d+))?(?P<type>[bcdeEfFgGnosxX%])?$", re.DOTALL)
_PERCENT = re.compile(r"%(?:\([^)]*\))?(?P<flags>[-+ 0#]*)(?P<width>\d+|\*)?(?:\.(?P<prec>\d+|\*))?"
                      r"(?P<type>[diouxXeEfFgGcrsa%])")


def numeric_spec(spec: str) -> bool:
    """Whether a format spec formats a number: ":g", ":,", ":+g", ":.1f", ":02d" and their kin, a zero pad
    with no type (":02"), and a precision with no type (":.2", which rounds a float). A nested field
    ("{x:{spec}}") cannot be told apart, so it counts. Plain width and alignment (":>10", ":5") do not."""
    if "{" in spec:
        return True
    m = _SPEC.match(spec)
    if not m or not spec:
        return False
    kind = m.group("type")
    if kind in tuple("eEfFgGn%") or m.group("group") or m.group("sign"):
        return True
    if kind is None and (m.group("zero") or m.group("prec")):
        return True
    return kind in tuple("bdoxX") and bool(m.group("zero") or m.group("width"))


def _percent_numeric(template: str) -> bool:
    for m in _PERCENT.finditer(template):
        kind = m.group("type")
        if kind in "eEfFgG":
            return True
        if kind in "diouxX" and (m.group("flags") or m.group("width") or m.group("prec")):
            return True
    return False


def _format_numeric(template: str) -> bool:
    """Whether a str.format template has a numeric spec. A nested field ("{:.{n}f}") counts."""
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError:
        return False
    return any(numeric_spec(spec) for _, _, spec, _ in fields if spec)


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """The module-level names bound once to a string constant: a template held for later use."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            out[target.id] = value.value
    return out


def format_spec_lines(source: str) -> list[tuple[int, str]]:
    """Each line of a Python file that formats a number with a format spec: an f-string's spec, a
    str.format template, format(x, spec), or a % template. A template held in a module-level string
    constant is read where it is used. A spec or template that is not a constant cannot be told apart,
    so it counts: format(x, spec), str.format(template, x), an f-string spec with a nested field.
    Comments and docstrings are never read. Returns (line number, the line's text stripped), one per
    line, in order."""
    tree = ast.parse(source)
    lines = source.splitlines()
    held = _module_strings(tree)
    hits: set[int] = set()

    def text_of(node) -> str | None:
        """A string constant's value, or a held template's; None when it is not known."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in held:
            return held[node.id]
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            spec = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.format_spec.values)
            if numeric_spec(spec):
                hits.add(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format":
                if isinstance(func.value, ast.Name) and func.value.id == "str":  # str.format(template, x)
                    template = text_of(node.args[0]) if node.args else None
                    if template is None or _format_numeric(template):
                        hits.add(node.lineno)
                else:
                    template = text_of(func.value)
                    if template is not None and _format_numeric(template):
                        hits.add(node.lineno)
            elif isinstance(func, ast.Name) and func.id == "format" and len(node.args) >= 2:
                spec = text_of(node.args[1])
                if spec is None or numeric_spec(spec):
                    hits.add(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            template = text_of(node.left)
            if template is not None and _percent_numeric(template):
                hits.add(node.lineno)
    return [(n, lines[n - 1].strip()) for n in sorted(hits)]


# ---------------------------------------------------------------- exemptions


class Exemption(NamedTuple):
    path: str    # posix, from the repository root: "app/services/common.py"
    line: str    # the line's text, stripped, whitespace runs as one space
    reason: str


# The T1 exemptions the contract names. Found by text, not line number, so they survive edits.
EXEMPT_LINES = (
    Exemption("app/links.py", "text = name.casefold().replace(\"'\", \"\").replace(\"\u2019\", \"\")",
              "A curly apostrophe stripped from a lift name in a link. No one reads it."),
    Exemption("app/rules.py", "shown = shown if len(shown) <= QUOTE_MAX else shown[:QUOTE_MAX - 1] + \"\u2026\"",
              "The cut mark of rules.quote. T2 treats quote's output as text the person entered."),
    Exemption("app/auth.py", "return text if len(text) <= LOG_VALUE_LIMIT else text[:LOG_VALUE_LIMIT] + \"...(cut)\"",
              "Three ASCII dots in a log line. No one reads it on a page or in a result."),
    Exemption("app/services/common.py", "return f\"{value + 0.0:.2f}\"  # + 0.0 turns -0.0 into 0.0",
              "The dedupe key: a format spec that never reaches a person."),
)

# A character allowed in one file's string constants: the byte-order mark export.py writes (D16).
# By character, not by line: the constant does not exist yet, and lane A names it.
EXEMPT_CHARACTERS = (
    ("app/services/export.py", "\ufeff"),
)


def norm_path(path) -> str:
    """A path as the exemptions write it: posix, from the repository root."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(ROOT)
        except ValueError:
            pass
    return p.as_posix()


def norm_line(line: str) -> str:
    """A line's text as the exemptions compare it: stripped, every whitespace run as one space."""
    return squash(line)


def load_kept(path=KEPT_PATH) -> tuple[tuple[str, str], ...]:
    """KEPT from tests/text_rules_kept.py, the message and fix lines lane A keeps: a list of
    (path, the line's text) pairs. Read as a literal, never imported. No file gives ()."""
    path = Path(path)
    if not path.exists():
        return ()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        if any(isinstance(t, ast.Name) and t.id == "KEPT" for t in targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, (list, tuple)) or not all(
                    isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, str) for x in v)
                    for v in value):
                raise ValueError(f"{path}: KEPT must be a list of (path, line text) pairs")
            return tuple((norm_path(p), norm_line(t)) for p, t in value)
    raise ValueError(f"{path}: no KEPT list")


def exempt(path, line: str, kept_path=KEPT_PATH) -> bool:
    """Whether a line is exempt from every T1 rule: named in EXEMPT_LINES. A KEPT line is not: it keeps
    its format spec only (exempt_format). kept_path is read by exempt_format, and kept here so a
    caller's arguments stay the same."""
    return (norm_path(path), norm_line(line)) in {(e.path, norm_line(e.line)) for e in EXEMPT_LINES}


def exempt_format(path, line: str, kept_path=KEPT_PATH) -> bool:
    """Whether a line may keep its numeric format spec: named in EXEMPT_LINES, or in KEPT."""
    return exempt(path, line) or (norm_path(path), norm_line(line)) in set(load_kept(kept_path))


def exempt_characters(path) -> frozenset[str]:
    """The characters a file's string constants may carry beyond the allowlist."""
    p = norm_path(path)
    return frozenset(c for q, c in EXEMPT_CHARACTERS if q == p)


# The files whose string constants are text for Claude: the server instructions, the function and
# field descriptions. T1 holds them to the claude surface, not the generic source one.
CLAUDE_SOURCES = ("app/mcp_server.py", "app/mcp_schemas.py")


def source_surface(path) -> str:
    """The surface T1 checks a Python file's string constants as: CLAUDE for CLAUDE_SOURCES, else SOURCE."""
    return CLAUDE if norm_path(path) in CLAUDE_SOURCES else SOURCE


# ---------------------------------------------------------------- docs

_BLOCK_START = re.compile(r"^\s*(?:#|\||```|~~~|[-*+]\s|\d+[.)]\s|>)")


class DocBlock(NamedTuple):
    line: int   # the line the block starts on
    text: str   # its lines as written, joined by newlines


def doc_blocks(text: str) -> list[DocBlock]:
    """A Markdown doc as the blocks a reader reads: a paragraph, a list item with its wrapped lines, one
    heading, one table row, one line of a code block. A blank line ends a block. A check reads a block
    with its newlines as spaces, so a phrase wrapped across two lines is found."""
    blocks: list[DocBlock] = []
    current: list[str] = []
    start = 0
    fenced = False

    def end():
        nonlocal current
        if current:
            blocks.append(DocBlock(start, "\n".join(current)))
        current = []

    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        fence = stripped.startswith(("```", "~~~"))
        if not stripped:
            end()
        elif fenced or fence or stripped.startswith(("#", "|")):
            end()
            blocks.append(DocBlock(n, line))
        else:
            if _BLOCK_START.match(line):
                end()
            if not current:
                start = n
            current.append(line)
        if fence:
            fenced = not fenced
    end()
    return blocks


# ---------------------------------------------------------------- text the person entered


def mask_entered(text: str, entered: Iterable[str]) -> str:
    """text with every value the person entered put out of reach of the checks: each value as given and
    rules.quote of it, cut mark included. Each becomes
    ENTERED. Longest first; blank values are skipped; a value is matched as a whole, never inside a
    longer word, number or date: beside a digit, ".", ",", ":" and "-" end nothing, so ["80"] leaves
    "80.5 kg" alone and ["11"] leaves "2026-11-10" alone. A value with no letter or digit in it is
    refused with ValueError: it would mask punctuation. entered: descriptions, notes, names, anything
    the person or Claude sent."""
    from app.rules import quote
    variants = set()
    for value in entered:
        if value is None or not str(value).strip():
            continue
        value = str(value)
        if not any(c.isalnum() for c in value):
            raise ValueError(f"an entered value needs a letter or a digit to be masked: {value!r}")
        variants |= {value, quote(value)}
    for variant in sorted(variants, key=len, reverse=True):
        before = r"(?<!\w)(?<!\d[.,:-])" if re.match(r"\w", variant) else ""
        after = r"(?!\w)(?![.,:-]\d)" if re.search(r"\w$", variant) else ""
        text = re.sub(before + re.escape(variant) + after, ENTERED, text)
    return text
