"""T5, the stylesheet part: app/static/style.css, read with a small parser from the standard library.

What it holds (stage 6, section L): the colour tokens and nothing coloured outside them; contrast worked
out from the token values against an allowed-pairs table, with the two banned pairs; value cells that
never break and line up; a minimum tap size on each control; no reordering; every icon url() naming a
file that exists, and the icon files clean; the one breakpoint and the row-table stacking below 720 px;
motion only as section L's Motion section lists it.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app import charts, web

from .test_web import client, seam, web_client  # noqa: F401 - fixtures

STATIC = Path(web.HERE) / "static"
CSS_PATH = STATIC / "style.css"
ICONS = STATIC / "icons"

NO_PREFERENCE = "@media (prefers-reduced-motion: no-preference)"
REDUCE = "@media (prefers-reduced-motion: reduce)"
LAPTOP = "@media (min-width: 960px)"
STACKED = "@media (max-width: 719.98px)"


# ---------------------------------------------------------------- the parser

@dataclass
class Rule:
    selector: str
    context: tuple[str, ...]          # the at-rules around it, outermost first
    decls: list[tuple[str, str, bool]] = field(default_factory=list)  # (property, value, important)

    @property
    def selectors(self) -> list[str]:
        return [s.strip() for s in _split_top(self.selector, ",")]

    def get(self, prop: str) -> str | None:
        found = [v for p, v, _ in self.decls if p == prop]
        return found[-1] if found else None


def _split_top(text: str, sep: str) -> list[str]:
    """Split on sep outside brackets and quotes."""
    out, depth, quote, start = [], 0, None, 0
    for n, ch in enumerate(text):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:n])
            start = n + 1
    out.append(text[start:])
    return out


def strip_comments(text: str) -> str:
    out, n, quote = [], 0, None
    while n < len(text):
        ch = text[n]
        if quote:
            out.append(ch)
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif text.startswith("/*", n):
            end = text.find("*/", n + 2)
            assert end != -1, "an unclosed comment"
            n = end + 2
            continue
        else:
            out.append(ch)
        n += 1
    return "".join(out)


class Sheet:
    """Every style rule with its at-rule context. Raises on unbalanced braces. Counts each block and
    each declaration it read, so a check can never pass on rules the parser dropped."""

    def __init__(self, text: str):
        self.text = strip_comments(text)
        self.rules: list[Rule] = []
        self.blocks = 0
        self.declarations = 0
        end = self._block(0, (), None)
        assert end == len(self.text), "text after the last block"

    def _block(self, n: int, context: tuple[str, ...], rule: Rule | None) -> int:
        text = self.text
        buf, quote, depth = [], None, 0
        while n < len(text):
            ch = text[n]
            if quote:
                buf.append(ch)
                quote = None if ch == quote else quote
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch == "(":
                depth += 1
                buf.append(ch)
            elif ch == ")":
                depth -= 1
                buf.append(ch)
            elif ch == ";" and depth == 0:
                self._declaration("".join(buf), context, rule)
                buf = []
            elif ch == "{":
                self.blocks += 1
                prelude = " ".join("".join(buf).split())
                buf = []
                if prelude.startswith("@") and not prelude.startswith("@view-transition"):
                    n = self._block(n + 1, context + (prelude,), None)
                else:
                    inner = Rule(prelude, context)
                    self.rules.append(inner)
                    n = self._block(n + 1, context, inner)
                continue
            elif ch == "}":
                assert not "".join(buf).strip(), f"a declaration with no semicolon: {''.join(buf)!r}"
                assert rule is not None or context, "a closing brace with nothing open"
                return n + 1
            else:
                buf.append(ch)
            n += 1
        assert not "".join(buf).strip() and rule is None and not context, "an unclosed block"
        return n

    def _declaration(self, text: str, context, rule: Rule | None) -> None:
        text = text.strip()
        if not text:
            return
        assert rule is not None, f"a declaration outside a rule: {text!r}"
        prop, _, value = text.partition(":")
        assert value, f"not a declaration: {text!r}"
        value = " ".join(value.split())
        important = value.endswith("!important")
        value = value.removesuffix("!important").strip()
        rule.decls.append((prop.strip().lower(), value, important))
        self.declarations += 1

    def find(self, selector: str, context: tuple[str, ...] = ()) -> list[Rule]:
        """The rules whose selector list holds this exact selector, in exactly this context."""
        return [r for r in self.rules if selector in r.selectors and r.context == context]

    def value(self, selector: str, prop: str, context: tuple[str, ...] = ()) -> str:
        values = [r.get(prop) for r in self.find(selector, context) if r.get(prop) is not None]
        assert values, f"{selector} sets no {prop} in {context or 'the top level'}"
        return values[-1]


@pytest.fixture(scope="module")
def css() -> Sheet:
    return Sheet(CSS_PATH.read_text(encoding="utf-8"))


def test_the_parser_reads_every_block_and_every_declaration(css):
    assert css.blocks == css.text.count("{") and css.declarations == css.text.count(";")
    assert len(css.rules) > 300 and css.declarations > 1000
    contexts = {c for r in css.rules for c in r.context}
    assert {LAPTOP, STACKED, NO_PREFERENCE, REDUCE, "@supports selector(:popover-open)"} <= contexts


@pytest.mark.parametrize("bad", ["a { color: red }", "a { color: red; ", "a { color: red; } }", "/* open", "b"])
def test_the_parser_refuses_what_it_cannot_read(bad):
    with pytest.raises(AssertionError):
        Sheet(bad)


# ---------------------------------------------------------------- tokens and colours

# Section L's colour tokens, in full.
TOKENS = {
    "--bg": "#000000", "--surface": "#161616", "--surface-hover": "#1b1b1b", "--raised": "#232323",
    "--raised-2": "#2c2c2c", "--sheet": "#1c1c1c", "--bar": "#1a1a1a", "--line": "#343434",
    "--line-strong": "#707070", "--text": "#f2f2f2", "--text-bright": "#ffffff", "--text-2": "#a6a6a6",
    "--text-3": "#8c8c8c", "--on-primary": "#000000", "--up": "#4ade80", "--today": "#5aa9ff",
    "--warn": "#f5b841", "--err": "#ff7a7a", "--sample": "#a9b4ff",
}
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOUR_FUNCTION = re.compile(r"\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", re.I)
NAMED = re.compile(r"\b(black|white|red|green|blue|yellow|orange|purple|gr[ae]y|silver|maroon|navy|teal|olive|"
                   r"lime|aqua|fuchsia|pink|brown|gold|indigo|violet|cyan|magenta|beige|tan|coral|crimson|"
                   r"salmon|khaki|ivory|lavender|plum|orchid|turquoise|wheat|canvas|canvastext)\b", re.I)
COLOUR_PROPS = re.compile(r"^(-webkit-)?(color|background.*|border.*|outline.*|fill|stroke|box-shadow|"
                          r"text-decoration.*|caret-color|accent-color|scrollbar-color|column-rule.*|mask.*)$")


def root(css) -> Rule:
    [rule] = css.find(":root")
    return rule


def tokens(css) -> dict[str, str]:
    return {p: v.lower() for p, v, _ in root(css).decls if p.startswith("--") and HEX.fullmatch(v)}


def test_the_colour_tokens_are_section_ls_table(css):
    assert tokens(css) == TOKENS
    assert root(css).get("color-scheme") == "dark"
    elsewhere = [(r.selector, p) for r in css.rules if r.selector != ":root" or r.context
                 for p, v, _ in r.decls if p.startswith("--") and (HEX.search(v) or COLOUR_FUNCTION.search(v))]
    assert not elsewhere


def test_dark_only(css):
    assert "prefers-color-scheme" not in css.text and "light" not in root(css).get("color-scheme")


# The literal colours section L allows: black with an alpha, only in a shadow, the sheet's backdrop
# (62%) and the laptop top bar (82%). transparent and currentColor are words, not colours.
BLACK_ALPHA = re.compile(r"rgb\(0 0 0 / (\.\d+)\)")


def test_colours_only_through_tokens(css):
    seen = set()
    for rule in css.rules:
        if rule.selector == ":root" and not rule.context:
            continue
        for prop, value, _ in rule.decls:
            if prop.startswith("--") and "url(" in value:
                continue
            bare = BLACK_ALPHA.sub("", value)
            for alpha in BLACK_ALPHA.findall(value):
                if prop == "box-shadow":
                    seen.add("shadow")
                elif prop == "background" and rule.selector.endswith("::backdrop") and alpha == ".62":
                    seen.add("backdrop")
                elif prop == "background" and rule.selector == ".top" and rule.context == (LAPTOP,) and alpha == ".82":
                    seen.add("top bar")
                else:
                    pytest.fail(f"black at {alpha} in {rule.selector} {prop}")
            assert not HEX.search(bare), (rule.selector, prop, value)
            assert not COLOUR_FUNCTION.search(bare), (rule.selector, prop, value)
            if COLOUR_PROPS.match(prop):
                words = NAMED.findall(re.sub(r"url\([^)]*\)", "", bare))
                assert not words, (rule.selector, prop, value)
    assert seen == {"shadow", "backdrop", "top bar"}


def test_every_var_names_a_token_that_exists(css):
    defined = {p for r in css.rules for p, _, _ in r.decls if p.startswith("--")}
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css.text))
    assert used <= defined and set(TOKENS) <= used


# ---------------------------------------------------------------- contrast, worked out from the tokens

def luminance(hex_colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(fg: str, bg: str) -> float:
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def over(top: str, alpha: float, under: str) -> str:
    """top drawn at alpha over under, as a hex colour."""
    mixed = [round(int(top[i:i + 2], 16) * alpha + int(under[i:i + 2], 16) * (1 - alpha)) for i in (1, 3, 5)]
    return "#" + "".join(hex(c)[2:].rjust(2, "0") for c in mixed)


TEXT, BORDER = 4.5, 3.0
BACKGROUNDS = ("--bg", "--surface", "--raised", "--sheet", "--raised-2")
# Section L's contrast table: foreground -> its ratio on each background, in BACKGROUNDS order. None is
# a banned pair. "--line" is decorative: a divider, never the only mark of anything.
L_TABLE = {
    "--text": ((18.76, 16.16, 14.04, 15.22, 12.47), TEXT),
    "--text-2": ((8.63, 7.43, 6.46, 7.00, 5.74), TEXT),
    "--text-3": ((6.25, 5.38, 4.67, 5.07, None), TEXT),
    "--up": ((12.05, 10.38, 9.02, 9.78, 8.01), TEXT),
    "--today": ((8.55, 7.37, 6.40, 6.94, 5.69), TEXT),
    "--warn": ((11.82, 10.18, 8.84, 9.59, 7.86), TEXT),
    "--err": ((8.32, 7.17, 6.22, 6.75, 5.53), TEXT),
    "--sample": ((10.66, 9.19, 7.98, 8.65, 7.09), TEXT),
    "--line-strong": ((4.24, 3.65, 3.17, 3.44, None), BORDER),
}
BANNED = {("--text-3", "--raised-2"): 4.15, ("--line-strong", "--raised-2"): 2.82}
OTHER_PAIRS = {("--on-primary", "--text"): (18.76, TEXT), ("--on-primary", "--text-bright"): (21.00, TEXT),
               ("--on-primary", "--err"): (8.32, TEXT), ("--text-2", "--surface-hover"): (7.08, TEXT),
               ("--text-3", "--surface-hover"): (5.12, TEXT)}


def allowed_pairs() -> dict[tuple[str, str], float]:
    """(foreground, background) -> the ratio it needs."""
    out = {}
    for fg, (ratios, need) in L_TABLE.items():
        for bg, figure in zip(BACKGROUNDS, ratios):
            if figure is not None:
                out[(fg, bg)] = need
    for pair, (_, need) in OTHER_PAIRS.items():
        out[pair] = need
    return out


def test_contrast_from_the_token_values_matches_section_ls_table(css):
    t = tokens(css)
    for fg, (ratios, need) in L_TABLE.items():
        for bg, figure in zip(BACKGROUNDS, ratios):
            worked = ratio(t[fg], t[bg])
            if figure is None:
                assert round(worked, 2) == BANNED[(fg, bg)] and worked < need, (fg, bg, worked)
            else:
                assert round(worked, 2) == figure and worked >= need, (fg, bg, worked)
    for (fg, bg), (figure, need) in OTHER_PAIRS.items():
        assert round(ratio(t[fg], t[bg]), 2) == figure >= need
    # The lightest thing that can pass under the phone tab bar: --text, under --bar at 90%.
    assert round(ratio(t["--text-2"], over(t["--bar"], 0.9, t["--text"])), 2) == 5.42
    assert "color-mix(in srgb, var(--bar) 90%, transparent)" in css.value("nav.tabs", "background")


def _token(value: str | None) -> str | None:
    found = re.findall(r"var\((--[a-z0-9-]+)\)", value or "")
    colours = [f for f in found if f in TOKENS]
    return colours[-1] if colours else None


BORDER_PROPS = ("border", "border-top", "border-bottom", "border-left", "border-right", "border-color",
                "outline", "outline-color")


def test_every_pair_one_rule_paints_is_an_allowed_pair(css):
    allowed = allowed_pairs()
    checked = 0
    for rule in css.rules:
        bg = _token(rule.get("background") or rule.get("background-color"))
        if bg is None:
            continue
        fg = _token(rule.get("color"))
        if fg is not None:
            assert allowed.get((fg, bg)) == TEXT, (rule.selector, fg, bg)
            checked += 1
        for prop in BORDER_PROPS:
            line = _token(rule.get(prop))
            if line is None or line in (bg, "--line"):  # the fill's own edge, or a divider
                continue
            assert (line, bg) in allowed, (rule.selector, prop, line, bg)
            checked += 1
    assert checked >= 15


def _raised_2_rules(css) -> list[Rule]:
    return [r for r in css.rules if _token(r.get("background") or r.get("background-color")) == "--raised-2"]


# What sits inside each thing drawn on --raised-2 and is --text-3 elsewhere: each needs its own override.
INSIDE_RAISED_2 = {".card button.tile:not(.is-empty):hover": ((" .tile-note", "color"), ("::before", "background")),
                   ".said.you": ((" .who", "color"),)}


def test_text_3_and_line_strong_never_sit_on_raised_2(css):
    banned = {"--text-3", "--line-strong"}
    surfaces = _raised_2_rules(css)
    assert {s for r in surfaces for s in r.selectors} >= set(INSIDE_RAISED_2)
    for surface in surfaces:
        assert not {_token(v) for _, v, _ in surface.decls} & banned, surface.selector
        for selector in surface.selectors:
            inside = [r for r in css.rules for s in r.selectors
                      if s != selector and s.startswith(selector) and s[len(selector)] in " :"]
            for rule in inside:
                assert not {_token(v) for _, v, _ in rule.decls} & banned, rule.selector
            for suffix, prop in INSIDE_RAISED_2.get(selector, ()):
                override = _token(css.value(selector + suffix, prop))
                assert override not in banned and (override, "--raised-2") in allowed_pairs(), (selector, suffix)
            if selector.startswith((".secondary", ".primary", "button.link")):
                assert ":not(:disabled" in selector  # a disabled button keeps its dashed --line-strong edge


# ---------------------------------------------------------------- numbers, words and sizes

def test_no_word_breaks_mid_word(css):
    assert "break-all" not in css.text and "word-break" not in css.text


def test_value_cells_never_break_and_line_up(css):
    assert css.value("body", "font-variant-numeric") == "tabular-nums"
    for selector in ("th.num", "td.num"):
        assert css.value(selector, "white-space") == "nowrap"
        assert css.value(selector, "font-variant-numeric") == "tabular-nums"
        assert css.value(selector, "text-align") == "right"
    assert css.value(".nowrap", "white-space") == "nowrap"
    for selector in (".tile-value", ".big", ".sheet .giant", "dl.figures dd", "dl.totals dd", ".tag"):
        assert css.value(selector, "white-space") == "nowrap", selector
    assert css.value("table.rows td.num", "white-space", (STACKED,)) == "nowrap"


def test_the_system_font_and_no_font_file(css):
    assert root(css).get("--font") == 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif'
    assert root(css).get("--mono") == 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'
    assert css.value("body", "font") == "400 16px/1.5 var(--font)"
    assert css.value("code", "font-family") == "var(--mono)"
    assert "@font-face" not in css.text and not re.search(r"\.(woff2?|ttf|otf)\b", css.text)


def test_text_sizes_in_px_and_inputs_at_16(css):
    sizes = [(r.selector, v) for r in css.rules for p, v, _ in r.decls if p == "font-size"]
    assert len(sizes) > 80
    assert all(re.fullmatch(r"\d+(\.\d+)?px", v) for _, v in sizes), [s for s in sizes if not s[1].endswith("px")]
    for selector in ('input[type="text"]', "input:not([type])", "select", "textarea"):
        assert css.value(selector, "font-size") == "16px"


def px(value: str | None) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)px", value or "")
    return float(match.group(1)) if match else 0.0


# Each control class, with the height and width it must reach. A link inside a sentence is exempt.
CONTROLS = {
    ".button": (48, 48), "button.primary": (48, 48), "button.secondary": (48, 48), "button.danger": (48, 48),
    "button.link": (44, 44), "a.action": (44, 44), ".tab": (52, 76), "a.pill": (44, 44),
    ".segments a": (44, 44), ".sheet .close": (44, 44), "details.add > summary": (56, None),
    "a.aside": (44, None), ".back": (44, None), ".plain-link": (44, None), ".site-foot a": (44, None),
    "table.rows td a": (44, None), "label.choice": (44, None), ".brand": (44, None), ".tile": (132, None),
    'input[type="text"]': (48, None), "select": (48, None), "dl.facts dt a": (44, None),
}


def _size(css, selector, context, *props) -> float:
    rules = css.find(selector, context)
    return max([px(r.get(p)) for r in rules for p in props] + [0.0])


@pytest.mark.parametrize("selector", sorted(CONTROLS))
def test_each_control_is_at_least_44_px(css, selector):
    height, width = CONTROLS[selector]
    assert _size(css, selector, (), "min-height", "height") >= height >= 44
    if width:
        assert _size(css, selector, (), "min-width", "width") >= width >= 44


def test_a_tab_on_a_laptop_and_a_day_pill_keep_their_size(css):
    assert _size(css, ".tab", (LAPTOP,), "min-height") >= 44 and _size(css, ".tab", (LAPTOP,), "min-width") >= 44
    # The day grid runs 12 px into the card's padding with no gap: 7 columns of 44 px from 360 px up.
    assert css.value("ol.days", "margin-inline") == "-12px" and css.value("ol.days", "column-gap") == "0"
    assert (360 - 2 * 16 - 2 * 20 + 2 * 12) / 7 >= 44


# ---------------------------------------------------------------- order on screen

def test_no_reordering(css):
    props = {p: v for r in css.rules for p, v, _ in r.decls}
    for p, v in ((p, v) for r in css.rules for p, v, _ in r.decls):
        assert p != "order", "order"
        assert not (p == "grid-auto-flow" and "dense" in v), v
        assert "-reverse" not in v, (p, v)
        assert p not in ("grid-area", "grid-template-areas"), p
        if p in ("grid-row", "grid-row-start", "grid-row-end"):
            assert re.fullmatch(r"span \d+|auto", v), (p, v)
    assert "grid-row" in props  # a row span is used, and allowed


# ---------------------------------------------------------------- the icons

ICON_NAMES = {"today", "progress", "goals", "settings", "weight", "food", "training", "maintenance", "run",
              "plus", "close"}


def test_every_icon_url_names_a_file_that_exists(css):
    urls = re.findall(r"url\(\s*['\"]?([^'\")]*)['\"]?\s*\)", css.text)
    assert len(urls) == css.text.count("url(") and urls
    for url in urls:
        assert re.fullmatch(r"/static/icons/[a-z-]+\.svg", url), url
        assert (STATIC / url.removeprefix("/static/")).is_file(), url
    assert {Path(u).stem for u in urls} == ICON_NAMES
    assert not re.search(r"data:|https?:", re.sub(r"\"[^\"]*\"", "", css.text))


SVG = "{http://www.w3.org/2000/svg}"


def test_the_icon_files_are_the_eleven_and_clean():
    assert {p.stem for p in ICONS.iterdir()} == ICON_NAMES and all(p.suffix == ".svg" for p in ICONS.iterdir())
    for path in ICONS.iterdir():
        text = path.read_text(encoding="utf-8")
        root_el = ET.fromstring(text)
        assert root_el.tag == f"{SVG}svg" and root_el.get("viewBox") == "0 0 24 24", path.name
        assert root_el.get("stroke-width") == "2" and root_el.get("stroke-linecap") == "round", path.name
        for el in root_el.iter():
            assert el.tag.removeprefix(SVG) in {"svg", "path", "circle", "rect", "line", "polyline"}, (path.name, el.tag)
            for name, value in el.attrib.items():
                assert "href" not in name and name != "style" and not name.startswith("on"), (path.name, name)
                assert "url(" not in value and "data:" not in value, (path.name, value)
        assert "<script" not in text and "<style" not in text and "http" not in text.replace(SVG[1:-1], "")


@pytest.mark.parametrize("name", sorted(ICON_NAMES))
def test_each_icon_answers_from_static(client, name):
    r = client.get(f"/static/icons/{name}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "public, max-age=3600"


# ---------------------------------------------------------------- one breakpoint, and tables

def test_one_breakpoint_at_960_and_tables_stack_below_720(css):
    widths = set(re.findall(r"@media[^{]*?\((?:min|max)-width:[^)]*\)", css.text))
    assert widths == {"@media (min-width: 960px)", "@media (max-width: 719.98px)"}


def test_row_tables_stack_below_720(css):
    assert css.value("div.table-wrap", "overflow-x") == "auto"
    stacked = (STACKED,)
    assert css.value("table.rows tr", "display", stacked) == "grid"
    assert css.value("table.rows td", "display", stacked) == "block"
    assert css.value("table.rows td.lead", "grid-column", stacked) == "1 / -1"
    assert css.value("table.rows td[data-label]::before", "content", stacked) == "attr(data-label)"
    assert css.value("table.rows td:empty", "display", stacked) == "none"
    assert css.value("table.rows thead", "position", stacked) == "absolute"
    assert not css.find("table.rows td[data-label]::before")  # labels show only when stacked


# ---------------------------------------------------------------- focus

def test_focus_draws_a_ring_and_nothing_removes_it(css):
    assert css.value(":focus-visible", "outline") == "2px solid var(--text)"
    assert css.value(":focus-visible", "outline-offset") == "2px"
    assert css.value("input:focus-visible", "outline-offset") == "0"
    assert css.value("input:focus-visible", "border-color") == "var(--text)"
    for rule in css.rules:
        for prop, value, _ in rule.decls:
            if prop.startswith("outline") and prop != "outline-offset":
                assert value not in ("none", "0", "0px"), rule.selector


# ---------------------------------------------------------------- motion

def _animated(css) -> list[Rule]:
    """Every rule that starts, delays or names an animation, outside the keyframes themselves."""
    return [r for r in css.rules if not (r.context and r.context[-1].startswith("@keyframes"))
            and any(p.startswith("animation") and v != "none" for p, v, _ in r.decls)]


def test_on_load_motion_only_with_no_preference_and_not_calm(css):
    animated = _animated(css)
    assert len(animated) >= 5
    for rule in animated:
        assert rule.context == (NO_PREFERENCE,), rule.selector
        assert all(s.startswith("body:not(.calm) ") for s in rule.selectors), rule.selector
    names = {r.get("animation").split()[0] for r in animated if r.get("animation")}
    keyframes = {c.removeprefix("@keyframes ") for r in css.rules for c in r.context if c.startswith("@keyframes")}
    assert names == keyframes == {"draw", "grow", "fade", "roll"}
    drawn = {s for r in animated for s in r.selectors}
    for selector in ("body:not(.calm) svg.chart .series", "body:not(.calm) svg.chart .bar",
                     "body:not(.calm) svg.chart .area", "body:not(.calm) svg.chart .dot.end-dot"):
        assert selector in drawn, selector
    # The line draws from start to end over pathLength="1"; with no motion it is whole.
    assert css.value("body:not(.calm) svg.chart .series", "stroke-dasharray", (NO_PREFERENCE,)) == "1"
    assert not [r for r in css.rules if r.get("stroke-dashoffset")
                and not (r.context and r.context[0].startswith("@keyframes"))]


def test_the_cross_fade_is_opt_in_and_reduced_motion_turns_everything_off(css):
    fades = [(r.context, r.get("navigation")) for r in css.rules if r.selector == "@view-transition"]
    assert sorted(fades) == [((NO_PREFERENCE,), "auto"), ((REDUCE,), "none")]
    [off] = css.find("*", (REDUCE,))
    assert off.selectors == ["*", "*::before", "*::after"]
    assert ("transition", "none", True) in off.decls and ("animation", "none", True) in off.decls
    [backdrop] = css.find("::backdrop", (REDUCE,))
    assert ("transition", "none", True) in backdrop.decls
    assert {p for r in css.rules if r.context == (REDUCE,) for p, _, _ in r.decls} <= {"transition", "animation",
                                                                                       "navigation"}


TRANSITIONS = {"scale", "translate", "opacity", "overlay", "display"}


def test_only_the_sheet_and_press_feedback_transition(css):
    seen = set()
    for rule in css.rules:
        value = rule.get("transition")
        if value is None or rule.context == (REDUCE,):
            continue
        for part in _split_top(value, ","):
            prop, length = part.split()[:2]
            assert prop in TRANSITIONS, (rule.selector, part)
            assert length == (".08s" if prop == "scale" else ".34s"), (rule.selector, part)
            seen.add(prop)
            if prop != "scale":
                assert rule.selector.startswith(".sheet[popover]"), rule.selector
    assert seen == TRANSITIONS


PRESSED = {"button.tile:active": ".97", ".button:active": ".98", "button.primary:active": ".98",
           ".tab:active": ".96", "a.pill:active::before": ".92"}


def test_press_feedback_scales(css):
    for selector, scale in PRESSED.items():
        assert css.value(selector, "scale") == scale, selector


def test_the_sheet_slides_from_a_starting_style(css):
    assert css.value(".sheet[popover]:popover-open", "translate", ("@starting-style",)) == "0 100%"
    assert css.value(".sheet[popover]:popover-open", "translate", (LAPTOP, "@starting-style")) == "0 32px"
    assert css.value(".sheet[popover]::backdrop", "background") == "rgb(0 0 0 / .62)"
    assert "allow-discrete" in css.value(".sheet[popover]", "transition")


def test_the_rolling_number_shows_only_on_the_today_card_with_motion(css):
    assert css.value(".roll[hidden]", "display") == "none"
    shown = [r for r in css.rules if r.get("display") == "flex" and any(".roll[hidden]" in s for s in r.selectors)]
    assert shown and all(r.context == (NO_PREFERENCE,) for r in shown)
    assert all(s.startswith("body:not(.calm) .hero .big ") for r in shown for s in r.selectors)
    assert css.value("body:not(.calm) .hero .big .rolling-text", "color", (NO_PREFERENCE,)) == "transparent"
    assert {f".roll .d{n} .strip" for n in range(10)} <= {s for r in css.rules for s in r.selectors}


def test_words_in_the_today_cards_number_slot_wrap_at_the_titles_size(css):
    """"No food yet" in the 64 px nowrap slot would run off a 360 px phone. Words there (p.big.words) take
    h2.hero-title's type and wrap. The plain .big keeps nowrap for a number."""
    for prop in ("font-size", "line-height", "font-weight", "letter-spacing"):
        assert css.value(".hero .big.words", prop) == css.value(".hero h2.hero-title", prop), prop
    assert css.value(".hero .big.words", "white-space") == "normal"
    assert css.value(".big", "white-space") == "nowrap"



