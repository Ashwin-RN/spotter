"""T7, the step 2 part: a figure on a page is the figure Claude reads, printed by the one formatter.

For each figure that is both on Progress and in get_trends (and get_progress), and both on Goals and in
get_goals, the page's text holds exactly show(value, quantity, page=True) of the result's number, inside
the figure's own element. Text inside an SVG chart and a label built in a service are compared with
page=False. The chart's description never widens.

Run on the real services, on the sample account and on an edge account, so show on both sides holds
whatever precision a service field carries. The plan-line fields are a later step.
"""

from __future__ import annotations

import html
import re
from datetime import date

import pytest

from app import links, services
from app.formatting import day, day_utc, days, show

from . import text_rules as tr
from .test_pages_text import edge_account, sample_account
from .test_web import body_weight_card, client, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

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

def test_the_trends_card_holds_get_trends_figures(client, account):
    trends = services.get_trends(account)
    page = client.get("/progress").text
    card = facts(re.search(r'<div class="card trends"[^>]*>(.*?)</div>', page, re.S).group(1))
    bw, cal, ses, car, m = (trends["bodyweight"], trends["calories"], trends["sessions"], trends["cardio"],
                            trends["maintenance"])
    assert bw is not None
    figure(card["Body weight"], bw["smoothed"], "bodyweight_derived")
    if bw["now"] is not None:
        figure(card["Body weight"], bw["now"], "bodyweight_derived")
    if bw["rate_per_week"] is not None:
        assert holds(card["Body weight"], f"{show(bw['rate_per_week'], 'rate', page=True)} kg a week")
        window = bw["rate_window"]
        assert holds(card["Body weight"], f"from {days(window['from'], window['to'], trends['today'])}")
    assert cal is not None
    if cal["avg_7d"] is not None:
        figure(card["Calories a day"], cal["avg_7d"], "kcal")
        figure(card["Calories a day"], cal["days_logged_7d"], "count")
    figure(card["Calories a day"], cal["avg_28d"], "kcal")
    figure(card["Calories a day"], cal["days_logged_28d"], "count")
    if cal["target_kcal"] is not None:
        figure(card["Calories a day"], cal["target_kcal"], "kcal")
    if cal["estimated_share_pct_28d"]:
        figure(card["Calories a day"], cal["estimated_share_pct_28d"], "percent")
    figure(card["Sessions"], ses["this_week"], "count")
    figure(card["Sessions"], ses["per_week_4w"], "count_avg")
    if ses["target_per_week"] is not None:
        figure(card["Sessions"], ses["target_per_week"], "count")
    figure(card["Cardio"], car["minutes_this_week"], "week_minutes")
    figure(card["Cardio"], car["minutes_per_week_4w"], "week_minutes")
    if car["target_minutes"] is not None:
        figure(card["Cardio"], car["target_minutes"], "week_minutes")
    if m:
        figure(card["Maintenance"], m["kcal"], "maintenance")
        figure(card["Maintenance"], m["days_logged"], "count")
        assert holds(card["Maintenance"], days(m["from"], m["to"], trends["today"]))


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
        cells = re.search(rf'<td><a href="/progress/[^"]+">{re.escape(row["exercise"])}</a></td>(.*?)</tr>',
                          page, re.S).group(1)
        text = text_of(cells)
        lift = services.get_progress(account, row["exercise"])["lift"]
        if row["current_estimated_max_kg"] is not None:
            figure(text, row["current_estimated_max_kg"], "estimated_max")
        else:
            assert "No estimated max yet" in text
        best = lift["best_set"]
        if best:  # the best set, read from lifts.history on the page and from get_progress here
            assert holds(text, f"{show(best['weight_kg'], 'weight_kg', page=True)} × {show(best['reps'], 'reps')}")


def test_a_lifts_page_holds_its_figures(client, account):
    name = services.get_progress(account)["lifts"][0]["exercise"]
    result = services.get_progress(account, name)
    lift, today = result["lift"], result["today"]
    page = client.get(f"/progress/{links.slug(name)}").text
    card = facts(page)
    if lift["current_estimated_max_kg"] is not None:
        figure(card["Estimated max now"], lift["current_estimated_max_kg"], "estimated_max")
    figure(card["Sessions"], lift["sessions"], "count")
    sessions = text_of(re.search(r"<h2>Sessions</h2>(.*?)</div>", page, re.S).group(1))
    for p in lift["estimated_max_over_time"]:
        assert holds(sessions, f"{day(p['date'], today)}: estimated max {show(p['estimated_max_kg'], 'estimated_max', page=True)}")


# ---------------------------------------------------------------- Goals and get_goals

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
        figure(card["Started"], g["start_value"], q)
        assert holds(card["Started"], day(g["start_date"], today))
        if g["latest"] is not None:
            figure(card["Now"], g["latest"], q)
        pace = "time_pace" if g["kind"] == "cardio_best" else "rate"
        for name, value in (("Needed", g["needed_per_week"]), ("So far", g["actual_per_week"])):
            if value is None:
                continue
            words = "" if pace == "time_pace" else f" {g['unit']} a week"
            assert holds(card[name], show(value, pace, page=True) + words), (name, card[name])
        if g["projected_date"]:
            assert holds(card["At this pace"], day(g["projected_date"], today))
        assert g["status"] in text
    for g in result["closed"]:
        item = re.search(rf'<li class="entry-line" id="goal-{g["goal_id"]}">(.*?)</li>', page, re.S).group(1)
        if g["closed_at"]:
            assert holds(text_of(item), f"on {day_utc(g['closed_at'], zone, today)}")


def test_the_edge_account_has_a_cardio_time_goal_and_a_closed_goal(client):
    sign_in(client)
    user_id = only_user().id
    edge_account(user_id)
    result = services.get_goals(user_id, True)
    assert any(g["kind"] == "cardio_best" for g in result["goals"]) and result["closed"]
    assert date.fromisoformat(result["today"])
