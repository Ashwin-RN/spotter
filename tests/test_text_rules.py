"""Unit tests of the text rule checks in tests/text_rules.py, on hand-made strings and sources.

Then T1, the sweep over the app, at the end of the file. Banned characters are written as escapes.
"""

from pathlib import Path

import pytest

from . import text_rules as tr

EM, EN, ELL, LARR, RARR = "\u2014", "\u2013", "\u2026", "\u2190", "\u2192"
LSQ, RSQ, LDQ, RDQ = "\u2018", "\u2019", "\u201c", "\u201d"
TIMES, MIDDOT, NBSP, MINUS, BOM = "\u00d7", "\u00b7", "\u00a0", "\u2212", "\ufeff"


def rules(findings):
    return [f.rule for f in findings]


def matches(findings):
    return [f.match for f in findings]


# ---------------------------------------------------------------- findings


def test_a_finding_names_its_rule_its_match_and_a_short_context():
    [f] = tr.check_semicolons("x" * 100 + "; " + "y" * 100)
    assert (f.rule, f.match) == (tr.SEMICOLON, ";")
    assert len(f.context) <= 2 * tr.CONTEXT + 1
    assert "semicolon" in str(f) and "';'" in str(f)


# ---------------------------------------------------------------- characters


@pytest.mark.parametrize("char, rule", [
    (EM, tr.DASH), (EN, tr.DASH), (MINUS, tr.DASH),
    (ELL, tr.ELLIPSIS),
    (LARR, tr.ARROW), (RARR, tr.ARROW), ("\u21d2", tr.ARROW), ("\u27f6", tr.ARROW),
    (LSQ, tr.CURLY_QUOTE), (RSQ, tr.CURLY_QUOTE), (LDQ, tr.CURLY_QUOTE), (RDQ, tr.CURLY_QUOTE),
    ("\U0001f4aa", tr.EMOJI), ("\u2705", tr.EMOJI), ("\u2764", tr.EMOJI),
    ("\u03b1", tr.CHARACTER), ("\u2248", tr.CHARACTER), ("\u00e9", tr.CHARACTER),
])
@pytest.mark.parametrize("surface", tr.SURFACES)
def test_a_banned_character_is_found_on_every_surface(char, rule, surface):
    assert rules(tr.check_characters(f"Back squat{char}100 kg", surface)) == [rule]


@pytest.mark.parametrize("text, rule", [
    ("Saved - see Today", tr.DASH),
    ("Saved -- see Today", tr.DASH),
    ("Saved--see Today", tr.DASH),
    (f"Saved{NBSP}-{NBSP}see Today", tr.DASH),
    (f"Saved {NBSP}- see Today", tr.DASH),
    ('"Saved" - see Today', tr.DASH),
    ("Saved. - See Today", tr.DASH),
    ("Saved - 'Today'", tr.DASH),
    ("Wait . . . then log", tr.ELLIPSIS),
    ("kcal -> 300", tr.ARROW),
    ("Today => Progress", tr.ARROW),
    ("Progress <- Today", tr.ARROW),
    ("Loading...", tr.ELLIPSIS),
    ("Logged!", tr.EXCLAMATION),
    ("~2,500 kcal", tr.TILDE),
    ("about ~ 80 kg", tr.TILDE),
])
def test_an_ascii_form_of_a_banned_mark_is_found(text, rule):
    assert rules(tr.check_characters(text, tr.PAGE)) == [rule]


@pytest.mark.parametrize("text", [
    "One weigh-in a day.",
    "-0.24 kg a week",
    "Rate -12 seconds a week",
    "20 to 400 kg",
    "x != y",
    r"(?!\d)(?<!\d)",
    "claude mcp add --transport http",
    "~/spotter",
    "3 sets, 5 reps. Done.",
])
def test_plain_ascii_passes(text):
    assert tr.check_characters(text, tr.PAGE) == []


def test_the_allowlist_depends_on_the_surface():
    times = f"3 {TIMES} 5 at 60 kg"
    for surface in tr.SURFACES:
        assert tr.check_characters(times, surface) == []
    title = f"Progress {MIDDOT} Spotter"
    assert tr.check_characters(title, tr.TITLE) == []
    assert tr.check_characters(title, tr.SOURCE) == []
    for surface in (tr.PAGE, tr.SUMMARY, tr.MESSAGE, tr.CLAUDE, tr.DOC):
        assert rules(tr.check_characters(title, surface)) == [tr.CHARACTER]
    unit = f"80{NBSP}kg"
    for surface in (tr.PAGE, tr.TITLE, tr.SOURCE):
        assert tr.check_characters(unit, surface) == []
    for surface in (tr.SUMMARY, tr.MESSAGE, tr.CLAUDE, tr.DOC):  # a service label keeps a plain space
        assert rules(tr.check_characters(unit, surface)) == [tr.CHARACTER]


def test_allow_adds_a_character_for_one_call():
    assert rules(tr.check_characters(BOM + "date,kg", tr.SOURCE)) == [tr.CHARACTER]
    assert tr.check_characters(BOM + "date,kg", tr.SOURCE, allow={BOM}) == []
    assert tr.check(BOM, tr.SOURCE, allow={BOM}) == []


# ---------------------------------------------------------------- word lists

ALL_WORDS = [(rule, w) for rule, words in tr.WORD_LISTS.items() for w in words]


@pytest.mark.parametrize("rule, word", ALL_WORDS)
def test_every_listed_word_is_found_by_its_own_check(rule, word):
    check = {tr.FILLER: tr.check_filler, tr.CHAT_PHRASE: tr.check_chat_phrases,
             tr.AI_VOCABULARY: tr.check_ai_vocabulary}[rule]
    for text in (f"Then {word} log it.", f"{word.upper()} it is", f"{word.capitalize()}."):
        found = check(text)
        assert rules(found) == [rule], text
        assert found[0].match.casefold() == word.casefold()
    assert rules(tr.check_words(f"Then {word} log it.")) == [rule]


def test_the_word_lists_are_lower_case_trimmed_and_disjoint():
    seen = set()
    for words in tr.WORD_LISTS.values():
        for w in words:
            assert w == w.strip().lower() and "  " not in w
            assert w not in seen
            seen.add(w)


def test_an_attribute_name_in_markup_is_not_prose():
    for text in ('<g transform="rotate(90)">', "<g transform='x'>", '<text highlight="1">'):
        assert tr.check_words(text) == [], text
    for text in ("<g>Transform your data</g>", 'transform = "x"', "transform=x", 'Then "transform" it'):
        assert rules(tr.check_words(text)) == [tr.AI_VOCABULARY], text


def test_contractions_are_a_function_but_no_surface_runs_them():
    assert rules(tr.check_contractions("You can't.")) == [tr.CONTRACTION]
    for surface in tr.SURFACES:
        assert "contractions" not in tr.RULES[surface]
        assert tr.check("You can't.", surface) == []
    assert rules(tr.check("You can't.", tr.PAGE, rules=["contractions"])) == [tr.CONTRACTION]


def test_a_word_in_quotes_is_found():
    assert rules(tr.check_chat_phrases("Reply 'Great, logged'.")) == [tr.CHAT_PHRASE]
    assert rules(tr.check_filler('Say "just start".')) == [tr.FILLER]