def test_two_lift_sessions_sit_apart_as_two_meals_do(css):
    """Two sessions on the Lifts card are spaced as two meals on the Food card: the same gap, one rule each."""
    assert css.value(".session + .session", "margin-top") == css.value(".meal + .meal", "margin-top") == "18px"
    [rule] = css.find(".session + .session")
    assert rule.decls == [("margin-top", "18px", False)]


# ---------------------------------------------------------------- tiles and sheets without popovers

def test_a_tile_twin_shows_where_popovers_do_not(css):
    supports = ("@supports selector(:popover-open)",)
    assert css.value("a.tile-link", "display") == "flex"
    assert css.value("button.tile[popovertarget]", "display") == "none"
    assert css.value("a.tile-link", "display", supports) == "none"
    assert css.value("button.tile[popovertarget]", "display", supports) == "flex"
    assert css.value("[popover]", "display", ("@supports not selector(:popover-open)",)) == "none"


# ---------------------------------------------------------------- every class section L names is carried

NAMED_CLASSES = """
card card-head is-empty tiles four tile tile-weight tile-calories tile-training tile-maintenance tile-pace tile-run
tile-time tile-max tile-best tile-sessions tile-value tile-label tile-note tile-link sr nowrap chips chip chip-weight
chip-lifts chip-cardio chip-food chip-label is-missing primary secondary danger link action wide button tag
tag-you_said tag-saved_food tag-usda tag-claude_estimate tag-estimate tag-sample tag-status tag-good tag-behind
tag-nodata tag-closed top brand tabs tab tab-today tab-progress tab-goals tab-settings top-out ok undo notice
sample-banner goal-note error add field field-row hint has-error segments sheet sheet-head grab sheet-bar close period
giant noun sentence panel panel-title sheet-link table-wrap num remark group rows lead figure big unit figure-label
days pill is-lift is-cardio is-none is-today legend key line plan projection goal today dot-key link-box muted inline
steps entry-line attribution meal empty chart trends facts entries totals goals calm roll glance col-main col-side
goals-main set-goal grid axis value series smoothed area dot weigh-in is-latest end-dot proj-end goal-label today-line
today-label target-tick bar small
""".split()


