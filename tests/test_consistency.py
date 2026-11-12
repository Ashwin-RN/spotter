"""T7, the step 2 part: a figure on a page is the figure Claude reads, printed by the one formatter.

For each figure that is both on Progress and in get_trends (and get_progress), and both on Goals and in
get_goals, the page's text holds exactly show(value, quantity, page=True) of the result's number, inside
the figure's own element. Text inside an SVG chart and a label built in a service are compared with
page=False. The chart's description never widens.

Run on the real services, on the sample account and on an edge account, so show on both sides holds
whatever precision a service field carries. An open goal card reads goal_charts, unrounded: its cells are held
to goal_charts' own fields, and its now figure to the one plan_line prints.
"""

from __future__ import annotations

import html
import re
from datetime import date

import pytest
from sqlalchemy import func, select

from app import db, links, services, users
from app.formatting import day, day_utc, days, field, show, widen
from app.models import Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood, User
from app.services import lifts

from . import text_rules as tr
from .test_pages_text import edge_account, sample_account
from .test_web import FIXED_NOW, body_weight_card, client, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

ACCOUNTS = {"sample": sample_account, "edge": edge_account}
# A goal's figures take the goal unit's quantity (contract section C).
GOAL_QUANTITY = {"bodyweight": "bodyweight_derived", "lift": "estimated_max", "cardio_best": "goal_time"}


def text_of(markup: str) -> str:
    """The text a person reads in a piece of markup, one string."""
    return " ".join(p.text for p in tr.html_text(markup))


def holds(text: str, figure: str) -> bool:
    """The figure stands whole in the text: not the end of a longer number, not the start of one."""
    return re.search(rf"(?<![\d.,:+-]){re.escape(figure)}(?!\d|,\d|\.\d|:\d)", text) is not None