def test_a_phrase_matches_across_any_whitespace():
    assert rules(tr.check_filler("Note\n  that it counts.")) == [tr.FILLER]
    assert rules(tr.check_ai_vocabulary("in   order to log")) == [tr.AI_VOCABULARY]


@pytest.mark.parametrize("text", [
    "Adjust the target.",          # just
    "It is justified.",            # just
    "Pleased to log.",             # please
    "A greater share.",            # great
    "Overalls are clothes.",       # overall
    "Really-long-name",            # really, inside a hyphenated word
    "The highlighter",             # highlight
    "Deliverables",                # deliver
    "Transformer",                 # transform
    "highlight_rows",              # inside a code word
    "The dedupe key, the API key.",  # key is left out
    "Spaces or underscores are forgiven.",  # underscore is left out
    "A steady change.",            # newest-period words go by eye
])
def test_a_word_inside_a_longer_word_or_a_left_out_word_passes(text):
    assert tr.check_words(text) == []


# ---------------------------------------------------------------- sentence shapes


@pytest.mark.parametrize("text", [
    "It is not only a log but a coach.",
    "Not only fast, but also right.",
    "Spotter is not just a log.",
    "It's not a goal, it's a setting.",
    "It is not a goal; it is a setting.",
    "The line serves as the trend.",
    "This stands as a record.",
    "The switch acts as a guard.",
])
def test_a_listed_shape_is_found(text):
    assert rules(tr.check_shapes(text)) == [tr.SHAPE]


@pytest.mark.parametrize("text", [
    "A setting, not a goal.",                     # a contrast that removes an ambiguity stays
    "It is kept. It can be restored.",
    "The line is the trend.",
    "Not logged yet. But you can add one.",       # "not" and "but" in two sentences
    "Only the person can change it.",
])
def test_a_plain_contrast_passes(text):
    assert tr.check_shapes(text) == []


def test_a_negative_contraction_is_found_and_the_long_form_passes():
    assert matches(tr.check_contractions("You can't, don't, won't or isn't.")) == ["can't", "don't", "won't", "isn't"]
    assert rules(tr.check_contractions(f"Don{RSQ}t")) == [tr.CONTRACTION]
    assert tr.check_contractions("You cannot. Do not. Will not. It's fine.") == []


# ---------------------------------------------------------------- punctuation


def test_a_semicolon_is_found():
    assert matches(tr.check_semicolons("Lunch: roti; dinner: dal")) == [";"]
    assert tr.check_semicolons("Lunch: roti. Dinner: dal.") == []


@pytest.mark.parametrize("text", ["Back Squat 3 x 5 @ 60 kg", "5 reps @60 kg", "Lifts & cardio", "R&D", "& more",
                                  "Lifts &cardio", "Lifts& cardio"])
def test_at_or_ampersand_for_a_word_is_found(text):
    assert rules(tr.check_at_ampersand(text)) == [tr.AT_AMPERSAND]


@pytest.mark.parametrize("text", ["Back Squat 3 x 5 at 60 kg", "Lifts and cardio", "person@example.com",
                                  "Foods &amp; more", "80&#160;kg", "a &nbsp; b"])
def test_at_or_ampersand_inside_an_address_passes(text):
    assert tr.check_at_ampersand(text) == []


@pytest.mark.parametrize("text", ["Run 30 min (estimate, from body weight)", "Roti 120 kcal [estimate]",
                                  "Total 390 kcal (food minus cardio)"])
def test_words_in_brackets_are_found(text):
    assert rules(tr.check_brackets(text)) == [tr.BRACKETS]


@pytest.mark.parametrize("text", ["Estimated 150 kcal, 38% of it", "(38%)", "[1, 2]", "Total (2,216)"])
def test_brackets_around_numbers_alone_pass(text):
    assert tr.check_brackets(text) == []


# ---------------------------------------------------------------- numbers and ids


@pytest.mark.parametrize("text", ["Already logged: lift session 12.", "Food entry 3: nothing changed.",
                                  "Weigh-in 7 changed", "Replaced (id 41).", "goal #12", "Cardio entry 5 changed"])
def test_an_id_is_found(text):
    assert rules(tr.check_ids(text)) == [tr.ID]


@pytest.mark.parametrize("text", ["Weigh-in 79.5 kg", "Measurement 80 cm", "3 sessions in the last 7 days",
                                  "Set 2, 5 reps", "Session 1 kg", "Fastest session 31:00", "Goal 100 kg by Tue 10 Nov", "Entry 2,000 kcal"])
def test_a_figure_after_an_entry_word_is_not_an_id(text):
    assert tr.check_ids(text) == []


def test_an_iso_date_is_found_and_the_page_form_passes():
    assert matches(tr.check_iso_dates("Logged on 2026-11-10.")) == ["2026-11-10"]
    assert matches(tr.check_iso_dates("2026-11-10T08:00:00+00:00")) == ["2026-11-10"]
    assert tr.check_iso_dates("Logged on Tue 10 Nov.") == []
    assert tr.check_iso_dates("Phone 12345-67-89012") == []


@pytest.mark.parametrize("text, found", [
    ("+0 kg a week", ["+0"]), ("rate +0.0 kg", ["+0.0"]), ("rate +0.", ["+0."]),
    ("+0.24 kg a week", []), ("0 kg a week", []), ("UTC+05:30", []), ("+05:30", []), ("+1 kg", []),
])
def test_plus_zero(text, found):
    assert matches(tr.check_plus_zero(text)) == found


@pytest.mark.parametrize("text, found", [
    ("80.0 kg", ["80.0"]), ("7.50 h", ["7.50"]), ("Weight 80.0.", ["80.0"]), ("29:30.0", ["30.0"]),
    ("0.10 kg", ["0.10"]), ("80 kg", []), ("80.1 kg", []), ("80.05 kg", []), ("version 1.0.3", []),
])
def test_trailing_zero(text, found):
    assert matches(tr.check_trailing_zero(text)) == found


def test_widened_allows_trailing_zeros():
    assert tr.check_trailing_zero("Now 80.10 kg, plan 80.00 kg, 29:30.0", widened=True) == []
    assert tr.check("Now 80.10 kg", tr.SUMMARY, widened=True) == []
    assert rules(tr.check("Now 80.10 kg", tr.SUMMARY)) == [tr.TRAILING_ZERO]


@pytest.mark.parametrize("text, found", [
    ("2216 kcal", ["2216"]), ("Totals 1234, 5678", ["1234", "5678"]), ("12345.6 steps", ["12345"]),
    ("-1500 kcal", ["1500"]), ("Since 2025.", ["2025"]),
    ("2,216 kcal", []), ("999 kcal", []), ("12,345 steps", []), ("0.12345", []),
    ("Tue 10 Nov 2025", []), ("Sunday 8 November 2025", []), ("3 to 9 Nov 2025", []),
    ("29 Dec 2025 to 4 Jan 2026", []), ("Sept 2025", []),
])
def test_thousands(text, found):
    assert matches(tr.check_thousands(text)) == found


def test_a_year_needs_a_month_name_before_it():
    assert matches(tr.check_thousands("Nov 12025")) == ["12025"]  # five digits is no year
    assert matches(tr.check_thousands("No 2025")) == ["2025"]


# ---------------------------------------------------------------- page rules