def test_every_class_section_l_names_has_a_rule(css):
    classes = set(re.findall(r"\.([a-zA-Z_][\w-]*)", " ".join(r.selector for r in css.rules)))
    assert not [c for c in NAMED_CLASSES if c not in classes]


# ---------------------------------------------------------------- chart text on screen: 11 to 14 px

VIEWPORTS = (360, 390, 720, 959, 960, 1280)


def _px(value: str) -> float:
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", value.strip())
    assert match, value
    return float(match.group(1))


class Layout:
    """The stylesheet's own numbers for the boxes a chart sits in, and the width each is drawn at."""

    def __init__(self, css: Sheet):
        self.css = css
        r = root(css)
        self.gutter, self.pad = _px(r.get("--gutter")), _px(r.get("--pad"))
        self.content = _px(r.get("--content"))
        self.gutter_l = _px(css.value(":root", "--gutter", (LAPTOP,)))
        self.gap_l = _px(css.value(":root", "--gap", (LAPTOP,)))
        self.card_pad = self._var(css.value(".chart-card", "padding"))
        self.pair_gap = self._var(css.value(".pair", "gap"))
        self.pair_gap_l = self._var(css.value(".pair", "gap"), laptop=True)
        self.callouts = _px(css.value(".bw-wrap", "grid-template-columns", (LAPTOP,)).split()[-1])
        self.bw_gap = _px(css.value(".bw-wrap", "gap"))
        self.goal_pad = _px(css.value("li.goal", "padding", (LAPTOP,)))
        self.goal_col_gap = _px(css.value("li.goal", "column-gap", (LAPTOP,)))
        frs = [float(f) for f in re.findall(r"([\d.]+)fr", css.value("li.goal", "grid-template-columns", (LAPTOP,)))]
        self.goal_share = frs[0] / sum(frs)
        self.spans = {sel: self._span(css.value(sel, "grid-column", (LAPTOP,)))
                      for sel in (".progress .bw-card", ".lift-page .chart-card", ".goals-main",
                                  ".cardio-page .pace-card")}
        glance = re.fullmatch(r"calc\(100% \* (\d+) / 12 - ([\d.]+)px\)",
                              css.value(".today > .glance", "width", (LAPTOP,)))
        self.glance = (int(glance.group(1)), float(glance.group(2)))
        self.sheet_max = _px(css.value(".sheet[popover]", "max-width"))
        self.sheet_pad = _px(css.value(".sheet[popover]", "padding").split()[1])
        self.sheet_border = _px(css.value(".sheet[popover]", "border").split()[0])
        self.panel_pad = _px(css.value(".sheet .panel", "padding"))

    def _var(self, value: str, laptop: bool = False) -> float:
        name = re.fullmatch(r"var\((--[\w-]+)\)", value)
        if not name:
            return _px(value)
        if laptop and css_has(self.css, name.group(1)):
            return _px(self.css.value(":root", name.group(1), (LAPTOP,)))
        return _px(root(self.css).get(name.group(1)))

    @staticmethod
    def _span(value: str) -> int:
        start, end = (v.strip() for v in value.split("/"))
        if end.startswith("span"):
            return int(end.removeprefix("span "))
        return (13 if end == "-1" else int(end)) - int(start)

    def span(self, n: int, viewport: float) -> float:
        inner = min(viewport - 2 * self.gutter_l, self.content)
        col = (inner - 11 * self.gap_l) / 12
        return n * col + (n - 1) * self.gap_l

    def width(self, chart: str, v: float) -> float:
        """The box a chart is drawn in, before its own cap, at viewport width v."""
        sheet = min(v, self.sheet_max) - 2 * self.sheet_border - 2 * self.sheet_pad - 2 * self.panel_pad
        if chart == "sheet":
            return sheet
        if v < 960:
            if chart == "small":
                return (v - 2 * self.gutter - self.pair_gap) / 2 - 2 * self.card_pad
            return v - 2 * self.gutter - 2 * self.pad
        if chart == "body weight":
            return self.span(self.spans[".progress .bw-card"], v) - 2 * self.pad - self.callouts - self.bw_gap
        if chart == "lift":
            return self.span(self.spans[".lift-page .chart-card"], v) - 2 * self.card_pad
        if chart == "goal":
            card = self.span(self.spans[".goals-main"], v) - 2 * self.goal_pad
            return (card - self.goal_col_gap) * self.goal_share
        if chart == "pace":
            return self.span(self.spans[".cardio-page .pace-card"], v) - 2 * self.card_pad
        n, less = self.glance
        glance = min(v - 2 * self.gutter_l, self.content) * n / 12 - less
        return (glance - self.pair_gap_l) / 2 - 2 * self.card_pad