def facts(markup: str) -> dict[str, str]:
    """{dt text: dd text} of every dl in the markup."""
    return {text_of(dt): text_of(dd) for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", markup, re.S)}


def figure(text: str, value, quantity: str, page: bool = True, **kw) -> None:
    shown = show(value, quantity, page=page, **kw)
    assert holds(text, shown), (shown, text)


@pytest.fixture(params=sorted(ACCOUNTS))
def account(request, client):
    sign_in(client)
    user_id = only_user().id
    ACCOUNTS[request.param](user_id)
    return user_id


# ---------------------------------------------------------------- Progress and get_trends

def trends_table(page: str) -> dict[str, list[tuple[str, str]]]:
    """Progress' Trends table: {quantity: [(value, remark), ...]}, each cell's text as a person reads it, the rows of
    one quantity in their order."""
    card = re.search(r'<section class="card trends"[^>]*>(.*?)</section>', page, re.S).group(1)
    out: dict[str, list[tuple[str, str]]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", card, re.S)[1:]:  # the header row first
        quantity, value, remark = (text_of(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S))
        out.setdefault(quantity, []).append((value, remark))
    return out


def test_the_trends_card_holds_get_trends_figures(client, account):
    trends = services.get_trends(account)
    page = client.get("/progress").text
    table = trends_table(page)
    bw, cal, ses, car, m = (trends["bodyweight"], trends["calories"], trends["sessions"], trends["cardio"],
                            trends["maintenance"])
    assert bw is not None
    figure(table["Smoothed trend"][0][0], bw["smoothed"], "bodyweight_derived")
    if bw["now"] is not None:
        [(now, _)] = table.get("7-day average", []) + table.get("Latest weigh-in", [])
        figure(now, bw["now"], "bodyweight_derived")
    rate, rate_remark = table["Weight rate"][0]
    if bw["rate_per_week"] is not None:
        assert holds(rate, f"{show(bw['rate_per_week'], 'rate', page=True)} kg a week")
        window = bw["rate_window"]
        assert rate_remark == days(window["from"], window["to"], trends["today"])
    figure(table["Weigh-ins"][0][0], bw["weigh_ins_28d"], "count")
    assert cal is not None
    (week, week_remark), (month, month_remark) = table["Eaten a day"]
    if cal["avg_7d"] is not None:
        figure(week, cal["avg_7d"], "kcal")
        # The whole remark, so a count is never matched by the 7 of "Last 7 days" or by the other window's count.
        assert week_remark == f"Last 7 days, {show(cal['days_logged_7d'], 'count')} logged"
    figure(month, cal["avg_28d"], "kcal")
    assert month_remark == f"Last 28 days, {show(cal['days_logged_28d'], 'count')} logged"
    if cal["target_kcal"] is not None:
        figure(table["Calorie target"][0][0], cal["target_kcal"], "kcal")
    figure(table["From estimates"][0][0], cal["estimated_share_pct_28d"], "percent")
    figure(table["Lift sessions"][0][0], ses["this_week"], "count")
    assert table["Lift sessions a week"][0][0] == f"{show(ses['per_week_4w'], 'count_avg', page=True)}\u00a0a week"
    if ses["target_per_week"] is not None:
        figure(table["Lift sessions"][0][1], ses["target_per_week"], "count")
    figure(table["Cardio"][0][0], car["minutes_this_week"], "week_minutes")
    figure(table["Cardio a week"][0][0], car["minutes_per_week_4w"], "week_minutes")
    if car["target_minutes"] is not None:
        figure(table["Cardio"][0][1], car["target_minutes"], "week_minutes")
    if m:
        value, remark = table["Maintenance"][0]
        figure(value, m["kcal"], "maintenance")
        figure(remark, m["days_logged"], "count")
        assert holds(remark, days(m["from"], m["to"], trends["today"]))


def test_the_body_weight_card_and_chart_hold_the_same_figures(client, account):
    trends = services.get_trends(account)
    page = client.get("/progress").text
    card = body_weight_card(page)
    latest = trends["bodyweight"]["series"][-1]
    callout = text_of(re.search(r"<div><dt>Latest</dt><dd>(.*?)</dd></div>", card, re.S).group(1))
    figure(callout, latest["kg"], "weigh_in")  # a weigh-in, as entered: the Trends row is the derived figure
    assert holds(callout, day(latest["date"], trends["today"]))
    trend = text_of(re.search(r'<div class="is-key"><dt>Trend</dt><dd>(.*?)</dd></div>', card, re.S).group(1))
    figure(trend, trends["bodyweight"]["smoothed"], "bodyweight_derived")  # the derived figure, at 1 dp
    desc = html.unescape(re.search(r'<desc id="bodyweight-chart-desc">(.*?)</desc>', page, re.S).group(1))
    figure(desc, trends["bodyweight"]["smoothed"], "bodyweight_derived", page=False)  # never widened
    figure(desc, latest["kg"], "weigh_in", page=False)
    assert "\u00a0" not in desc


def test_the_lift_rows_hold_get_progress_figures(client, account):
    rows = services.get_progress(account)["lifts"]
    page = client.get("/progress").text
    assert rows
    for row in rows:
        cells = re.search(rf'<td(?: class="lead")?><a href="/progress/[^"]+">{re.escape(row["exercise"])}</a></td>'
                          r'(.*?)</tr>', page, re.S).group(1)
        text = text_of(cells)
        lift = services.get_progress(account, row["exercise"])["lift"]
        if row["current_estimated_max_kg"] is not None:
            figure(text, row["current_estimated_max_kg"], "estimated_max")
        else:  # the two reasons, told apart by lifts.history's reps_only; never a 0 kg
            bare = reps_only(account).get(row["exercise"])
            assert ("Needs added weight" if bare else "Needs a weighted set of 1 to 10 reps") in text
            assert not re.search(r"(?<![\d.,])0 kg", text)
            if bare:
                assert holds(text, f"{show(bare['reps'], 'reps')} reps")
        best = lift["best_set"]
        if best:  # the best set, read from lifts.history on the page and from get_progress here
            assert holds(text, f"{show(best['weight_kg'], 'weight_kg', page=True)} × {show(best['reps'], 'reps')}")
        if row["slope_per_week"] is not None:  # the trend, get_progress' slope
            assert holds(text, f"{show(row['slope_per_week'], 'rate', page=True)} kg a week")
        else:
            assert "kg a week" not in text


def reps_only(user_id: int) -> dict:
    """lifts.history's reps_only, the read the page's no-added-weight rows come from."""
    with db.new_session() as s:
        return lifts.history(s, user_id, users.today_for(s.get(User, user_id)))["reps_only"]


def labelled_cells(cells: str) -> dict[str, str]:
    """A row table's cells by their data-label, each cell's text as a person reads it."""
    return {label: text_of(cell) for label, cell in re.findall(r'<td[^>]*data-label="([^"]+)"[^>]*>(.*?)</td>', cells,
                                                               re.S)}


def test_the_cardio_rows_hold_get_cardio_figures(client, account):
    """Each Cardio row, by its activity's name, cell by cell by data-label: the last 28 days' sessions and minutes,
    the fastest pace as get_cardio gives it, and the last day, each as the read has it. So Sessions and Time
    swapped, or a count matched inside a day, fail."""
    result = services.get_cardio(account)
    page = client.get("/progress").text
    assert result["activities"]
    paced = any(a["fastest_pace"] for a in result["activities"])
    for a in result["activities"]:
        cells = re.search(rf'<td class="lead">{re.escape(a["exercise"])}</td>(.*?)</tr>', page, re.S).group(1)
        want = {"Sessions": show(a["sessions_28d"], "count", page=True),
                "Time (min)": show(a["minutes_28d"], "week_minutes", page=True, unit=False),
                **({"Fastest pace (per km)": a["fastest_pace"] or ""} if paced else {}),
                "Last": day(a["last_date"], result["today"])}
        assert labelled_cells(cells) == want, a["exercise"]
        assert f'href="/day/{a["last_date"]}"' in cells


def lift_tile(page: str, variant: str) -> str:
    """A lift page's tile of that variant, its text as a person reads it."""
    return text_of(re.search(rf'<div class="tile tile-{variant}(?: is-empty)?">(.*?)</div>', page, re.S).group(1))


def lift_list(page: str, title_id: str) -> list[str]:
    """Each line of the lift page's card named by that h2 id, its text as a person reads it."""
    card = re.search(rf'aria-labelledby="{title_id}">(.*?)</section>', page, re.S).group(1)
    return [text_of(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", card, re.S)]


@pytest.mark.parametrize("name", sorted(ACCOUNTS))
def test_a_lifts_page_holds_its_figures(client, monkeypatch, name):
    """Every lift's page, on both accounts at the fixed day: each tile, the chart card's figure, each best and each
    session, against get_progress with the lift and lifts.history's reps_only, each through its own filter, every day
    worded against get_progress' today. A lift with no estimated max says why, and never 0 kg."""
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    sign_in(client)
    account = only_user().id
    ACCOUNTS[name](account)
    rows = services.get_progress(account)["lifts"]
    assert rows
    for row in rows:
        result = services.get_progress(account, row["exercise"])
        lift, today = result["lift"], result["today"]
        page = client.get(f"/progress/{links.slug(row['exercise'])}").text
        top, best, sessions = (lift_tile(page, v) for v in ("max", "best", "sessions"))
        bare = reps_only(account).get(row["exercise"])
        if lift["current_estimated_max_kg"] is not None:
            figure(top, lift["current_estimated_max_kg"], "estimated_max", unit=False)
            chart = text_of(re.search(r'<p class="figure">(.*?)</p>', page, re.S).group(1))
            figure(chart, lift["current_estimated_max_kg"], "estimated_max")
        else:  # the Lifts card's two reasons, told apart by reps_only
            assert top.endswith("Needs added weight" if bare else "Needs a weighted set of 1 to 10 reps")
            assert '<p class="figure">' not in page
        assert not re.search(r"(?<![\d.,])0\s?kg", text_of(page))
        if lift["best_set"]:
            b = lift["best_set"]
            figure(best, b["weight_kg"], "weight_kg", unit=False)
            assert holds(best, f"kg \u00d7 {show(b['reps'], 'reps')}") and holds(best, day(b["date"], today))
        elif bare:
            assert holds(best, f"{show(bare['reps'], 'reps')} reps") and holds(best, day(bare["date"], today))
        else:  # neither a best set nor reps_only: the empty tile says why
            assert best.endswith("No weighted set of 1 to 10 reps yet"), best
        figure(sessions, lift["sessions"], "count")
        assert holds(sessions, f"last trained {day(lift['last_trained'], today)}")
        want = []
        for p in reversed(lift["bests"]):
            q = "estimated_max" if p["kind"] == "estimated_max" else "weight_kg"
            wide = q == "estimated_max" and show(p["value"], q) == show(p["previous"], q)
            want.append(f"{day(p['date'], today)}: {lifts.BEST_LABELS[p['kind']]} {show(p['value'], q, page=True, widened=wide)}, "
                        f"up from {show(p['previous'], q, page=True, widened=wide)}")
        assert lift_list(page, "bests-title") == want
        assert lift_list(page, "sessions-title") == [
            f"{day(p['date'], today)}: estimated max {show(p['estimated_max_kg'], 'estimated_max', page=True)}"
            for p in reversed(lift["estimated_max_over_time"])]


# ---------------------------------------------------------------- Goals, get_goals and goal_charts

# By projection's tag words, by status (section L, Goals).
STATUS_WORDS = {"reached": "Reached", "on track": "On track", "behind": "Behind", "not enough data": "Not enough data"}
NOW_FIGURE = r'<span class="big">(.*?)</span> <span class="figure-label">'


def test_each_goal_holds_get_goals_figures(client, account):
    result = services.get_goals(account, True)
    today, zone = result["today"], only_user().time_zone
    page = client.get("/goals").text
    assert result["goals"]
    for g in result["goals"]:
        item = re.search(rf'<li class="goal" id="goal-{g["goal_id"]}">(.*?)</li>', page, re.S).group(1)
        text, card = text_of(item), facts(item)
        q = GOAL_QUANTITY.get(g["kind"]) or ("bpm" if g["unit"] == "bpm" else "cm")
        assert html.unescape(g["label"]) in text  # a label built in the service: plain spaces
        assert holds(text, f"by {day(g['target_date'], today)}")
        started = goal_cells(page, g["goal_id"])["Started"]  # the figure, then its day under it in span.sub
        figure(started[0], g["start_value"], q)
        assert started[1:] == [day(g["start_date"], today)]
        if g["latest"] is not None:  # get_goals' latest, rounded: the now figure prints it, or the widened plan_line's
            now = text_of(re.search(NOW_FIGURE, item, re.S).group(1))
            assert holds(now, show(g["latest"], q, page=True)) or holds(g["plan_line"], now.replace("\u00a0", " "))
        if g["vs_plan"] not in (None, "on the line"):  # Plan line today: the plan figure plan_line prints (rule 2)
            assert holds(g["plan_line"], card["Plan line today"].replace("\u00a0", " ")), card["Plan line today"]
        else:
            assert "Plan line today" not in card or g["vs_plan"] is None
        pace = "time_pace" if g["kind"] == "cardio_best" else "rate"
        for name, value in (("Needed", g["needed_per_week"]), ("So far", g["actual_per_week"])):
            if value is None:
                continue
            words = "" if pace == "time_pace" else f"\u00a0{g['unit']} a week"
            assert holds(card[name], show(value, pace, page=True) + words), (name, card[name])
        if g["projected_date"] and g["status"] != "reached":
            assert holds(card["At this pace"], day(g["projected_date"], today))
        assert STATUS_WORDS[g["status"]] in text
    for g in result["closed"]:
        item = re.search(rf'<li class="entry-line" id="goal-{g["goal_id"]}">(.*?)</li>', page, re.S).group(1)
        if g["closed_at"]:
            assert holds(text_of(item), f"Closed {day_utc(g['closed_at'], zone, today)}")


def goal_cells(page: str, goal_id: int) -> dict[str, list[str]]:
    """One open goal card's cells, each its own element's text as a person reads it: the head's label and date, the
    now figure and its words, the plan box's heading and each line under it, the tag and what it means, each fact's
    value and the line under it (span.sub), and the chart's desc. Nothing is read from a row's joined text."""
    item = re.search(rf'<li class="goal" id="goal-{goal_id}">(.*?)</li>', page, re.S).group(1)
    plan = re.search(r'<dt>Against the plan line</dt>\s*<dd( class="plain")?>(.*?)</dd>', item, re.S)
    projection = re.search(r"<dt>By projection</dt>\s*<dd>(.*?)</dd>", item, re.S).group(1)

    def cells(pattern: str, markup: str = item) -> list[str]:
        return [text_of(m) for m in re.findall(pattern, markup, re.S)]

    plain = bool(plan.group(1))
    return {
        "label": cells(r"<h2><strong>(.*?)</strong></h2>"), "by": cells(r'<p class="goal-by">(.*?)</p>'),
        "now": cells(NOW_FIGURE), "now_words": cells(r'<span class="figure-label">(.*?)</span>'),
        "plain": [text_of(plan.group(2))] if plain else [],
        "heading": [] if plain else [text_of(plan.group(2).split("<span", 1)[0])],
        "plan_lines": [] if plain else cells(r'<span class="means">(.*?)</span>', plan.group(2)),
        "tag": cells(r'<span class="tag tag-status[^"]*">(.*?)</span>', projection),
        "means": cells(r'<span class="means">(.*?)</span>', projection),
        "desc": cells(rf'<desc id="chart-goal-{goal_id}-desc">(.*?)</desc>'),
        **{dt: [text_of(dd.split("<span", 1)[0])] + cells(r'<span class="sub">(.*?)</span>', dd)
           for dt, dd in re.findall(r'<dt>(Started|Plan line today|Needed|So far|At this pace)</dt>'
                                    r'<dd(?: class="words")?>(.*?)</dd>', item, re.S)},
    }


@pytest.mark.parametrize("name", sorted(ACCOUNTS))
def test_each_goal_card_holds_goal_charts_figures_cell_by_cell(client, monkeypatch, name):
    """Every open goal card on both accounts at the fixed day, cell by cell, against goal_charts' own unrounded
    fields: each figure through show with page=True, the now figure widened exactly when formatting.widen is (rule
    2) and so the one plan_line prints, the plan box's sentence the service's plan_line as written, and every day
    worded against goal_charts' today."""
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    sign_in(client)
    account = only_user().id
    ACCOUNTS[name](account)
    result = services.goal_charts(account)
    today = result["today"]
    page = client.get("/goals").text
    assert result["goals"]
    for g in result["goals"]:
        cells = goal_cells(page, g["goal_id"])
        q = GOAL_QUANTITY.get(g["kind"]) or ("bpm" if g["unit"] == "bpm" else "cm")
        assert cells["label"] == [html.unescape(g["label"])] and cells["by"] == [f"by {day(g['target_date'], today)}"]
        assert cells["tag"] == [STATUS_WORDS[g["status"]]]
        assert cells["Started"] == [show(g["start_value"], q, page=True), day(g["start_date"], today)]
        pace = "time_pace" if g["kind"] == "cardio_best" else "rate"
        for fact, value, missing in (("Needed", g["needed_per_week"], "Not worked out yet"),
                                     ("So far", g["actual_per_week"], "Not enough data yet")):
            words = "" if pace == "time_pace" else f"\u00a0{g['unit']} a week"
            assert cells[fact] == ([show(value, pace, page=True) + words] if value is not None else [missing]), fact
        if g["status"] == "reached":
            assert cells["At this pace"] == ["Reached"]
        elif g["projected_date"]:
            assert cells["At this pace"] == [day(g["projected_date"], today), "reached"]
        else:
            assert cells["At this pace"] == ["No date yet"]
        # The chart's desc (T7): the start unwidened, then plan_line word for word, then the target.
        said = g["plan_line"] or "Nothing logged yet."
        start = f"Started {show(g['start_value'], q)} on {day(g['start_date'], today)}."
        assert len(cells["desc"]) == 1 and cells["desc"][0].startswith(f"{start} {said} Target "), cells["desc"]
        wide = g["latest"] is not None and widen([g["latest"], g["should_be_today"]], q)
        if g["vs_plan"] == "on the line":  # rule 3
            assert "Plan line today" not in cells
        else:  # rule 2: the plan figure plan_line prints
            assert cells["Plan line today"] == [show(g["should_be_today"], q, page=True, widened=wide)]
            if g["plan_line"]:
                assert holds(g["plan_line"], cells["Plan line today"][0].replace("\u00a0", " "))
        if g["latest"] is None:
            assert cells["now"] == [] and cells["now_words"] == ["Nothing logged yet"]
            assert cells["plain"] == ["Nothing logged yet"] and cells["heading"] == []
            continue
        assert cells["now"] == [show(g["latest"], q, page=True, widened=wide)]
        assert holds(g["plan_line"], cells["now"][0].replace("\u00a0", " "))
        assert cells["plan_lines"][0] == g["plan_line"]
        days = g["days_ahead"]
        assert cells["plan_lines"][1:] == ([f"{show(abs(days), 'count')}\u00a0{'day' if abs(days) == 1 else 'days'} "
                                            f"{g['vs_plan']}"] if days else [])
        assert len(cells["heading"]) == len(cells["means"]) == len(cells["now_words"]) == 1


# ---------------------------------------------------------------- Foods and list_saved_foods (pass B15)

def saved_food_rows(page: str) -> tuple[list[str], list[list[str]]]:
    """Foods' Saved foods table: its headers, then each row's cells in order, each cell's text as a person reads it."""
    card = re.search(r'<section class="card saved-foods"[^>]*>(.*?)</section>', page, re.S).group(1)
    headers = [text_of(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", card, re.S)]
    body = re.search(r"<tbody>(.*?)</tbody>", card, re.S).group(1)
    return headers, [[text_of(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                     for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]


def test_each_saved_food_row_holds_list_saved_foods_figures_cell_by_cell(client, account):
    """Every live saved food, in list_saved_foods' order, cell by cell under its header: the name and its sample tag,
    the serving as written, kcal_per_serving, times_logged as a count and grams_per_serving, each through show, a food
    with no grams an empty cell, and the Grams column only when some food has grams. So two columns swapped, a figure
    off the formatter, or a deleted food shown, fail."""
    live = [f for f in services.list_saved_foods(account)["foods"] if f["deleted_at"] is None]
    assert live
    weighed = any(f["grams_per_serving"] is not None for f in live)
    headers, rows = saved_food_rows(client.get("/foods").text)
    assert headers == ["Food", "Serving", "kcal", "Times logged"] + (["Grams (g)"] if weighed else []) + \
        ["Edit", "Delete"]
    want = []
    for f in live:
        cells = {"Food": f["name"] + (" sample" if f["is_sample"] else ""), "Serving": f["serving_label"],
                 "kcal": show(f["kcal_per_serving"], "kcal_per_serving", page=True, unit=False),
                 "Times logged": show(f["times_logged"], "count", page=True),
                 "Grams (g)": "" if f["grams_per_serving"] is None else show(f["grams_per_serving"], "grams", page=True,
                                                                              unit=False),
                 "Edit": "Edit", "Delete": "Delete"}
        want.append([cells[h] for h in headers])
    assert rows == want


def test_the_edge_account_has_a_cardio_time_goal_and_a_closed_goal(client):
    sign_in(client)
    user_id = only_user().id
    edge_account(user_id)
    result = services.get_goals(user_id, True)
    assert any(g["kind"] == "cardio_best" for g in result["goals"]) and result["closed"]
    assert date.fromisoformat(result["today"])


# ---------------------------------------------------------------- Settings and Delete my account (pass B16)

# Each target's input and the quantity formatting.field takes it in (the contract's figure table, line 2124).
TARGET_FIELDS = (("calorie_target_kcal", "kcal"), ("target_sessions_per_week", "count"),
                 ("target_cardio_minutes_per_week", "week_minutes"))
# Delete my account's lines, by the one table's words, and the table each counts, deleted rows included.
DELETE_LINES = (("Lift sessions", LiftSession), ("Weigh-ins", Bodyweight), ("Measurements", Measurement),
                ("Food entries", FoodEntry), ("Cardio sessions", CardioEntry), ("Saved foods", SavedFood),
                ("Goals", Goal))


def test_settings_holds_the_user_rows_own_values_figure_by_figure(client, account):
    """The account facts are the user row's name, email, time_zone, subject and id. Each target's input is its stored
    value through formatting.field, empty when none is set. The add-back box and the checked switch are the row's."""
    page = client.get("/settings").text
    with db.session_scope() as s:
        user = s.get(User, account)
        row = {c.key: getattr(user, c.key) for c in User.__table__.columns}
    assert facts(page) == {"Name": row["name"] or "Not given", "Email": row["email"] or "Not given",
                           "Time zone": row["time_zone"], "Sign-in id": row["subject"], "Account id": str(row["id"])}
    for name, quantity in TARGET_FIELDS:
        [tag] = re.findall(rf'<input id="{name}" name="{name}"[^>]*>', page)
        value = re.search(r' value="([^"]*)"', tag)
        want = None if row[name] is None else field(row[name], quantity)
        assert (value.group(1) if value else None) == want, (name, tag)
    assert bool(re.search(r'name="add_back_exercise_kcal" checked', page)) == bool(row["add_back_exercise_kcal"])
    assert re.findall(r'name="claude_can" value="([a-z_]+)" checked', page) == [row["claude_can"]]


def test_delete_my_account_holds_each_count_line_by_line(client, account, make_user):
    """Each line of Delete my account is its table's count of the person's rows, deleted ones included, through show
    as a count, in the one table's words and order. Another person's rows are not counted."""
    spare_id = make_user().id
    assert "error" not in services.log_bodyweight(spare_id, {"kg": "90"}, "claude")
    lines = re.findall(r'<li class="entry-line"><span>([^<]+)</span><strong>([^<]+)</strong></li>',
                       client.get("/settings/delete").text)
    want = []
    with db.session_scope() as s:
        for label, model in DELETE_LINES:
            n = s.scalar(select(func.count()).select_from(model).where(model.user_id == account))
            want.append((label, show(n, "count", page=True)))
    assert lines == want and any(n != "0" for _, n in lines)