def test_code_words_are_found_on_a_page():
    assert matches(tr.check_code_words("Or give start_value, then log_lifts.")) == ["start_value", "log_lifts"]
    assert matches(tr.check_code_words('Then say "whoami".')) == ["whoami"]
    assert tr.check_code_words("Where you are now. One weigh-in a day. Who am I?") == []


def test_function_names_come_from_the_server():
    names = tr.function_names()
    assert "whoami" in names and "log_lifts" in names


def test_sentence_length():
    long = " ".join(["word"] * 26) + "."
    [f] = tr.check_sentence_length(long)
    assert (f.rule, f.context) == (tr.SENTENCE_LENGTH, "26 words")
    assert tr.check_sentence_length(" ".join(["word"] * 25) + ".") == []
    assert tr.check_sentence_length((" ".join(["word"] * 20) + ". ") * 3) == []
    assert tr.check_sentence_length(" ".join(["3", TIMES] * 20)) == []  # a symbol is not a word
    assert tr.check_sentence_length("a b c d", limit=3) != []


@pytest.mark.parametrize("piece", ["none", " None yet ", "0", "-", "--", EM, EN, "n/a", "N/A"])
def test_a_mark_standing_for_a_figure_is_found(piece):
    assert rules(tr.check_empty_figure(piece)) == [tr.EMPTY_FIGURE]


@pytest.mark.parametrize("piece", ["None logged in the last 7 days", "0 kcal", "No weigh-ins yet.", "10", ""])
def test_words_or_a_real_figure_pass(piece):
    assert tr.check_empty_figure(piece) == []


# ---------------------------------------------------------------- surfaces


def test_every_surface_has_rules_and_every_rule_has_a_check():
    assert set(tr.RULES) == set(tr.SURFACES)
    for names in tr.RULES.values():
        assert names[0] == "characters"
        assert set(names) <= set(tr.RULE_NAMES)
    assert set(tr.T1_TEMPLATE_RULES) <= set(tr.RULE_NAMES)
    assert set(tr.T1_SOURCE_RULES) <= set(tr.RULE_NAMES)


def test_check_runs_the_surface_rules_only():
    text = "Lunch; dinner. Logged 2026-11-10, 2216 kcal (estimate). Just +0 kg." + EM
    assert set(rules(tr.check(text, tr.SUMMARY))) == {
        tr.SEMICOLON, tr.ISO_DATE, tr.THOUSANDS, tr.BRACKETS, tr.FILLER, tr.PLUS_ZERO, tr.DASH}
    assert set(rules(tr.check(text, tr.MESSAGE))) == {tr.FILLER, tr.DASH}
    assert set(rules(tr.check(text, tr.DOC))) == {tr.FILLER, tr.DASH}
    page = tr.check(" ".join(["word"] * 30) + " start_value; 2026-11-10", tr.PAGE)
    assert set(rules(page)) == {tr.SENTENCE_LENGTH, tr.CODE_WORD, tr.SEMICOLON, tr.ISO_DATE}
    assert rules(tr.check("none", tr.PAGE)) == [tr.EMPTY_FIGURE]
    assert tr.check("none", tr.SUMMARY) == []


def test_check_takes_a_subset_of_rules():
    assert rules(tr.check("a; b" + EM, tr.PAGE, rules=["semicolons"])) == [tr.SEMICOLON]
    assert rules(tr.check("a; b" + MIDDOT, tr.TITLE, rules=tr.T1_TEMPLATE_RULES)) == [tr.SEMICOLON]
    with pytest.raises(ValueError):
        tr.check("x", tr.PAGE, rules=["no_such_rule"])
    with pytest.raises(ValueError):
        tr.check("x", "no_such_surface")


def test_the_placeholders_trip_no_rule():
    for surface in tr.SURFACES:
        assert tr.check(f"{tr.ENTERED} {tr.HOLE}", surface) == []
        assert tr.check(f"{tr.HOLE} {tr.HOLE} kg", surface) == []


# ---------------------------------------------------------------- HTML text


def texts(pieces, kind=None):
    return [p.text for p in pieces if kind is None or p.kind == kind]


def test_html_text_splits_at_blocks_and_keeps_inline_elements_together():
    html = ("<div><p>Latest: <strong>80 kg</strong> on <a href='/day/x'>Tue 10 Nov</a>.</p>"
            "<table><tr><td>Back Squat</td><td>100 kg</td></tr></table><p>one<br>two</p></div>")
    assert texts(tr.html_text(html)) == ["Latest: 80 kg on Tue 10 Nov.", "Back Squat", "100 kg", "one", "two"]


def test_html_text_decodes_entities():
    assert texts(tr.html_text("<p>&larr; Foods &amp; more&nbsp;kg</p>")) == [f"{LARR} Foods & more{NBSP}kg"]
    assert texts(tr.html_text("<p>80&#160;kg</p>")) == [f"80{NBSP}kg"]


def test_html_text_leaves_out_an_aria_hidden_roll_and_script_and_style():
    html = ('<p>Total <span aria-hidden="true" class="num roll"><span>1</span><span>2</span></span>'
            '<span class="sr">12</span> kcal</p><script>var x = "just";</script><style>p{}</style><p>After</p>')
    assert texts(tr.html_text(html)) == ["Total 12 kcal", "After"]


def test_html_text_keeps_a_roll_that_is_not_hidden_and_a_hidden_that_is_not_a_roll():
    assert texts(tr.html_text('<p><span class="roll">7</span></p>')) == ["7"]
    assert texts(tr.html_text('<p><span aria-hidden="true">7</span></p>')) == ["7"]
    assert texts(tr.html_text('<p><span aria-hidden="true" class="rolling">7</span></p>')) == ["7"]


def test_html_text_reads_the_attributes_a_person_reads():
    html = ('<button title="Keep this number" aria-label="Confirm entry">OK</button>'
            '<img alt="A chart"><input placeholder="Food" value="hidden"><input type="submit" value="Save">'
            '<a href="/x" class="tag-x" data-x="y">Go</a>')
    pieces = tr.html_text(html)
    assert texts(pieces, "attr") == ["Keep this number", "Confirm entry", "A chart", "Food", "Save"]
    assert texts(pieces, "text") == ["OK", "Go"]


def test_html_text_reads_an_optgroup_label_and_the_meta_tags_a_person_reads():
    html = ('<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta name="description" content="A record you keep by talking">'
            '<meta property="og:title" content="Spotter, really"><meta name="twitter:description" content="Logs">'
            '</head><select><optgroup label="Lifts"><option>Back Squat</option></optgroup></select>')
    pieces = tr.html_text(html)
    assert [(p.text, p.tag) for p in pieces if p.kind == "attr"] == [
        ("A record you keep by talking", "meta"), ("Spotter, really", "meta"), ("Logs", "meta"), ("Lifts", "optgroup")]
    assert texts(pieces, "text") == ["Back Squat"]


def test_html_text_marks_the_document_title_only():
    html = "<head><title>Progress \u00b7 Spotter</title></head><body><svg><title>Weight chart</title></svg></body>"
    pieces = tr.html_text(html)
    assert texts(pieces, "title") == [f"Progress {MIDDOT} Spotter"]
    assert texts(pieces, "text") == ["Weight chart"]