def css_has(css: Sheet, token: str) -> bool:
    return any(r.get(token) for r in css.find(":root", (LAPTOP,)))


# chart -> (its viewBox width, (the selector that sizes its text, the one that caps its width) below 960
# px, the same from 960 px up)
CHART_TEXT = {
    "body weight": (charts.BW_W, ("svg.chart", "svg.chart"), ("#bodyweight-chart", "#bodyweight-chart")),
    "lift": (charts.LIFT_W, ("svg.chart", "svg.chart"), ("#progress-chart", "#progress-chart")),
    "goal": (charts.GOAL_W, ("svg.chart", "svg.chart"), ("li.goal svg.chart", "li.goal svg.chart")),
    "pace": (charts.PACE_W, ("svg.chart", "svg.chart"), ("#chart-pace", "#chart-pace")),
    "sheet": (charts.SHEET_W, ("svg.chart", "svg.chart"), (".sheet svg.chart", ".sheet svg.chart")),
    "small": (charts.SMALL_W, ("svg.chart.small", "svg.chart.small"),
              (".glance svg.chart.small", ".glance svg.chart.small")),
}


def _units(css: Sheet, selector: str, context) -> float:
    axis = _px(css.value(f"{selector} .axis", "font-size", context))
    if not selector.endswith(".small"):
        assert _px(css.value(f"{selector} .goal-label", "font-size", context)) == axis, selector
    return axis