def test_html_text_names_the_element_each_piece_sits_in():
    html = ("<h2>Food</h2><table><tr><td>Roti <strong>120</strong></td></tr></table>"
            "<dl><dd><span>none</span></dd></dl><select><option>None</option></select>"
            "<button title='Keep it'>Confirm</button><p>Text</p>")
    assert [(p.text, p.kind, p.tag) for p in tr.html_text(html)] == [
        ("Food", "text", "h2"), ("Roti 120", "text", "td"), ("none", "text", "dd"), ("None", "text", "option"),
        ("Keep it", "attr", "button"), ("Confirm", "text", "button"), ("Text", "text", "p")]
    assert [p.tag for p in tr.template_text('{{ "Save" if a else "Add" }}')] == ["", ""]


def test_html_text_gives_lines():
    pieces = tr.html_text("<p>one</p>\n\n<p>\n  two</p>")
    assert [(p.text, p.line) for p in pieces] == [("one", 1), ("two", 4)]


# ---------------------------------------------------------------- template text


def test_template_text_reads_a_tag_split_by_an_expression():
    src = '<a href="/day/{{ d.isoformat() }}" rel="prev">&larr; {{ d.isoformat() }}</a>'
    assert texts(tr.template_text(src)) == [f"{LARR} {tr.HOLE}"]


def test_template_text_keeps_each_branch_of_an_if_apart():
    src = '<td>{% if c %}{{ k }} kcal{% else %}<span class="muted">none</span>{% endif %}</td>'
    assert texts(tr.template_text(src)) == [f"{tr.HOLE} kcal", "none"]
    src = "<p>{% if a %}One{% elif b %}Two{% else %}Three{% endif %} logged</p>"
    assert texts(tr.template_text(src)) == ["One", "Two", "Three logged"]


def test_template_text_reads_literals_that_reach_the_page():
    src = """
<button>{{ "Replace weigh-in" if day.weigh_in else "Save weigh-in" }}</button>
<span>{{ note or "Not enough logged yet." }}</span>
<li>New best: {{ b.text if b.text else b.exercise ~ " kg" }}</li>
{% for path, name, line in [("/today", "Today", "everything you logged, by day.")] %}<dd>{{ line }}</dd>{% endfor %}
{{ status_form(g.id, "hit", "Hit") }}
{{ x|default("none yet") }}
{% set label = "Body weight" %}
"""
    assert texts(tr.template_text(src), "literal") == [
        "Replace weigh-in", "Save weigh-in", "Not enough logged yet.", "kg", "/today", "Today",
        "everything you logged, by day.", "hit", "Hit", "none yet", "Body weight"]


def test_template_text_reads_with_values_macro_defaults_and_attribute_access():
    src = """
{% with heading = "Great start", n = 3 %}<h2>{{ heading }}</h2>{% endwith %}
{% macro row(label="Just now", count=0) %}<p>{{ label }}</p>{% endmacro %}
{% for k, v in {"a": "Really good"}.items() %}<dd>{{ v }}</dd>{% endfor %}
<p>{{ "simply logged".upper() }} {{ ("one", "two")|join(", ") }}</p>
"""
    assert texts(tr.template_text(src), "literal") == [
        "Great start", "Just now", "a", "Really good", "simply logged", "one", "two", ","]
    found = [x for p in tr.template_text(src) for x in tr.check(p.text, tr.PAGE, rules=tr.T1_TEMPLATE_RULES)]
    assert sorted(f.match.casefold() for f in found) == ["great", "just", "really", "simply"]


def test_template_format_lines_find_number_formatting_in_a_template():
    src = """<p>{{ x|round(1) }} kg</p>
<p>{{ "%.1f"|format(x) }}</p>
<p>{{ "{:,}".format(n) }}</p>
{% filter format(3) %}%d{% endfilter %}
<p>{{ x|show("kcal", page=True) }} {{ name|upper }} {{ x|int }}</p>
{# {{ x|round }} in a comment #}
<p>{{ "Format your round" }}</p>"""
    assert tr.template_format_lines(src) == [
        (1, "<p>{{ x|round(1) }} kg</p>"), (2, '<p>{{ "%.1f"|format(x) }}</p>'), (3, '<p>{{ "{:,}".format(n) }}</p>'),
        (4, "{% filter format(3) %}%d{% endfilter %}")]


def test_template_text_leaves_out_tests_subscripts_names_and_comments():
    src = """{% extends "base.html" %}{% import "m.html" as m %}
{% block main %}{% include "notice_banner.html" %}
{% if x == "secret one" %}<p>Shown</p>{% endif %}
{% if y is sameas "secret two" %}{% endif %}
<p>{{ errors["secret three"] }} {{ a.b }}</p>
{# just a comment #}
{% endblock %}"""
    pieces = tr.template_text(src)
    assert texts(pieces) == ["Shown"]
    assert not any("secret" in t or "base" in t or "banner" in t or "just" in t for t in texts(pieces))


def test_template_text_leaves_out_a_comparison_inside_an_expression():
    src = '<p>{{ a and b == "secret four" }}</p>{% set ok = x is sameas "secret five" %}<p>{{ "Kept" or c != "secret six" }}</p>'
    assert texts(tr.template_text(src), "literal") == ["Kept"]


def test_template_text_marks_a_title_block():
    src = '{% extends "base.html" %}{% block title %}{{ on }} \u00b7 Spotter{% endblock %}{% block main %}<h1>Day</h1>{% endblock %}'
    pieces = tr.template_text(src)
    assert texts(pieces, "title") == [f"{tr.HOLE} {MIDDOT} Spotter"]
    assert texts(pieces, "text") == ["Day"]
    assert texts(tr.template_text("<title>{% block title %}Spotter{% endblock %}</title>"), "title") == ["Spotter"]


def test_template_text_skips_an_aria_hidden_roll_and_reads_macros_and_attributes():
    src = """{% macro tag(r) %}{% if r.s %}<span class="tag">estimate</span>{% endif %}{% endmacro %}
<p>Total <span aria-hidden="true" class="roll">{{ n }} digits</span> kcal</p>
<button title="Keep this number as yours">Confirm</button>"""
    pieces = tr.template_text(src)
    assert texts(pieces, "text") == ["estimate", "Total kcal", "Confirm"]
    assert texts(pieces, "attr") == ["Keep this number as yours"]


def test_template_text_gives_the_template_lines():
    src = '{% if a %}\n<p>one</p>\n{% endif %}\n{% for x in y %}\n\n<p>{{ x }} two</p>{% endfor %}\n<p>{{ "three" }}</p>'
    pieces = tr.template_text(src)
    assert [(p.text, p.line) for p in pieces if "{" not in p.text or "two" in p.text] == [
        ("one", 2), (f"{tr.HOLE} two", 6), ("three", 7)]
    # a Jinja tag that spans lines swallows them: the lines after it still match the template
    src = '<p>{{ f(\n  a,\n  b) }}</p>{% if x\n  and y %}<p>four</p>{% endif %}\n<p>five</p>'
    assert [(p.text, p.line) for p in tr.template_text(src) if p.kind == "text" and p.text != tr.HOLE] == [
        ("four", 4), ("five", 5)]


def test_template_text_finds_what_t1_must_find():
    src = "<p>Saved; see {{ x }} &rarr; Today</p><p>{{ 'Great' if a else 'ok' }}</p>"
    found = [x for p in tr.template_text(src) for x in tr.check(p.text, tr.PAGE, rules=tr.T1_TEMPLATE_RULES)]
    assert sorted(rules(found)) == sorted([tr.SEMICOLON, tr.ARROW, tr.CHAT_PHRASE])


# ---------------------------------------------------------------- string constants

SOURCE = '''"""Module docstring: just a note."""
import re

# a comment: really
PATTERN = re.compile(r"(?!x)")
LABEL = ("Food entry "
         "changed")


class Thing:
    """Class docstring: please."""

    def method(self, x, n):
        """Method docstring: simply."""
        text = f"{x:.2f} kg and {'inner'} {n}"
        data = b"bytes"
        return text, "plain"


async def run():
    """Async docstring: easy."""
    return "after"
'''


def test_string_constants_skip_docstrings_and_comments():
    found = tr.string_constants(SOURCE)
    assert [c.text for c in found] == ["(?!x)", "Food entry changed", f"{tr.HOLE} kg and {tr.HOLE} {tr.HOLE}",
                                       "inner", "plain", "after"]


def test_string_constants_give_start_and_end_lines():
    found = {c.text: (c.line, c.end_line) for c in tr.string_constants(SOURCE)}
    assert found["Food entry changed"] == (6, 7)
    assert found["plain"] == (17, 17)


def test_a_docstring_is_only_the_first_statement():
    src = 'def f():\n    x = 1\n    "not a docstring"\n'
    assert [c.text for c in tr.string_constants(src)] == ["not a docstring"]


# ---------------------------------------------------------------- format specs


@pytest.mark.parametrize("line", [
    'a = f"{x:g} kg"', 'a = f"{x:,}"', 'a = f"{x:+g}"', 'a = f"{x:.1f}"', 'a = f"{x:02d}"',
    'a = f"{x:.0%}"', 'a = f"{x:>8.2f}"', 'a = f"{x:.{n}f}"', 'a = f"{x:5d}"',
    'a = "{:,}".format(x)', 'a = "{0:.2f} kg".format(x)', 'a = "{v:+g}".format(v=x)',
    'a = format(x, ".2f")', 'a = format(x, ",")',
    'a = "%.1f kg" % x', 'a = "%02d:%02d" % (m, s)', 'a = "%g" % x', 'a = "%(v).2f" % d',
    # a nested field, a zero pad with no type, a precision with no type
    'a = f"{x:{spec}}"', 'a = f"{s:02}"', 'a = f"{x:.2}"', 'a = f"{name:.80}"', 'a = "{:{w}}".format(x, w=3)',
    # a spec or template that is not a constant
    'a = format(x, spec)', 'a = str.format(template, x)', 'a = str.format("{:.1f}", x)',
])
def test_a_numeric_format_spec_is_found(line):
    assert tr.format_spec_lines(line + "\n") == [(1, line)]


@pytest.mark.parametrize("line", [
    'a = f"{x} kg"', 'a = f"{name:>10}"', 'a = f"{s:5}"', 'a = f"{s:0>5}"', 'a = f"{d:%Y-%m-%d}"',
    'a = f"{x!r}"', 'a = "{} kg".format(x)', 'a = "{:>4}".format(s)', 'a = "%s kg" % x', 'a = "%d sets" % n',
    'a = "50%"', 'a = d.strftime("%d %b")', 'a = format(x)', 'a = format(x, "")', 'log.info("%.1f", x)',
    'a = str.format("{} kg", x)', 'a = n % 60', 'a = template.format(x)',
    '# a = f"{x:g}"', 'a = 1  # f"{x:.2f}"',
])
def test_other_formatting_passes(line):
    assert tr.format_spec_lines(line + "\n") == []


def test_a_template_held_in_a_module_constant_is_read_where_it_is_used():
    src = ('ONE = "{:.1f} kg"\nTWO = "%02d:%02d"\nPLAIN = "{} kg"\nWORDS = "%s kg"\n'
           'def f(x, m, s):\n'
           '    a = ONE.format(x)\n'
           '    b = TWO % (m, s)\n'
           '    c = PLAIN.format(x)\n'
           '    d = WORDS % x\n'
           '    e = format(x, ONE[1:-4])\n'
           '    g = str.format(ONE, x)\n'
           '    h = str.format(PLAIN, x)\n')
    assert [n for n, _ in tr.format_spec_lines(src)] == [6, 7, 10, 11]


def test_a_template_bound_inside_a_function_is_not_held():
    src = 'def f(x):\n    ONE = "{:.1f}"\n    return ONE.format(x)\n'
    assert tr.format_spec_lines(src) == []


def test_a_format_spec_in_a_docstring_is_not_read():
    src = 'def f(x):\n    """Gives f"{x:.2f}" and "{:,}".format(x)."""\n    return x\n'
    assert tr.format_spec_lines(src) == []


def test_format_spec_lines_give_one_stripped_line_each_in_order():
    src = 'def f(x):\n    a = 1\n    return f"{x:g} and {x:,}"\n\nb = "%.1f" % 2\n'
    assert tr.format_spec_lines(src) == [(3, 'return f"{x:g} and {x:,}"'), (5, 'b = "%.1f" % 2')]


@pytest.mark.parametrize("spec, numeric", [
    ("g", True), (",", True), ("+g", True), (".1f", True), ("02d", True), ("_", True), ("e", True),
    ("02", True), (".2", True), (".80", True), ("{w}", True), (">{w}", True),
    ("", False), (">10", False), ("5", False), ("0>5", False), ("d", False), ("s", False), ("%Y", False),
    ("x", False),
])
def test_numeric_spec(spec, numeric):
    assert tr.numeric_spec(spec) is numeric


# ---------------------------------------------------------------- exemptions


def test_each_named_exemption_would_be_a_finding_without_it():
    for e in tr.EXEMPT_LINES:
        source = e.line.replace("return ", "x = ", 1) + "\n"
        if e.path == "app/services/common.py":
            assert tr.format_spec_lines(source), e
        else:
            found = [x for c in tr.string_constants(source) for x in tr.check(c.text, tr.SOURCE)]
            assert found, e
        assert tr.exempt(e.path, e.line)


def test_the_named_exemptions_are_the_contracts():
    assert {e.path for e in tr.EXEMPT_LINES} == {
        "app/links.py", "app/rules.py", "app/auth.py", "app/services/common.py"}
    assert all(e.reason for e in tr.EXEMPT_LINES)
    assert tr.exempt_characters("app/services/export.py") == {BOM}
    assert tr.exempt_characters("app/services/food.py") == set()


def test_exempt_normalises_the_path_and_the_whitespace():
    e = tr.EXEMPT_LINES[0]
    assert tr.exempt(tr.ROOT / "app" / "links.py", "    " + e.line.replace(" ", "  ") + "  ")
    assert tr.exempt(str(tr.ROOT / "app" / "links.py"), e.line)
    assert not tr.exempt("app/web.py", e.line)
    assert not tr.exempt(e.path, e.line + " # changed")


def test_kept_is_read_from_its_file(tmp_path):
    kept = tmp_path / "kept.py"
    assert tr.load_kept(kept) == ()
    kept.write_text('"""Lines lane A keeps."""\nKEPT = [\n    ("app/services/goals.py", "  msg = f\'{x:g}\'  "),\n]\n',
                    encoding="utf-8")
    assert tr.load_kept(kept) == (("app/services/goals.py", "msg = f'{x:g}'"),)
    assert tr.exempt_format("app/services/goals.py", "msg = f'{x:g}'", kept_path=kept)
    assert not tr.exempt_format("app/services/goals.py", "msg = f'{x:g}'", kept_path=tmp_path / "none.py")