def _cap(css: Sheet, selector: str, context) -> float:
    value = css.value(selector, "max-width", context)
    return float("inf") if value == "none" else _px(value)


def on_screen(css: Sheet, layout: Layout, chart: str, v: float) -> float:
    """The chart's text size in CSS px at viewport width v: units x drawn width / viewBox width."""
    box, phone, laptop = CHART_TEXT[chart]
    size_sel, cap_sel = phone if v < 960 else laptop
    context = () if v < 960 else (LAPTOP,)
    return _units(css, size_sel, context) * min(layout.width(chart, v), _cap(css, cap_sel, context)) / box


@pytest.mark.parametrize("chart", sorted(CHART_TEXT))
def test_chart_text_shows_at_11_to_14_px_at_every_width(css, chart):
    layout = Layout(css)
    sizes = {v: round(on_screen(css, layout, chart, v), 2) for v in (*VIEWPORTS, *range(360, 1601, 13))}
    assert all(11 <= s <= 14 for s in sizes.values()), {v: s for v, s in sizes.items() if not 11 <= s <= 14}


PHONE_CAPS = {"svg": "100%", "svg.chart": "336px", "svg.chart.small": "149px"}  # with the reset img, svg
# The six named contexts: each sets its own cap from 960 px up (the sheet's is none: 560 px at most).
NAMED_CAPS = {"#bodyweight-chart": "386px", "#progress-chart": "690px", "li.goal svg.chart": "373px",
              "#chart-pace": "403px", ".glance svg.chart.small": "261px", ".sheet svg.chart": "none"}