def test_a_kept_line_keeps_its_format_spec_only(tmp_path):
    kept = tmp_path / "kept.py"
    kept.write_text("KEPT = [('app/services/goals.py', 'msg = f\"{x:g}\"')]\n", encoding="utf-8")
    assert tr.exempt_format("app/services/goals.py", 'msg = f"{x:g}"', kept_path=kept)
    assert not tr.exempt("app/services/goals.py", 'msg = f"{x:g}"', kept_path=kept)
    for e in tr.EXEMPT_LINES:  # a named line is exempt from every rule, its format spec included
        assert tr.exempt(e.path, e.line) and tr.exempt_format(e.path, e.line, kept_path=kept)


@pytest.mark.parametrize("body", ["KEPT = [('a.py',)]\n", "KEPT = 'a.py'\n", "OTHER = []\n", "KEPT = [(1, 'x')]\n"])
def test_a_malformed_kept_file_is_refused(tmp_path, body):
    kept = tmp_path / "kept.py"
    kept.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        tr.load_kept(kept)


def test_the_kept_file_is_lane_as_and_reads():
    assert tr.KEPT_PATH.relative_to(tr.ROOT).as_posix() == "tests/text_rules_kept.py"
    kept = tr.load_kept()  # the real file, when it exists, is well formed
    assert all(path.startswith("app/") for path, _ in kept)


# ---------------------------------------------------------------- text the person entered


def test_mask_entered_hides_values_the_person_gave():
    text = "Lunch: Roti special 240 kcal, Roti 120 kcal. Notes: felt strong; Rotisserie."
    masked = tr.mask_entered(text, ["Roti", "Roti special", "felt strong;", "", None, "  "])
    assert masked == f"Lunch: {tr.ENTERED} 240 kcal, {tr.ENTERED} 120 kcal. Notes: {tr.ENTERED} Rotisserie."
    assert tr.check(masked, tr.SUMMARY) == []


def test_mask_entered_hides_a_name_and_a_quoted_value():
    from app.rules import QUOTE_MAX, quote
    assert tr.mask_entered("Oats 300 kcal", ["Oats"]) == f"{tr.ENTERED} 300 kcal"
    long = "word " * 40
    cut = quote(long)
    assert len(cut) <= QUOTE_MAX and cut != long
    masked = tr.mask_entered(f"Description '{cut}' changed", [long])
    assert masked == f"Description '{tr.ENTERED}' changed"
    assert tr.check(masked, tr.MESSAGE) == []


@pytest.mark.parametrize("text, entered, masked", [
    ("80.5 kg, then 80 kg.", ["80"], f"80.5 kg, then {tr.ENTERED} kg."),
    ("Logged 2026-11-10 at 11", ["11"], f"Logged 2026-11-10 at {tr.ENTERED}"),
    ("1,500 kcal or 500", ["500"], f"1,500 kcal or {tr.ENTERED}"),
    ("At 10:30 and 30", ["30"], f"At 10:30 and {tr.ENTERED}"),
    ("Set 2.5 or 5.", ["5"], f"Set 2.5 or {tr.ENTERED}."),
    ("Roti, dal.", ["Roti", "dal"], f"{tr.ENTERED}, {tr.ENTERED}."),
])
def test_mask_entered_never_masks_inside_a_number_or_a_date(text, entered, masked):
    assert tr.mask_entered(text, entered) == masked


@pytest.mark.parametrize("value", ["-", "...", "()", "; ;"])
def test_mask_entered_refuses_a_value_with_no_letter_or_digit(value):
    with pytest.raises(ValueError):
        tr.mask_entered("a - b", [value])


# ---------------------------------------------------------------- these files


@pytest.mark.parametrize("name", ["text_rules.py", "test_text_rules.py", "test_docs.py"])
def test_these_files_write_every_banned_character_as_an_escape(name):
    text = (Path(__file__).parent / name).read_text(encoding="utf-8")
    assert all(ord(c) < 128 for c in text)


# ================================================================ T1: the sweep over the app
# Every template's text (every subfolder), every string constant in app/, docs/can-and-cant.md and
# README.md: the character allowlist, the word lists and the sentence shapes; semicolons in templates too.
# The two MCP files are text for Claude: the claude surface's characters. No numeric format spec in app/
# outside app/formatting.py, and no number formatting in a template.
# EXEMPT_LINES exempt a line from every rule, EXEMPT_CHARACTERS one character in one file, and lane A's
# KEPT a line's format spec only. An aria-hidden roll is never read. Docs are read a block at a time.

APP = tr.ROOT / "app"
FORMATTER = APP / "formatting.py"
DOCS = [tr.ROOT / "README.md", tr.ROOT / "docs" / "can-and-cant.md"]


def template_files(folder: Path) -> list[Path]:
    """Every template under folder, subfolders included."""
    return sorted(folder.rglob("*.html"))


def source_files(folder: Path) -> list[Path]:
    """Every Python file under folder, subfolders included."""
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts)


TEMPLATES = template_files(APP / "templates")
APP_SOURCES = source_files(APP)


def named_keys() -> set:
    """The (path, line text) pairs exempt from every T1 rule: EXEMPT_LINES."""
    return {(e.path, tr.norm_line(e.line)) for e in tr.EXEMPT_LINES}


def exempt_keys(kept_path=tr.KEPT_PATH) -> set:
    """The pairs that may keep a numeric format spec, read once per test: the named lines and KEPT."""
    return named_keys() | set(tr.load_kept(kept_path))


def _exempt(keys, path, lines, first, last=None) -> bool:
    return any((path, tr.norm_line(lines[n - 1])) in keys for n in range(first, (last or first) + 1)
               if 0 < n <= len(lines))


def template_findings(source: str, path: str, keys) -> list:
    """(line, Finding) for a template: each piece of its text, a title as a title, under T1's template rules."""
    lines = source.splitlines()
    out = []
    for piece in tr.template_text(source):
        if _exempt(keys, path, lines, piece.line):
            continue
        surface = tr.TITLE if piece.kind == "title" else tr.PAGE
        out += [(piece.line, f) for f in tr.check(piece.text, surface, rules=tr.T1_TEMPLATE_RULES)]
    return out


def constant_findings(source: str, path: str, keys) -> list:
    """(line, Finding) for a Python file's string constants. A constant is skipped when any line it spans
    is exempt. The file's EXEMPT_CHARACTERS are allowed. The MCP files are checked as text for Claude."""
    lines = source.splitlines()
    allow = tr.exempt_characters(path)
    surface = tr.source_surface(path)
    out = []
    for c in tr.string_constants(source):
        if _exempt(keys, path, lines, c.line, c.end_line):
            continue
        out += [(c.line, f) for f in tr.check(c.text, surface, rules=tr.T1_SOURCE_RULES, allow=allow)]
    return out


def format_findings(source: str, path: str, keys) -> list:
    """(line, Finding) for each line of a Python file that formats a number with a format spec."""
    if path == tr.norm_path(FORMATTER):
        return []
    return [(n, tr.Finding(tr.FORMAT_SPEC, text, text)) for n, text in tr.format_spec_lines(source)
            if (path, tr.norm_line(text)) not in keys]


def template_format_findings(source: str, path: str, keys) -> list:
    """(line, Finding) for each line of a template that formats or rounds a number itself."""
    return [(n, tr.Finding(tr.FORMAT_SPEC, text, text)) for n, text in tr.template_format_lines(source)
            if (path, tr.norm_line(text)) not in keys]


def doc_findings(text: str, path: str, keys) -> list:
    """(line, Finding) for a doc, a block at a time (a paragraph, a list item, a row), so a phrase wrapped
    across lines is found. Each finding names the line its match starts on."""
    lines = text.splitlines()
    out = []
    for block in tr.doc_blocks(text):
        flat = block.text.replace("\n", " ")  # the same length: an offset in flat is an offset in block
        start = {}
        for f in tr.check(flat, tr.DOC):
            at = flat.find(f.match, start.get(f.match, 0))
            start[f.match] = at + 1
            n = block.line + block.text.count("\n", 0, max(at, 0))
            if not _exempt(keys, path, lines, n):
                out.append((n, f))
    return sorted(out, key=lambda x: x[0])


def stale_exemptions(root: Path = tr.ROOT, kept_path=tr.KEPT_PATH, named=tr.EXEMPT_LINES,
                     characters=tr.EXEMPT_CHARACTERS) -> list[str]:
    """Every exemption that no longer holds: a named line gone from its file, a KEPT line that is no
    longer a format spec finding, a character no string constant of its file carries."""
    def source(path):
        target = root / path
        return target.read_text(encoding="utf-8") if target.exists() else ""

    out = []
    for e in named:
        if tr.norm_line(e.line) not in {tr.norm_line(x) for x in source(e.path).splitlines()}:
            out.append(f"named line gone: {e.path}: {ascii(e.line)}")
    for path, line in tr.load_kept(kept_path):
        found = {tr.norm_line(t) for _, t in tr.format_spec_lines(source(path))} if source(path) else set()
        if line not in found:
            out.append(f"kept line keeps no format spec: {path}: {ascii(line)}")
    for path, char in characters:
        text = source(path)
        if not text or not any(char in c.text for c in tr.string_constants(text)):
            out.append(f"exempt character not in the file: {path}: {ascii(char)}")
    return out


def report(path: str, found: list) -> str:
    """One line a finding: the file, the line, the rule, the text. ASCII, so any console prints it."""
    return "\n".join(f"{path}:{n} {f.rule}: {ascii(f.match)} in {ascii(f.context)}" for n, f in found)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", TEMPLATES, ids=tr.norm_path)
def test_t1_template_text(path):
    name = tr.norm_path(path)
    found = template_findings(_read(path), name, named_keys())
    assert not found, "\n" + report(name, found)


@pytest.mark.parametrize("path", TEMPLATES, ids=tr.norm_path)
def test_t1_no_number_format_in_a_template(path):
    name = tr.norm_path(path)
    found = template_format_findings(_read(path), name, exempt_keys())
    assert not found, "\n" + report(name, found)


@pytest.mark.parametrize("path", APP_SOURCES, ids=tr.norm_path)
def test_t1_string_constants(path):
    name = tr.norm_path(path)
    found = constant_findings(_read(path), name, named_keys())
    assert not found, "\n" + report(name, found)


@pytest.mark.parametrize("path", [p for p in APP_SOURCES if p != FORMATTER], ids=tr.norm_path)
def test_t1_no_number_format_outside_the_formatter(path):
    name = tr.norm_path(path)
    found = format_findings(_read(path), name, exempt_keys())
    assert not found, "\n" + report(name, found)


@pytest.mark.parametrize("path", DOCS, ids=tr.norm_path)
def test_t1_docs(path):
    name = tr.norm_path(path)
    found = doc_findings(_read(path), name, named_keys())
    assert not found, "\n" + report(name, found)


def test_t1_sweeps_every_file_it_should():
    names = {tr.norm_path(p) for p in TEMPLATES + APP_SOURCES + DOCS}
    for expected in ("app/templates/base.html", "app/templates/day.html", "app/web.py", "app/charts.py",
                     "app/mcp_server.py", "app/services/goals.py", "app/formatting.py", "README.md",
                     "docs/can-and-cant.md"):
        assert expected in names
    assert len(TEMPLATES) >= 10 and len(APP_SOURCES) >= 30
    assert FORMATTER.exists()
    every = {p for p in (APP / "templates").rglob("*") if p.is_file() and p.suffix == ".html"}
    assert set(TEMPLATES) == every


def test_t1_every_exemption_still_holds():
    """A stale exemption fails: delete it, or fix its text."""
    stale = stale_exemptions()
    assert not stale, "\n" + "\n".join(stale)


# ---------------------------------------------------------------- T1's helpers, on hand-made files

NO_KEYS: set = set()


def found_rules(found):
    return [f.rule for _, f in found]


def test_template_findings_run_the_template_rules_with_lines():
    src = ('{% block title %}Day ' + "{{ x }}" + ' \u00b7 Spotter{% endblock %}\n'
           '<p>Saved; see Today.</p>\n<p>Just {{ y }}</p>\n'
           '<p>{{ "Great" if a else "Fine" }}</p>\n<span aria-hidden="true" class="roll">Really!</span>\n'
           "<p>You can't, start_value, 2026-11-10, " + " ".join(["word"] * 30) + ".</p>")
    found = template_findings(src, "app/templates/x.html", NO_KEYS)
    assert [(n, f.rule) for n, f in found] == [(2, tr.SEMICOLON), (3, tr.FILLER), (4, tr.CHAT_PHRASE)]


def test_template_findings_honour_an_exempt_line():
    src = "<p>One; two</p>\n<p>Three; four</p>"
    found = template_findings(src, "app/templates/x.html", {("app/templates/x.html", "<p>One; two</p>")})
    assert [n for n, _ in found] == [2]


def test_template_findings_hold_the_title_to_its_allowlist():
    found = template_findings("<p>Day \u00b7 Spotter</p>", "app/templates/x.html", NO_KEYS)
    assert found_rules(found) == [tr.CHARACTER]


def test_constant_findings_skip_docstrings_comments_and_exempt_lines():
    src = ('"""Docstring: just."""\n'
           '# a comment: really\n'
           'A = "Saved - see Today"\n'
           'B = "kept; a semicolon in source passes"\n'
           'C = ("Simply "\n'
           '     "logged")\n'
           'D = "\ufeff"\n'
           'E = "mark \u2026"\n')
    found = constant_findings(src, "app/x.py", NO_KEYS)
    assert [(n, f.rule) for n, f in found] == [(3, tr.DASH), (5, tr.FILLER), (7, tr.CHARACTER), (8, tr.ELLIPSIS)]
    keys = {("app/x.py", 'A = "Saved - see Today"'), ("app/x.py", '"logged")')}
    found = constant_findings(src, "app/x.py", keys)
    assert [(n, f.rule) for n, f in found] == [(7, tr.CHARACTER), (8, tr.ELLIPSIS)]
    found = constant_findings(src, "app/services/export.py", {("app/services/export.py", k) for _, k in keys})
    assert [(n, f.rule) for n, f in found] == [(8, tr.ELLIPSIS)]