# A selector that can size a chart: one naming svg, the class chart (not chart-card), or a chart's id.
CHART_SELECTOR = re.compile(r"\bsvg\b|\.chart(?![\w-])|#[\w-]*chart\b")


def _caps(css: Sheet, context) -> dict[str, str]:
    """Every max-width a chart selector sets in this context. None of them may be !important."""
    out = {}
    for r in css.rules:
        if r.context != context:
            continue
        for prop, value, important in r.decls:
            if prop != "max-width" or not any(CHART_SELECTOR.search(s) for s in r.selectors):
                continue
            assert not important, f"{r.selector} sets an !important max-width"
            out.update({s: value for s in r.selectors if CHART_SELECTOR.search(s)})
    return out


def test_a_chart_outside_the_named_contexts_keeps_the_phone_cap_on_a_laptop(css):
    """From 960 px up only the six named contexts change a chart's cap, so a chart placed anywhere else
    keeps the base rule's phone cap: drawn as at 959 px, where the chart text test holds it."""
    assert _caps(css, ()) == PHONE_CAPS
    laptop = _caps(css, (LAPTOP,))
    assert laptop == NAMED_CAPS, laptop


# ---------------------------------------------------------------- which cap a chart gets, by the cascade

_COMPOUND = re.compile(r"([a-z][\w-]*)?((?:[.#][\w-]+)*)")