def test_format_findings_skip_the_formatter_and_exempt_lines():
    src = 'a = f"{x:g} kg"\nb = f"{y:,}"\n'
    assert [n for n, _ in format_findings(src, "app/x.py", NO_KEYS)] == [1, 2]
    assert format_findings(src, "app/formatting.py", NO_KEYS) == []
    assert [n for n, _ in format_findings(src, "app/x.py", {("app/x.py", 'b = f"{y:,}"')})] == [1]


def test_format_findings_honour_kept(tmp_path):
    kept = tmp_path / "kept.py"
    kept.write_text("KEPT = [('app/x.py', 'b = f\"{y:,}\"')]\n", encoding="utf-8")
    src = 'a = f"{x:g} kg"\nb = f"{y:,}"\n'
    assert [n for n, _ in format_findings(src, "app/x.py", exempt_keys(kept))] == [1]


def test_doc_findings_name_the_line_each_finding_is_on():
    text = "# Doc\n\nOne line; semicolons pass in a doc.\nThen just log it.\nA " + EM + " dash.\n"
    found = doc_findings(text, "README.md", NO_KEYS)
    assert [(n, f.rule) for n, f in found] == [(4, tr.FILLER), (5, tr.DASH)]


def test_doc_findings_find_a_phrase_wrapped_across_lines():
    text = ("A paragraph that ends a line with note\nthat it wraps, and in order\nto log.\n\n"
            "- An item, of\n  course wrapped.\n- Another item that says not only\n  this but that.\n")
    found = doc_findings(text, "README.md", NO_KEYS)
    assert [(n, f.rule) for n, f in found] == [(1, tr.FILLER), (2, tr.AI_VOCABULARY), (5, tr.CHAT_PHRASE),
                                                (7, tr.SHAPE)]
    keys = {("README.md", "that it wraps, and in order")}
    assert [n for n, _ in doc_findings(text, "README.md", keys)] == [1, 5, 7]


def test_doc_findings_keep_blocks_apart():
    text = "Ends with note\n\nthat starts a paragraph.\n# In\nsummary under a heading\n| a | of |\n| course | b |\n"
    assert doc_findings(text, "README.md", NO_KEYS) == []


def test_doc_blocks_split_paragraphs_items_headings_rows_and_code():
    text = "# Title\nOne\ntwo\n\n- item\n  wrapped\n1. next\n| a |\n```\ncode\nmore\n```\n> quote\n"
    assert tr.doc_blocks(text) == [
        tr.DocBlock(1, "# Title"), tr.DocBlock(2, "One\ntwo"), tr.DocBlock(5, "- item\n  wrapped"),
        tr.DocBlock(7, "1. next"), tr.DocBlock(8, "| a |"), tr.DocBlock(9, "```"), tr.DocBlock(10, "code"),
        tr.DocBlock(11, "more"), tr.DocBlock(12, "```"), tr.DocBlock(13, "> quote")]


def test_template_files_and_the_guard_reach_a_subfolder(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("a.html", "sub/b.html", "sub/c.txt"):
        (tmp_path / name).write_text("<p>x</p>", encoding="utf-8")
    assert [p.relative_to(tmp_path).as_posix() for p in template_files(tmp_path)] == ["a.html", "sub/b.html"]
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    for name in ("m.py", "pkg/n.py", "pkg/__pycache__/o.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    assert [p.relative_to(tmp_path).as_posix() for p in source_files(tmp_path)] == ["m.py", "pkg/n.py"]


def test_template_format_findings_skip_an_exempt_line():
    src = "<p>{{ x|round }}</p>\n<p>{{ y|format }}</p>"
    assert [n for n, _ in template_format_findings(src, "app/templates/x.html", NO_KEYS)] == [1, 2]
    keys = {("app/templates/x.html", "<p>{{ y|format }}</p>")}
    assert [n for n, _ in template_format_findings(src, "app/templates/x.html", keys)] == [1]


def test_the_mcp_files_are_checked_as_text_for_claude():
    src = f'A = "Roti {TIMES} 2"\nB = "80{NBSP}kg"\nC = "Progress {MIDDOT} Spotter"\n'
    assert constant_findings(src, "app/web.py", NO_KEYS) == []
    for path in ("app/mcp_server.py", "app/mcp_schemas.py"):
        assert [(n, f.rule) for n, f in constant_findings(src, path, NO_KEYS)] == [(2, tr.CHARACTER), (3, tr.CHARACTER)]
        assert tr.source_surface(path) == tr.CLAUDE
    assert tr.source_surface("app/web.py") == tr.SOURCE
    assert tr.source_surface(tr.ROOT / "app" / "mcp_server.py") == tr.CLAUDE


def test_stale_exemptions_are_found(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text('x = f"{v:g}"\ny = "plain"\nBOM = "\\ufeff"\n', encoding="utf-8")
    kept = tmp_path / "kept.py"
    named = (tr.Exemption("app/a.py", "y = 'plain'", "gone"), tr.Exemption("app/a.py", 'y = "plain"', "here"))
    kept.write_text("KEPT = [('app/a.py', 'x = f\"{v:g}\"'), ('app/a.py', 'y = \"plain\"'), "
                    "('app/gone.py', 'z = 1')]\n", encoding="utf-8")
    chars = (("app/a.py", BOM), ("app/a.py", EM), ("app/gone.py", BOM))
    assert stale_exemptions(tmp_path, kept, named, chars) == [
        "named line gone: app/a.py: \"y = 'plain'\"",
        "kept line keeps no format spec: app/a.py: 'y = \"plain\"'",
        "kept line keeps no format spec: app/gone.py: 'z = 1'",
        "exempt character not in the file: app/a.py: '\\u2014'",
        "exempt character not in the file: app/gone.py: '\\ufeff'"]
    assert stale_exemptions(tmp_path, tmp_path / "none.py", named[1:], chars[:1]) == []


def test_a_kept_line_is_held_to_every_rule_but_its_format_spec(tmp_path):
    kept = tmp_path / "kept.py"
    kept.write_text("KEPT = [('app/x.py', 'a = f\"Just {x:g} kg\"')]\n", encoding="utf-8")
    src = 'a = f"Just {x:g} kg"\n'
    assert format_findings(src, "app/x.py", exempt_keys(kept)) == []
    assert found_rules(constant_findings(src, "app/x.py", named_keys())) == [tr.FILLER]


def test_report_names_file_line_rule_and_text_in_ascii():
    found = [(5, tr.Finding(tr.ARROW, LARR, LARR + " Foods"))]
    assert report("app/templates/x.html", found) == "app/templates/x.html:5 arrow: '\\u2190' in '\\u2190 Foods'"
    assert all(ord(c) < 128 for c in report("x", found))


def test_exempt_keys_hold_the_named_lines_and_kept(tmp_path):
    keys = exempt_keys(tmp_path / "none.py")
    assert {p for p, _ in keys} == {e.path for e in tr.EXEMPT_LINES}
    kept = tmp_path / "kept.py"
    kept.write_text("KEPT = [('app/y.py', '  x = 1  ')]\n", encoding="utf-8")
    assert ("app/y.py", "x = 1") in exempt_keys(kept)
    assert ("app/y.py", "x = 1") not in named_keys()
    assert named_keys() == {(e.path, tr.norm_line(e.line)) for e in tr.EXEMPT_LINES}