def _compound(text: str):
    """(tag, ids, classes) for a simple compound such as svg.chart.small. A pseudo-class, an attribute or a
    combinator is not modelled: such a selector never matches here."""
    m = _COMPOUND.fullmatch(text)
    if not m or not text:
        return None
    parts = re.findall(r"([.#])([\w-]+)", m.group(2))
    return m.group(1), {v for k, v in parts if k == "#"}, {v for k, v in parts if k == "."}


def _fits(compound, element) -> bool:
    tag, ids, classes = compound
    return (not tag or tag == element[0]) and ids <= element[1] and classes <= element[2]


def _matches(selector: str, path: list) -> bool:
    """Does a descendant selector match the last element of `path` (root first)?"""
    compounds = [_compound(c) for c in selector.split()]
    if not compounds or None in compounds or not _fits(compounds[-1], path[-1]):
        return False
    n = len(path) - 2
    for compound in reversed(compounds[:-1]):
        while n >= 0 and not _fits(compound, path[n]):
            n -= 1
        if n < 0:
            return False
        n -= 1
    return True


def _specificity(selector: str) -> tuple[int, int, int]:
    compounds = [_compound(c) for c in selector.split()]
    return (sum(len(c[1]) for c in compounds), sum(len(c[2]) for c in compounds),
            sum(1 for c in compounds if c[0]))


def cap_of(css: Sheet, path: list, laptop: bool) -> str:
    """The max-width the cascade gives the last element of `path`: the most specific matching rule, the
    later one on a tie, from the top level and, from 960 px up, the laptop block."""
    contexts = [(), (LAPTOP,)] if laptop else [()]
    best = None
    for order, rule in enumerate(css.rules):
        if rule.context not in contexts or rule.get("max-width") is None:
            continue
        for selector in rule.selectors:
            if _matches(selector, path):
                key = (_specificity(selector), order)
                if best is None or key > best[0]:
                    best = (key, rule.get("max-width"))
    return best[1] if best else "none"


def _el(tag: str, *classes: str, id_: str = "") -> tuple:
    return (tag, {id_} if id_ else set(), set(classes))


# (where the chart sits, root first) -> (its cap below 960 px, its cap from 960 px up)
PLACED = {
    "a chart in a plain card": ([_el("div", "card"), _el("svg", "chart")], ("336px", "336px")),
    "a small chart in a plain card": ([_el("div", "card"), _el("svg", "chart", "small")], ("149px", "149px")),
    "a chart in a sheet": ([_el("div", "sheet"), _el("svg", "chart")], ("336px", "none")),
    "a small chart in a sheet": ([_el("div", "sheet"), _el("svg", "chart", "small")], ("149px", "none")),
    "a Today sheet's chart": ([_el("div", "glance"), _el("div", "sheet"), _el("div", "panel"), _el("svg", "chart")],
                              ("336px", "none")),
    "the Today pair": ([_el("div", "glance"), _el("svg", "chart", "small")], ("149px", "261px")),
    "the Today pair in its chart card": ([_el("div", "glance"), _el("div", "pair"),
                                          _el("section", "card", "chart-card"), _el("svg", "chart", "small")],
                                         ("149px", "261px")),
    "a goal chart": ([_el("li", "goal"), _el("svg", "chart")], ("336px", "373px")),
    "the body weight chart": ([_el("main", "page", "progress"), _el("section", "card", "bw-card", id_="body-weight"),
                               _el("div", "bw-wrap"), _el("svg", "chart", id_="bodyweight-chart")],
                              ("336px", "386px")),
    "a Progress sheet's chart": ([_el("main", "page", "progress"), _el("div", "glance"), _el("div", "sheet"),
                                  _el("div", "panel"), _el("svg", "chart")], ("336px", "none")),
    "the lift chart": ([_el("div", "chart-card"), _el("svg", "chart", id_="progress-chart")], ("336px", "690px")),
    "the pace chart": ([_el("div", "pace-card"), _el("svg", "chart", id_="chart-pace")], ("336px", "403px")),
}


@pytest.mark.parametrize("place", sorted(PLACED))
def test_each_chart_gets_its_cap_by_the_cascade(css, place):
    """The tie included: .sheet svg.chart and svg.chart.small are both (0,2,1), and the laptop rule comes
    later, so a small chart in a sheet has no cap from 960 px up, as before."""
    path, (phone, laptop) = PLACED[place]
    assert (cap_of(css, path, False), cap_of(css, path, True)) == (phone, laptop)


def test_the_cascade_model_reads_selectors_as_a_browser_would():
    assert _matches("li.goal svg.chart", [_el("ul"), _el("li", "goal"), _el("div"), _el("svg", "chart")])
    assert not _matches("li.goal svg.chart", [_el("li"), _el("svg", "chart")])
    assert not _matches(".sheet svg.chart:not(.small)", [_el("div", "sheet"), _el("svg", "chart")])
    assert _specificity("#x svg.chart") == (1, 1, 1) and _specificity(".glance svg.chart.small") == (0, 3, 1)


def test_charts_size_their_margins_for_the_stylesheets_phone_text(css):
    assert _px(css.value("svg.chart .axis", "font-size")) == charts.AXIS
    assert _px(css.value("svg.chart .goal-label", "font-size")) == charts.AXIS
    assert _px(css.value("svg.chart.small .axis", "font-size")) == charts.SMALL_AXIS
    laptop = [(_px(v), r.selector) for r in css.rules if r.context == (LAPTOP,)
              and any(".axis" in s for s in r.selectors) for p, v, _ in r.decls if p == "font-size"]
    big = [(size, sel) for size, sel in laptop if size > (charts.SMALL_AXIS if ".small" in sel else charts.AXIS)]
    assert laptop and not big  # no chart's text is bigger in its own units than on a phone


def test_no_page_wide_clip_hides_sideways_overflow(css):
    for selector in ("html", "body"):
        for rule in css.find(selector):
            assert rule.get("overflow-x") is None and rule.get("overflow") is None, selector


COSMETIC = {"padding-bottom", "border", "border-color", "background", "background-color"}


def test_has_is_only_ever_cosmetic(css):
    """A browser without :has() keeps every layout: a past day is the class past on main.page.today."""
    for rule in css.rules:
        if ":has(" in rule.selector:
            assert {p for p, _, _ in rule.decls} <= COSMETIC, rule.selector
    assert css.value(".page.today.past > .hero", "float", (LAPTOP,)) == "none"


def test_chart_cards_take_the_card_gap_and_padding(css):
    """Section L, Spacing: cards 12 px apart on a phone and 16 px on a laptop, card padding 20 px."""
    assert css.value(".pair", "gap") == "var(--gap)" and css.value(".chart-card", "padding") == "var(--pad)"
    assert not css.find(".pair", (LAPTOP,)) and not css.find(".chart-card", (LAPTOP,))
    assert (root(css).get("--gap"), root(css).get("--pad"), css.value(":root", "--gap", (LAPTOP,))) ==         ("12px", "20px", "16px")


def test_the_behind_line_reads_as_muted_words_and_sits_under_the_facts_on_a_laptop(css):
    """Goals B: the behind line, p.goal-ask, looks as p.muted does on a phone. On a laptop it takes the facts' column,
    under them, and spans the row the actions take, so the actions stay under the chart (no row number: the grid's
    own placement keeps the markup order). Its rules are the only ones it has."""
    for prop in ("color", "font-size"):
        assert css.value("p.goal-ask", prop) == css.value(".muted", prop), prop
    assert css.value("li.goal > p.goal-ask", "grid-column", (LAPTOP,)) == css.value(
        "li.goal > dl.goal-facts", "grid-column", (LAPTOP,)) == "2"
    assert css.value("li.goal > p.goal-ask", "grid-row", (LAPTOP,)) == "span 2"
    assert css.value("li.goal > .goal-actions", "grid-column", (LAPTOP,)) == "1"
    assert [r.selector for r in css.rules if "goal-ask" in r.selector] == ["p.goal-ask", "li.goal > p.goal-ask"]


def test_the_set_a_goal_forms_parts_each_have_their_rule(css):
    """Goals C: the segments a row of 44 px pills, the current one filled white; the form a grid of stacked fields;
    the hint and the grey line in --text-3; an error under its field in --err, its input bordered in --err. Each
    part the form draws is already styled, so pass B14 adds no rule."""
    assert css.value(".segments", "display") == "flex"
    assert css.value(".segments a", "min-height") == "44px"
    assert css.value('.segments a[aria-current="true"]', "background") == "var(--text)"
    assert css.value('.segments a[aria-current="true"]', "color") == "var(--on-primary)"
    assert css.value("form.goal-form", "display") == css.value(".field", "display") == "grid"
    assert css.value("p.hint", "color") == css.value(".small", "color") == "var(--text-3)"
    assert css.value("p.error", "margin-top") == "6px" and css.value(".error", "color") == "var(--err)"
    assert css.value('[aria-invalid="true"]', "border") == "1.5px solid var(--err)"
    assert css.value("div.error", "border") == "1.5px solid var(--err)"


def test_the_foods_pages_parts_each_have_their_rule(css):
    """Foods (pass B15): on a laptop Saved foods takes columns 1 to 8 and Save a food 9 to 12, under the page head,
    the banner and the strip, which span both; the grey line under the h1 in --text-2; the table in its sideways
    scrolling wrap; the empty card dashed; the fold-out's plus turns when open; the strips and the edit page's back
    link and form are the edit pages' own. Each part the two pages draw is already styled, so pass B15 adds no rule."""
    assert css.value(".foods-page .saved-foods", "grid-column", (LAPTOP,)) == "1 / span 8"
    assert css.value(".foods-page .save-food", "grid-column", (LAPTOP,)) == "9 / -1"
    assert css.value(".page > *", "grid-column", (LAPTOP,)) == "1 / -1"
    assert css.value(".page-head .lede", "color") == "var(--text-2)"
    assert css.value(".page-head .lede", "flex-basis") == "100%"
    assert css.value("div.table-wrap", "overflow-x") == "auto"
    assert css.value(".card.is-empty", "border") == "1.5px dashed var(--line-strong)"
    assert css.value("p.empty-line", "color") == "var(--text-2)"
    assert css.value("details.add[open] > summary::before", "rotate") == "45deg"
    assert css.value("div.ok.undo form", "margin-left") == "auto" and css.value("p.ok", "display") == "flex"
    assert css.value(".back", "min-height") == "44px" and css.value(".form-page > *", "max-width") == "640px"
    assert css.value("form.stack", "display") == css.value("details.add > form", "display") == "grid"
    assert css.value(".field .hint", "color") == "var(--text-3)"
