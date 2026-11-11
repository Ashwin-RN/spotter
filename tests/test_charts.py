"""app/charts.py, and the chart data app.services.progress builds for it."""

import inspect
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import charts, db, formatting, metrics, services, users
from app.charts import bodyweight_svg, chart_svg
from app.metrics import bodyweight_average
from app.models import Goal
from app.services import body, lifts
from app.services.goals import goal_numbers, load_numbers
from app.services.progress import bodyweight_chart, lift_chart

from . import text_rules as tr
from .test_css import CSS_PATH, Sheet

SVG = "{http://www.w3.org/2000/svg}"
CHART = {"lift": "Bench <Press>", "choices": ["Bench <Press>"],
         "points": [{"date": "2026-10-01", "value": 50.0}, {"date": "2026-10-08", "value": 52.5},
                    {"date": "2026-10-15", "value": 53.0}],
         "latest": {"date": "2026-10-15", "value": 53.0},
         "goal": {"target_kg": 60.0, "target_date": "2026-12-01", "status": "behind", "projected": 57.4}}


def lines(root):
    return {el.get("class"): el for el in root.iter(f"{SVG}line") if el.get("class") != "grid"}


# ---------------------------------------------------------------- the SVG

def test_chart_svg_is_valid_with_goal_line_and_dashed_projection():
    root = ET.fromstring(str(chart_svg(CHART)))  # parses: well-formed, the lift name escaped
    assert root.tag == f"{SVG}svg" and root.get("role") == "img"
    assert root.find(f"{SVG}title").text == "Estimated max over time: Bench <Press>"
    assert "Latest 53 kg on Thu 15 Oct" in root.find(f"{SVG}desc").text  # SVG text: a plain space
    goal = lines(root)["goal"]
    assert goal.get("y1") == goal.get("y2") and goal.get("x1") != goal.get("x2")  # horizontal
    projection = lines(root)["projection"]
    assert projection.get("stroke-dasharray") and float(projection.get("x2")) > float(projection.get("x1"))
    assert float(projection.get("y2")) > float(goal.get("y1"))  # 57.4 sits below the 60 kg line
    assert len(list(root.iter(f"{SVG}circle"))) == 3 and root.find(f"{SVG}polyline") is not None
    assert "Goal 60 kg" in [t.text for t in root.iter(f"{SVG}text")]


def test_chart_svg_without_goal_or_points():
    assert str(chart_svg(None)) == "" and str(chart_svg({**CHART, "points": []})) == ""
    root = ET.fromstring(str(chart_svg({**CHART, "goal": None, "points": CHART["points"][:1]})))
    assert not [el for el in root.iter(f"{SVG}line") if el.get("class") in ("goal", "projection")]
    assert root.find(f"{SVG}polyline") is None and len(list(root.iter(f"{SVG}circle"))) == 1


def test_chart_skips_a_past_goal_date_and_clamps_a_wild_projection():
    past = {**CHART, "goal": {**CHART["goal"], "target_date": "2026-10-10"}}  # before the last point
    root = ET.fromstring(str(chart_svg(past)))
    assert "projection" not in lines(root) and "goal" in lines(root)

    wild = {**CHART, "goal": {**CHART["goal"], "projected": 400.0}}
    root = ET.fromstring(str(chart_svg(wild)))
    projection = lines(root)["projection"]
    grid_y = [float(el.get("y1")) for el in root.iter(f"{SVG}line") if el.get("class") == "grid"]
    assert min(grid_y) <= float(projection.get("y2")) <= max(grid_y)  # inside the plot, not off the top
    labels = [t.text for t in root.iter(f"{SVG}text") if "axis" in (t.get("class") or "").split()]
    assert labels and not [t for t in labels if t.startswith("400")]
    assert "projected 400 kg" in root.find(f"{SVG}desc").text


def test_the_element_id_and_the_description_are_escaped():
    odd = '"><script>x</script><svg id="'
    shady = {**CHART, "goal": {**CHART["goal"], "status": "a < b & c"}}
    for svg in (chart_svg(shady, element_id=odd),
                bodyweight_svg({"points": [{"date": "2026-10-01", "kg": 80.0}, {"date": "2026-10-08", "kg": 79.0}],
                                "average": [{"date": "2026-10-08", "kg": 79.5}],
                                "latest": {"date": "2026-10-08", "kg": 79.0},
                                "goal": {"target_kg": 75.0, "target_date": "2026-12-01"}}, element_id=odd)):
        root = ET.fromstring(str(svg))  # still one well-formed SVG
        assert root.get("id") == odd and not list(root.iter(f"{SVG}script")) and not list(root.iter("script"))
        assert root.find(f"{SVG}title").get("id") == f"{odd}-title"
    desc = ET.fromstring(str(chart_svg(shady))).find(f"{SVG}desc").text
    assert "behind" not in desc and "a < b & c" in desc


WEIGHED = {"points": [{"date": "2026-10-01", "kg": 80.0, "smoothed": 80.0},
                      {"date": "2026-10-02", "kg": 81.0, "smoothed": 80.1},
                      {"date": "2026-10-03", "kg": 79.0, "smoothed": 79.99}],
           "average": [{"date": "2026-10-01", "kg": 80.0}, {"date": "2026-10-02", "kg": 80.5},
                       {"date": "2026-10-03", "kg": 80.0}],
           "latest": {"date": "2026-10-03", "kg": 79.0}, "goal": None}


def polyline_values(root, chart) -> list[float]:
    """The kg each point of the chart's one line stands for, read back from its y positions."""
    [line] = list(root.iter(f"{SVG}polyline"))
    ys = [float(pair.split(",")[1]) for pair in line.get("points").split()]
    dots = {float(c.get("cy")): float(c.find(f"{SVG}title").text.split(": ")[1].split()[0])
            for c in root.iter(f"{SVG}circle")}
    (y1, v1), (y2, v2) = sorted(dots.items())[:2]  # two dots fix the scale
    return [round(v1 + (y - y1) * (v2 - v1) / (y2 - y1), 2) for y in ys]


def test_the_bodyweight_line_is_the_smoothed_trend_not_the_average():
    root = ET.fromstring(str(bodyweight_svg(WEIGHED)))
    [line] = list(root.iter(f"{SVG}polyline"))
    assert line.get("class") == "series smoothed"
    assert polyline_values(root, WEIGHED) == [80.0, 80.1, 79.99]  # the smoothed values, not 80, 80.5, 80
    desc = root.find(f"{SVG}desc").text
    assert "The line is the smoothed trend, now 80 kg." in desc and "7-day average" not in desc  # 1 dp
    assert len(list(root.iter(f"{SVG}circle"))) == 3  # every weigh-in is still a dot


def test_no_line_without_two_smoothed_points():
    bare = {**WEIGHED, "points": [{k: v for k, v in p.items() if k != "smoothed"} for p in WEIGHED["points"]]}
    one = {**WEIGHED, "points": [WEIGHED["points"][0], *bare["points"][1:]]}
    nulls = {**WEIGHED, "points": [{**p, "smoothed": None} for p in WEIGHED["points"]]}
    for chart in (bare, one, nulls):
        root = ET.fromstring(str(bodyweight_svg(chart)))
        assert root.find(f"{SVG}polyline") is None and len(list(root.iter(f"{SVG}circle"))) == 3
        assert "smoothed" not in root.find(f"{SVG}desc").text


def test_the_smoothed_line_stays_inside_the_plot():
    far = {**WEIGHED, "points": [{**p, "smoothed": s} for p, s in zip(WEIGHED["points"], (70.0, 90.0, 80.0))]}
    root = ET.fromstring(str(bodyweight_svg(far)))
    ys = [float(pair.split(",")[1]) for pair in root.find(f"{SVG}polyline").get("points").split()]
    grid = [float(el.get("y1")) for el in root.iter(f"{SVG}line") if el.get("class") == "grid"]
    assert all(min(grid) <= y <= max(grid) for y in ys)


# ---------------------------------------------------------------- the data, from the services

def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def numbers_for(user):
    today = users.today_for(user)
    with db.new_session() as s:
        history = lifts.history(s, user.id, today)
        weighed = body.weigh_ins(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")
                               .options(selectinload(Goal.exercise))))
        loaded = load_numbers(s, user.id, today, goals, lift_history=history)
        return history, weighed, goals, goal_numbers(goals, loaded, today), today


def test_the_lift_chart_follows_the_goal_lift_and_draws(make_user):
    user = make_user()
    for days_ago, kg in ((20, 60), (15, 62.5), (10, 65), (5, 67.5)):
        services.log_lifts(user.id, {"date": day(user, -days_ago), "sets": [
            {"exercise": "Back Squat", "weight_kg": kg, "reps": 5}]}, "claude")
    for days_ago in (12, 9, 6, 3, 2):
        services.log_lifts(user.id, {"date": day(user, -days_ago), "sets": [
            {"exercise": "Bench Press", "weight_kg": 40, "reps": 8}]}, "claude")
    history, _, _, numbers, _ = numbers_for(user)
    assert lift_chart(history, numbers)["lift"] == "Bench Press"  # the most points, with no goal
    services.set_goal(user.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 100,
                                "target_date": day(user, 56)}, "claude")
    history, _, _, numbers, _ = numbers_for(user)
    chart = lift_chart(history, numbers)
    assert chart["lift"] == "Back Squat" and chart["choices"] == ["Bench Press", "Back Squat"]
    assert chart["goal"]["target_kg"] == 100 and chart["goal"]["projected"] is not None
    assert lift_chart(history, numbers, "Bench Press")["goal"] is None
    root = ET.fromstring(str(chart_svg(chart)))
    assert "Goal 100 kg" in [t.text for t in root.iter(f"{SVG}text")] and "projection" in lines(root)
    assert lift_chart({"points": {}}, []) is None


def test_the_bodyweight_chart_for_a_weight_loss_goal(make_user):
    user = make_user()
    for days_ago, kg in ((40, 88), (30, 87.2), (20, 86.5), (10, 86), (3, 85.5), (0, 85.5)):
        services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "claude")
    services.set_goal(user.id, {"kind": "bodyweight", "target_value": 82, "target_date": day(user, 60)}, "claude")
    _, weighed, goals, _, today = numbers_for(user)
    chart = bodyweight_chart(weighed, goals, today)
    assert len(chart["points"]) == 6 and len(chart["average"]) == 6
    assert chart["goal"]["target_kg"] == 82 and chart["latest"]["kg"] == 85.5
    assert chart["average"][-1]["kg"] == bodyweight_average(
        [(b.date, b.kg) for b in weighed], weighed[-1].date)
    svg = str(bodyweight_svg(chart))
    root = ET.fromstring(svg)
    assert root.get("id") == "bodyweight-chart" and root.find(f"{SVG}title").text == "Body weight over time"
    assert "Goal 82 kg" in svg and len(list(root.iter(f"{SVG}circle"))) == 6


def test_the_real_chart_data_carries_the_smoothed_line(make_user):
    user = make_user()
    for days_ago, kg in ((20, 88), (15, 87.2), (10, 86.5), (5, 86), (0, 85.5)):
        services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "claude")
    _, weighed, goals, _, today = numbers_for(user)
    chart = bodyweight_chart(weighed, goals, today)
    assert [p["smoothed"] for p in chart["points"]] == [formatting.rounded(s, "bodyweight_derived") for _, s in
                                                        metrics.ewma([(b.date, b.kg) for b in weighed], today)]
    root = ET.fromstring(str(bodyweight_svg(chart)))
    assert polyline_values(root, chart) == [p["smoothed"] for p in chart["points"]]


def test_no_bodyweight_chart_under_two_weigh_ins(make_user):
    user = make_user()
    services.log_bodyweight(user.id, {"kg": 80}, "claude")
    _, weighed, goals, _, today = numbers_for(user)
    assert bodyweight_chart(weighed, goals, today) is None
    assert str(bodyweight_svg(None)) == ""


def test_the_bodyweight_chart_takes_its_window(make_user):
    """Stage 6, section D: bodyweight_chart's since draws only the weigh-ins from that date on, while the
    smoothed value stays the line over every weigh-in. Lane A adds since in its step 3 pass, after this one."""
    if "since" not in inspect.signature(bodyweight_chart).parameters:
        pytest.skip("progress.bodyweight_chart has no since yet: lane A, step 3")
    user = make_user()
    for days_ago, kg in ((40, 88), (30, 87.2), (20, 86.5), (10, 86), (3, 85.5), (0, 85.5)):
        services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "claude")
    _, weighed, goals, _, today = numbers_for(user)
    every = bodyweight_chart(weighed, goals, today)
    window = bodyweight_chart(weighed, goals, today, since=today - timedelta(days=27))
    assert window["points"] == every["points"][-4:]  # the same smoothed values: ewma over every weigh-in
    assert bodyweight_chart(weighed, goals, today, since=today - timedelta(days=1)) is None  # one in the window
    parsed(bodyweight_svg(window, today=today))


# ---------------------------------------------------------------- stage 6: the chart look, every chart
# Hand-made data in the shapes the reads return: get_trends bodyweight.series, trends.get_days days (E2),
# goals.goal_charts goals (E1) and cardio.get_cardio sessions (E3).

TODAY = date(2026, 11, 10)
BANNED_TAGS = {"script", "style", "use", "image", "foreignObject", "a", "animate", "set", "animateTransform"}


def parsed(markup) -> ET.Element:
    """One well-formed SVG that obeys the security policy: role, a title and a desc first, no style
    attribute, no script, no outside reference, no SMIL."""
    text = str(markup)
    root = ET.fromstring(text)
    assert root.tag == f"{SVG}svg" and root.get("role") == "img" and "chart" in root.get("class").split()
    title, desc = list(root)[:2]
    assert title.tag == f"{SVG}title" and desc.tag == f"{SVG}desc" and title.text and desc.text
    assert root.get("aria-labelledby") == f"{title.get('id')} {desc.get('id')}"
    for el in root.iter():
        assert el.tag.removeprefix(SVG) not in BANNED_TAGS, el.tag
        for name, value in el.attrib.items():
            assert name != "style" and "href" not in name and not name.startswith("on"), (el.tag, name)
            assert "url(" not in value and "javascript:" not in value and "data:" not in value, (el.tag, value)
    assert " " not in text  # text inside an SVG keeps a plain space
    series = [line for line in root.iter(f"{SVG}polyline") if "series" in line.get("class").split()]
    assert all(line.get("pathLength") == "1" for line in series)  # each draws in once on load
    texts_inside_and_apart(root)
    return root


# Text size in chart units, read from the stylesheet: the phone sizes, the largest any chart text is
# drawn at in its own units. A character's width in ems, near the system fonts' own (Segoe UI 0.56).
_CSS = Sheet(CSS_PATH.read_text(encoding="utf-8"))
AXIS_UNITS = float(_CSS.value("svg.chart .axis", "font-size").removesuffix("px"))
SMALL_UNITS = float(_CSS.value("svg.chart.small .axis", "font-size").removesuffix("px"))
LABEL_UNITS = float(_CSS.value("svg.chart .goal-label", "font-size").removesuffix("px"))
# A character's width in ems, by kind: near Segoe UI's own advance widths (digits 0.55).
EMS = {"digit": 0.55, "narrow": 0.27, "upper": 0.64, "lower": 0.5}
BOLDER = 1.04


def em(ch: str) -> float:
    kind = ("digit" if ch.isdigit() else "narrow" if ch in " ,.:;'-" else
            "upper" if ch.isupper() else "lower")
    return EMS[kind]
BOLD = {"value", "goal-label", "today-label", "is-today"}


def extent(el, small: bool) -> tuple[float, float, float, float]:
    """A text element's box (left, right, top, bottom), from its anchor, its text and its size."""
    names = set(classes(el))
    size = LABEL_UNITS if "goal-label" in names else (SMALL_UNITS if small else AXIS_UNITS)
    bold = bool(names & BOLD) or small
    w = sum(em(ch) for ch in el.text) * size * (BOLDER if bold else 1)
    x, y = float(el.get("x")), float(el.get("y"))
    left = x - w if "end" in names else (x - w / 2 if "mid" in names else x)
    return left, left + w, y - 0.75 * size, y + 0.25 * size


def texts_inside_and_apart(root) -> None:
    """Every label lies inside the viewBox, and no two labels overlap."""
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    small = "small" in classes(root)
    boxes = [(el.text, extent(el, small)) for el in root.iter(f"{SVG}text")]
    for text, (l, r, t, b) in boxes:
        assert 0 <= l and r <= width and 0 <= t and b <= height, (text, (l, r, t, b), (width, height))
    for i, (one, a) in enumerate(boxes):
        for two, b in boxes[i + 1:]:
            apart = a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2]
            assert apart, (one, a, two, b)


def classes(el) -> list[str]:
    return (el.get("class") or "").split()


def of_class(root, tag, name) -> list[ET.Element]:
    return [el for el in root.iter(f"{SVG}{tag}") if name in classes(el)]


def texts(root, name="axis") -> list[str]:
    return [t.text for t in of_class(root, "text", name)]


def page_text_is_clean(root) -> None:
    """The title and desc are read on a page (T3): the page rules, sentence length among them."""
    for el in (root.find(f"{SVG}title"), root.find(f"{SVG}desc")):
        assert not tr.check(el.text, "page"), (el.text, tr.check(el.text, "page"))


def grid_ys(root) -> list[float]:
    return [float(el.get("y1")) for el in of_class(root, "line", "grid")]


def inside_grid(root, ys) -> None:
    grid = grid_ys(root)
    assert grid and ys and all(min(grid) - 0.01 <= y <= max(grid) + 0.01 for y in ys), (grid, ys)


SERIES = [{"date": (TODAY - timedelta(days=27 - n)).isoformat(), "kg": round(80 - n * 0.05, 2),
           "smoothed": round(79.9 - n * 0.026, 2)} for n in range(28)]


def test_the_kept_charts_take_the_new_look():
    root = parsed(chart_svg(CHART))
    assert [classes(c) for c in root.iter(f"{SVG}circle")][-1] == ["dot", "is-latest"]
    ticks = [t for t in texts(root) if t[0].isdigit()]
    assert [t for t in ticks if t.endswith(" kg")] == [ticks[-1]]  # the unit once, on the top tick
    root = parsed(bodyweight_svg(WEIGHED))
    ticks = [float(t.split()[0]) for t in texts(root) if t[0].isdigit()]
    assert len(ticks) >= 2 and all(t == int(t) for t in ticks)  # grid lines in whole kg
    inside_grid(root, [float(c.get("cy")) for c in root.iter(f"{SVG}circle")])
    for root in (parsed(chart_svg(CHART)), parsed(bodyweight_svg(WEIGHED))):
        page_text_is_clean(root)


def test_the_small_weight_chart_is_the_line_the_area_and_the_end_dot():
    root = parsed(charts.weight_small_svg(SERIES, TODAY))
    assert "small" in classes(root) and root.get("id") == "chart-weight-4w"
    assert not of_class(root, "text", "axis") and not of_class(root, "line", "grid")  # no axis
    [line] = of_class(root, "polyline", "series")
    assert classes(line) == ["series", "smoothed"] and len(line.get("points").split()) == 28
    assert of_class(root, "path", "area") and len(of_class(root, "circle", "end-dot")) == 1
    desc = root.find(f"{SVG}desc").text
    assert f"from {formatting.show(SERIES[0]['smoothed'], 'bodyweight_derived')} on Wed 14 Oct" in desc
    assert f"to {formatting.show(SERIES[-1]['smoothed'], 'bodyweight_derived')} on Tue 10 Nov." in desc
    page_text_is_clean(root)


def test_the_weight_sheet_chart_has_three_labelled_grid_lines_and_its_two_dates():
    root = parsed(charts.weight_sheet_svg(SERIES, TODAY.isoformat()))
    assert len(grid_ys(root)) == 3
    assert [t for t in texts(root) if t[0].isdigit()] == ["79", "79.5", "80 kg"]  # even, at the shown step
    assert {"Wed 14 Oct", "Tue 10 Nov"} <= set(texts(root))
    [line] = of_class(root, "polyline", "series")
    inside_grid(root, [float(p.split(",")[1]) for p in line.get("points").split()])
    page_text_is_clean(root)


def test_a_weight_chart_needs_two_smoothed_points():
    for series in ([], SERIES[:1], [{**p, "smoothed": None} for p in SERIES], None):
        assert str(charts.weight_small_svg(series, TODAY)) == "" and str(charts.weight_sheet_svg(series, TODAY)) == ""
    assert str(charts.weight_small_svg(SERIES[:2], TODAY)) and str(charts.weight_sheet_svg(SERIES[:2], TODAY))


DAYS = [{"date": (TODAY - timedelta(days=27 - n)).isoformat(), "kcal": 2000 + n * 10, "lift_sessions": 0,
         "cardio_entries": 0, "cardio_minutes": 0.0, "food_entries": 3, "weighed": False, "is_sample": False}
        for n in range(28)]
DAYS[-3] = {**DAYS[-3], "kcal": None}  # Sunday 8 November: no food logged


def test_the_small_eaten_chart_is_a_bar_a_day_with_the_target():
    root = parsed(charts.eaten_small_svg(DAYS, TODAY, target_kcal=2400))
    assert "small" in classes(root)
    bars = list(root.iter(f"{SVG}rect"))
    assert len(bars) == 6 and classes(bars[-1]) == ["bar", "is-today"]  # Sunday has no bar
    assert sum("is-today" in classes(b) for b in bars) == 1
    assert texts(root) == ["W", "T", "F", "S", "S", "M", "T"]
    assert "is-today" in classes(of_class(root, "text", "axis")[-1])
    [goal] = of_class(root, "line", "goal")
    assert float(goal.get("y1")) < min(float(b.get("y")) for b in bars)  # 2,400 sits above every bar
    desc = root.find(f"{SVG}desc").text
    assert "Tue 10 Nov: 2,270 kcal." in desc and "Sun 8 Nov: no food logged." in desc
    assert "Calorie target 2,400 kcal." in desc
    page_text_is_clean(root)
    assert not of_class(parsed(charts.eaten_small_svg(DAYS, TODAY)), "line", "goal")  # no target, no line


def test_the_eaten_sheet_chart_labels_each_bar():
    root = parsed(charts.eaten_sheet_svg(DAYS, TODAY))
    values = texts(root, "value")
    assert values == [formatting.show(d["kcal"], "kcal", unit=False) for d in DAYS[-7:] if d["kcal"] is not None]
    assert [t for t in texts(root) if t not in values] == ["Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue"]
    assert len(list(root.iter(f"{SVG}rect"))) == 6
    page_text_is_clean(root)


def test_an_eaten_chart_needs_food_in_the_seven_days():
    empty = [{**d, "kcal": None} for d in DAYS]
    for days in (empty, [], None):
        assert str(charts.eaten_small_svg(days, TODAY)) == "" and str(charts.eaten_sheet_svg(days, TODAY)) == ""


def goal_like(**over) -> dict:
    goal = {"goal_id": 7, "label": "Body weight 77 kg", "kind": "bodyweight", "unit": "kg", "is_sample": False,
            "start_date": "2026-10-14", "start_value": 79.9, "target_date": "2027-01-05", "target_value": 77.0,
            "latest": 79.0123, "needed_per_week": -0.2512, "actual_per_week": -0.29,
            "projected_date": "2026-12-26", "projection": {"projected": 76.5, "latest": 79.0123},
            "should_be_today": 78.9611, "ahead_by": -0.0512, "days_ahead": -2, "vs_plan": "behind",
            "now_basis": "average_7d", "plan_line": "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind.",
            "path": {"plan": [{"date": "2026-10-14", "value": 79.9}, {"date": "2026-11-10", "value": 78.9611},
                              {"date": "2027-01-05", "value": 77.0}],
                     "actual": [{"date": "2026-10-14", "value": 79.9}, {"date": "2026-10-21", "value": 79.6},
                                {"date": "2026-10-28", "value": 79.4}, {"date": "2026-11-04", "value": 79.2},
                                {"date": "2026-11-10", "value": 79.0123}]}}
    goal.update(over)
    return goal


def at(el, a, b) -> tuple[float, float]:
    return float(el.get(a)), float(el.get(b))


def test_the_goal_chart_draws_plan_actual_projection_today_and_target():
    root = parsed(charts.goal_svg(goal_like(), TODAY))
    assert root.get("id") == "chart-goal-7"
    assert root.find(f"{SVG}title").text == "Body weight 77 kg by Tue 5 Jan 2027"
    [plan] = of_class(root, "line", "plan")
    [today] = of_class(root, "line", "today-line")
    [projection] = of_class(root, "line", "projection")
    [tick] = of_class(root, "line", "target-tick")
    [series] = of_class(root, "polyline", "series")
    assert len(series.get("points").split()) == 5
    assert len([d for d in of_class(root, "circle", "dot") if "proj-end" not in classes(d)]) == 5
    [latest] = of_class(root, "circle", "is-latest")
    assert latest.find(f"{SVG}title").text == "Tue 10 Nov: 79 kg"
    # The projection runs from (today, now) to (the projected date, the target).
    assert at(projection, "x1", "y1") == at(latest, "cx", "cy") and projection.get("x1") == today.get("x1")
    assert projection.get("y2") == tick.get("y1") and len(of_class(root, "circle", "proj-end")) == 1
    assert float(plan.get("x2")) == float(tick.get("x2")) > float(projection.get("x2"))  # reached before the date
    assert "Today" in texts(root, "today-label")
    assert texts(root, "goal-label") == ["77 kg"] and {"Wed 14 Oct", "Tue 5 Jan 2027"} <= set(texts(root))
    inside_grid(root, [float(p.split(",")[1]) for p in series.get("points").split()] +
                [float(plan.get("y1")), float(plan.get("y2")), float(projection.get("y2"))])
    page_text_is_clean(root)


def test_the_goal_charts_desc_says_the_goals_own_plan_line():
    """Section D's close-call rules live in plan_line, built by the service: the desc says it as written,
    after the start, and builds no comparison of its own."""
    desc = parsed(charts.goal_svg(goal_like(), TODAY)).find(f"{SVG}desc").text
    assert desc == ("Started 79.9 kg on Wed 14 Oct. 79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind. "
                    "Target 77 kg by Tue 5 Jan 2027. Needed -0.25 kg a week. So far -0.29 kg a week. "
                    "At this pace it is reached on Sat 26 Dec.")
    for line in ("On the plan line today, at 79 kg.", "78 kg now, at the target.", "Any other form the service writes."):
        desc = parsed(charts.goal_svg(goal_like(plan_line=line), TODAY)).find(f"{SVG}desc").text
        assert desc.split(". ", 1)[1].startswith(line) and "plan line is at" not in desc
    nothing = parsed(charts.goal_svg(goal_like(plan_line=None, latest=None, vs_plan=None), TODAY))
    assert "Nothing logged yet." in desc_of(nothing) and " now" not in desc_of(nothing).lower().replace("nothing", "")


def test_one_point_draws_the_plan_line_one_ringed_dot_and_the_today_line():
    one = goal_like(label="Waist 84 cm", kind="measurement", unit="cm", measurement_kind="waist",
                    start_date=TODAY.isoformat(), start_value=86.5, target_value=84.0, latest=86.5,
                    target_date="2027-02-02", needed_per_week=-0.21, actual_per_week=None, projected_date=None,
                    projection=None, should_be_today=86.5, ahead_by=0, days_ahead=0, vs_plan="on the line",
                    plan_line="On the plan line today, at 86.5 cm.",
                    path={"plan": [{"date": TODAY.isoformat(), "value": 86.5}, {"date": "2027-02-02", "value": 84.0}],
                          "actual": [{"date": TODAY.isoformat(), "value": 86.5}]})
    root = parsed(charts.goal_svg(one, TODAY))
    assert not root.findall(f"{SVG}polyline") and not of_class(root, "line", "projection")
    assert [classes(c) for c in root.iter(f"{SVG}circle")] == [["dot", "is-latest"]]
    assert of_class(root, "line", "plan") and of_class(root, "line", "today-line")
    assert texts(root, "goal-label") == ["84 cm"] and [t for t in texts(root) if t.endswith(" cm")]
    page_text_is_clean(root)
    nothing = {**one, "latest": None, "path": {**one["path"], "actual": []}, "vs_plan": None, "ahead_by": None,
               "plan_line": None}
    root = parsed(charts.goal_svg(nothing, TODAY))
    assert not list(root.iter(f"{SVG}circle")) and "Nothing logged yet." in root.find(f"{SVG}desc").text
    assert of_class(root, "line", "plan") and of_class(root, "line", "today-line")


def test_a_projection_with_no_projected_date_ends_at_the_target_date():
    root = parsed(charts.goal_svg(goal_like(projected_date=None, projection={"projected": 78.0}), TODAY))
    [projection] = of_class(root, "line", "projection")
    [tick] = of_class(root, "line", "target-tick")
    assert projection.get("x2") == tick.get("x2")
    assert float(projection.get("y2")) < float(tick.get("y1"))  # 78 kg sits above the 77 kg target
    root = parsed(charts.goal_svg(goal_like(projected_date=None, projection=None), TODAY))
    assert not of_class(root, "line", "projection") and not of_class(root, "circle", "proj-end")


def test_a_projected_date_far_past_the_target_is_held_inside_the_chart():
    root = parsed(charts.goal_svg(goal_like(projected_date="2028-06-01", status="behind"), TODAY))
    [projection] = of_class(root, "line", "projection")
    [tick] = of_class(root, "line", "target-tick")
    [grid] = of_class(root, "line", "grid")[:1]
    assert float(tick.get("x2")) < float(projection.get("x2")) <= float(grid.get("x2"))
    assert "Thu 1 Jun 2028" in root.find(f"{SVG}desc").text
    inside_grid(root, [float(projection.get("y2"))])


def test_a_cardio_time_goal_reads_in_m_ss_and_seconds():
    run = goal_like(label="Outdoor Run 5 km in 27:00", kind="cardio_best", unit="min", exercise="Outdoor Run",
                    distance_km=5.0, start_date="2026-10-17", start_value=31.0, target_value=27.0, latest=29.5,
                    needed_per_week=-0.31, actual_per_week=-0.5, projected_date="2026-12-12",
                    projection={"projected": 26.4, "latest": 29.5}, should_be_today=29.81, vs_plan="ahead",
                    plan_line="29:30 now against 29:49 on the plan line today: 19 seconds ahead.",
                    path={"plan": [{"date": "2026-10-17", "value": 31.0}, {"date": "2027-01-05", "value": 27.0}],
                          "actual": [{"date": "2026-10-17", "value": 31.0}, {"date": "2026-10-24", "value": 30.5},
                                     {"date": "2026-10-31", "value": 30.0}, {"date": "2026-11-07", "value": 29.5}]})
    root = parsed(charts.goal_svg(run, TODAY))
    ticks = [t for t in texts(root) if ":" in t]
    assert len(ticks) >= 2 and all(re.fullmatch(r"\d+:\d\d", t) for t in ticks)  # whole seconds, no unit
    assert texts(root, "goal-label") == ["27:00"]
    desc = root.find(f"{SVG}desc").text
    assert "Needed -19 seconds a week." in desc and "So far -30 seconds a week." in desc
    assert "29:30 now against 29:49 on the plan line today: 19 seconds ahead." in desc
    page_text_is_clean(root)


def test_the_goal_charts_now_is_its_latest_at_today_not_the_last_point():
    """E1: path.actual has a point per date that has one, so it can end before today. The ring is the
    goal's latest, at today; the actual line keeps its own last point."""
    goal = goal_like(latest=78.8, plan_line="78.8 kg now against 79 kg on the plan line today: 0.2 kg ahead.",
                     path={**goal_like()["path"], "actual": goal_like()["path"]["actual"][:-1]})
    root = parsed(charts.goal_svg(goal, TODAY))
    [ring] = of_class(root, "circle", "is-latest")
    [today] = of_class(root, "line", "today-line")
    [projection] = of_class(root, "line", "projection")
    assert ring.get("cx") == today.get("x1") == projection.get("x1") and ring.get("cy") == projection.get("y1")
    assert ring.find(f"{SVG}title").text == "Tue 10 Nov: 78.8 kg"
    [series] = of_class(root, "polyline", "series")
    last = series.get("points").split()[-1].split(",")
    assert float(last[0]) < float(ring.get("cx")) and float(last[1]) != float(ring.get("cy"))
    dots = [c for c in of_class(root, "circle", "dot") if not {"is-latest", "proj-end"} & set(classes(c))]
    assert len(dots) == 4 and "78.8 kg now against" in root.find(f"{SVG}desc").text


def test_the_goal_charts_first_date_is_the_start_and_its_last_the_target():
    root = parsed(charts.goal_svg(goal_like(), TODAY))
    bottom = [t.text for t in of_class(root, "text", "axis") if float(t.get("y")) == charts.GOAL_H - 8]
    assert bottom == ["Wed 14 Oct", "Tue 5 Jan 2027"]
    early = goal_like(start_date="2026-11-12", path={**goal_like()["path"],
                                                     "plan": [{"date": "2026-11-12", "value": 79.9},
                                                              {"date": "2027-01-05", "value": 77.0}],
                                                     "actual": []}, latest=None, vs_plan=None)
    root = parsed(charts.goal_svg(early, TODAY))  # today before the start: a time-zone move
    bottom = [t.text for t in of_class(root, "text", "axis") if float(t.get("y")) == charts.GOAL_H - 8]
    assert bottom == ["Thu 12 Nov", "Tue 5 Jan 2027"]


def xs_of(root) -> list[float]:
    out = []
    for el in root.iter():
        for name in ("x", "x1", "x2", "cx"):
            if el.get(name) is not None:
                out.append(float(el.get(name)))
        if el.get("points"):
            out += [float(p.split(",")[0]) for p in el.get("points").split()]
    return out


@pytest.mark.parametrize("case", ["target passed", "target before start", "today before start", "wild projection",
                                  "a target of 102.5 kg", "65 bpm", "a time goal"])
def test_the_goal_charts_edge_cases_stay_inside_the_chart(case):
    """Section E1's edge cases and the widest labels: every mark and every label's extent inside the
    viewBox (parsed holds the labels), the grid enclosing every value."""
    goal = {
        "target passed": goal_like(target_date="2026-11-01", should_be_today=77.0, projected_date=None,
                                   projection={"projected": 78.6}, days_ahead=None,
                                   path={"plan": [{"date": "2026-10-14", "value": 79.9},
                                                  {"date": "2026-11-01", "value": 77.0}],
                                         "actual": goal_like()["path"]["actual"]}),
        "target before start": goal_like(start_date="2026-11-12", target_date="2026-11-11", projected_date=None,
                                         projection=None, days_ahead=None,
                                         path={"plan": [{"date": "2026-11-12", "value": 79.9},
                                                        {"date": "2026-11-11", "value": 77.0}], "actual": []}),
        "today before start": goal_like(start_date="2026-11-11", projected_date=None, projection=None,
                                        path={"plan": [{"date": "2026-11-11", "value": 79.9},
                                                       {"date": "2027-01-05", "value": 77.0}],
                                              "actual": [{"date": "2026-11-10", "value": 79.8}]}),
        "wild projection": goal_like(projected_date=None, projection={"projected": 400.0}),
        "a target of 102.5 kg": goal_like(label="Back Squat 102.5 kg", kind="lift", start_value=88.0,
                                          target_value=102.5, latest=96.0, needed_per_week=1.2,
                                          actual_per_week=1.5, projected_date="2026-12-20",
                                          path={"plan": [{"date": "2026-10-14", "value": 88.0},
                                                         {"date": "2027-01-05", "value": 102.5}],
                                                "actual": [{"date": "2026-10-14", "value": 88.0},
                                                           {"date": "2026-11-10", "value": 96.0}]}),
        "65 bpm": goal_like(label="Resting heart rate 55 bpm", kind="measurement", unit="bpm", start_value=65.0,
                            target_value=55.0, latest=63.0, needed_per_week=-1.0, actual_per_week=-0.5,
                            projected_date=None, projection=None, vs_plan="ahead", should_be_today=64.0,
                            path={"plan": [{"date": "2026-10-14", "value": 65.0},
                                           {"date": "2027-01-05", "value": 55.0}],
                                  "actual": [{"date": "2026-10-14", "value": 65.0},
                                             {"date": "2026-11-10", "value": 63.0}]}),
        "a time goal": goal_like(label="Outdoor Run 10 km in 55:00", kind="cardio_best", unit="min",
                                 start_value=63.5, target_value=55.0, latest=60.25, needed_per_week=-0.6,
                                 actual_per_week=-0.8, projected_date="2027-01-20", should_be_today=61.0,
                                 projection={"projected": 54.0, "latest": 60.25}, vs_plan="ahead",
                                 path={"plan": [{"date": "2026-10-14", "value": 63.5},
                                                {"date": "2027-01-05", "value": 55.0}],
                                       "actual": [{"date": "2026-10-14", "value": 63.5},
                                                  {"date": "2026-11-10", "value": 60.25}]}),
    }[case]
    root = parsed(charts.goal_svg(goal, TODAY))
    assert all(0 <= x <= charts.GOAL_W for x in xs_of(root)), case
    ys = [float(c.get("cy")) for c in root.iter(f"{SVG}circle")]
    ys += [float(el.get(k)) for el in of_class(root, "line", "plan") + of_class(root, "line", "projection")
           for k in ("y1", "y2")]
    inside_grid(root, ys + [float(of_class(root, "line", "target-tick")[0].get("y1"))])
    assert not [t for t in texts(root) if t.startswith("400")]
    assert len(grid_ys(root)) <= charts.MOST_TICKS
    page_text_is_clean(root)


def test_a_goal_chart_with_nothing_to_draw_is_empty():
    assert str(charts.goal_svg(None, TODAY)) == ""
    assert str(charts.goal_svg(goal_like(path={"plan": [], "actual": []}), TODAY)) == ""


SESSIONS = [{"id": n, "date": (TODAY - timedelta(days=3 + 7 * n)).isoformat(), "minutes": 30.0,
             "distance_km": 5.0, "pace": None, "pace_min_per_km": round(5.9 + 0.1 * n, 2), "notes": None,
             "is_sample": False} for n in range(4)]  # newest first, as the read gives them
SESSIONS.insert(1, {**SESSIONS[0], "id": 9, "distance_km": None, "pace_min_per_km": None})  # no distance


def test_the_pace_chart_is_a_dot_per_session_with_its_pace():
    root = parsed(charts.pace_svg(SESSIONS, "Outdoor <Run>", TODAY))
    assert root.find(f"{SVG}title").text == "Pace per km: Outdoor <Run>"
    dots = list(root.iter(f"{SVG}circle"))
    assert len(dots) == 4 and classes(dots[-1]) == ["dot", "is-latest"]
    xs = [float(d.get("cx")) for d in dots]
    assert xs == sorted(xs) and float(dots[0].get("cy")) < float(dots[-1].get("cy"))  # oldest left, slowest high
    assert texts(root, "value")[-1] == "5:54" and set(texts(root, "value")) <= {"6:12", "6:06", "6:00", "5:54"}
    assert "Sat 7 Nov" in texts(root)  # the latest keeps its own; earlier ones give way where they collide
    ticks = [t for t in texts(root) if t not in texts(root, "value") and ":" in t]
    assert len(ticks) >= 2 and all(re.fullmatch(r"\d+:\d\d", t) for t in ticks)
    inside_grid(root, [float(d.get("cy")) for d in dots])
    desc = root.find(f"{SVG}desc").text
    assert "Sat 7 Nov: 5:54." in desc and "Sat 17 Oct: 6:12." in desc and "Lower is faster." in desc
    page_text_is_clean(root)


@pytest.mark.parametrize("count", [2, 3, 9, 12, 20, 40])
def test_crowded_pace_labels_give_way_and_the_latest_keeps_its_own(count):
    """parsed holds every label inside the canvas and clear of the others, by its estimated width."""
    many = [{**SESSIONS[0], "date": (TODAY - timedelta(days=n)).isoformat(), "pace_min_per_km": 12.5 - n / 100}
            for n in range(count)]
    root = parsed(charts.pace_svg(many, "Treadmill Walk", TODAY))
    assert len(list(root.iter(f"{SVG}circle"))) == count
    dates = [t.text for t in of_class(root, "text", "axis") if float(t.get("y")) == charts.PACE_H - 8]
    assert dates[-1] == "Tue 10 Nov" and len(dates) >= 1
    assert texts(root, "value")[-1] == formatting.show(12.5, "pace")


def test_pace_dates_with_a_year_fit_or_give_way():
    old = [{**SESSIONS[0], "date": f"2025-12-{d:02d}", "pace_min_per_km": 6.0 + d / 100} for d in range(31, 20, -1)]
    root = parsed(charts.pace_svg(old, "Outdoor Run", TODAY))
    dates = [t.text for t in of_class(root, "text", "axis") if float(t.get("y")) == charts.PACE_H - 8]
    assert dates and dates[-1] == "Wed 31 Dec 2025" and len(dates) < 11


def test_a_pace_chart_needs_two_sessions_with_a_distance():
    assert str(charts.pace_svg(SESSIONS[:2], "Outdoor Run", TODAY)) == ""
    assert str(charts.pace_svg([], "Outdoor Run", TODAY)) == "" and str(charts.pace_svg(None, "Outdoor Run", TODAY)) == ""


def test_ticks_are_capped_and_never_outside_the_plot():
    assert all(len(charts._ticks(lo, hi, steps)) <= charts.MOST_TICKS for lo, hi, steps in (
        (3, 600, charts.SECOND_STEPS), (1, 900, charts.LIFT_STEPS), (210, 330, charts.SECOND_STEPS),
        (0.5, 9000, charts.KG_STEPS), (20, 400, charts.WHOLE_STEPS)))
    walks = [{**SESSIONS[0], "date": (TODAY - timedelta(days=n)).isoformat(), "pace_min_per_km": p}
             for n, p in enumerate((3.0, 600.0, 9.0))]
    root = parsed(charts.pace_svg(walks, "Treadmill Walk", TODAY))
    assert len(grid_ys(root)) <= 4
    inside_grid(root, [float(c.get("cy")) for c in root.iter(f"{SVG}circle")])
    heavy = {**CHART, "points": [{"date": "2026-10-01", "value": 1.0}, {"date": "2026-10-08", "value": 900.0}],
             "goal": None}
    root = parsed(chart_svg(heavy, today=TODAY))
    assert len(grid_ys(root)) <= charts.MOST_TICKS
    huge = [{"date": (TODAY - timedelta(days=1)).isoformat(), "kg": 40, "smoothed": 40.0},
            {"date": TODAY.isoformat(), "kg": 300, "smoothed": 300.0}]
    root = parsed(charts.weight_sheet_svg(huge, TODAY))
    assert len(grid_ys(root)) == 3
    inside_grid(root, [float(c.get("cy")) for c in root.iter(f"{SVG}circle")])


WIDE_LABELS = {
    "lift at 102.5 kg": lambda: chart_svg({**CHART, "points": [{"date": "2025-12-30", "value": 95.0},
                                                               {"date": "2026-11-03", "value": 101.0}],
                                           "latest": {"date": "2026-11-03", "value": 101.0},
                                           "goal": {"target_kg": 102.5, "target_date": "2027-02-02",
                                                    "status": "on track", "projected": 104.0}}, today=TODAY),
    "body weight to 105 kg": lambda: bodyweight_svg({"points": [{"date": "2025-12-29", "kg": 104.2, "smoothed": 104.2},
                                                                {"date": "2026-11-10", "kg": 98.7, "smoothed": 99.95}],
                                                     "latest": {"date": "2026-11-10", "kg": 98.7},
                                                     "goal": {"target_kg": 95.5, "target_date": "2027-01-05"}},
                                                    today=TODAY),
    "weight sheet at 80.2 kg": lambda: charts.weight_sheet_svg(
        [{"date": "2025-12-30", "kg": 80.2, "smoothed": 80.2}, {"date": "2026-11-10", "kg": 79.4, "smoothed": 79.6}],
        TODAY),
    "eaten sheet over 10,000 kcal": lambda: charts.eaten_sheet_svg(
        [{**d, "kcal": 12345 - n} for n, d in enumerate(DAYS)], TODAY),
}


@pytest.mark.parametrize("name", sorted(WIDE_LABELS))
def test_the_widest_labels_stay_inside_their_chart(name):
    root = parsed(WIDE_LABELS[name]())  # parsed holds every label's extent inside the viewBox
    page_text_is_clean(root)


def test_a_lift_with_one_session_writes_its_date_once():
    one = {**CHART, "points": CHART["points"][:1], "latest": CHART["points"][0], "goal": None}
    root = parsed(chart_svg(one, today=TODAY))
    assert [t for t in texts(root) if not t[0].isdigit()] == ["Thu 1 Oct"]
    assert root.find(f"{SVG}desc").text.startswith("1 session on Thu 1 Oct. ")


def test_counts_in_a_desc_take_their_right_word():
    assert parsed(chart_svg(CHART)).find(f"{SVG}desc").text.startswith("3 sessions from ")
    assert parsed(bodyweight_svg(WEIGHED)).find(f"{SVG}desc").text.startswith("3 weigh-ins from ")


def test_the_lift_desc_names_a_projection_only_when_one_is_drawn():
    past = {**CHART, "goal": {**CHART["goal"], "target_date": "2026-10-10"}}
    root = parsed(chart_svg(past))
    assert "projection" not in lines(root) and "projected" not in root.find(f"{SVG}desc").text
    root = parsed(chart_svg(CHART))
    assert "projection" in lines(root) and "projected 57.4 kg" in root.find(f"{SVG}desc").text


def _drawn_xs(root) -> list[float]:
    [line] = [el for el in root.iter(f"{SVG}polyline") if "series" in classes(el)]
    return [float(p.split(",")[0]) for p in line.get("points").split()]


def test_a_point_dated_after_today_counts_as_today():
    """E1, every chart: never drawn past today's edge, and the line never doubles back."""
    later = [{"date": "2026-11-05", "kg": 80.0, "smoothed": 80.0}, {"date": "2026-11-11", "kg": 79.6, "smoothed": 79.8},
             {"date": "2026-11-10", "kg": 79.7, "smoothed": 79.7}]
    for svg in (charts.weight_sheet_svg(later, TODAY), charts.weight_small_svg(later, TODAY),
                bodyweight_svg({"points": later, "latest": later[-1], "goal": None}, today=TODAY)):
        root = parsed(svg)
        xs = _drawn_xs(root)
        assert xs == sorted(xs) and xs[-1] == xs[-2], xs  # the 11th sits on the 10th, today
        assert max(xs_of(root)) <= float(root.get("viewBox").split()[2])
    lift = {**CHART, "points": [{"date": "2026-11-05", "value": 50.0}, {"date": "2026-11-12", "value": 52.0},
                                {"date": "2026-11-10", "value": 51.0}], "latest": {"date": "2026-11-12", "value": 52.0},
            "goal": None}
    xs = _drawn_xs(parsed(chart_svg(lift, today=TODAY)))
    assert xs == sorted(xs) and xs[-1] == xs[-2]
    goal = goal_like(path={**goal_like()["path"], "actual": goal_like()["path"]["actual"][:-1] +
                           [{"date": "2026-11-12", "value": 79.0}]})
    root = parsed(charts.goal_svg(goal, TODAY))
    [today_line] = of_class(root, "line", "today-line")
    assert max(_drawn_xs(root)) == float(today_line.get("x1"))


ZERO = [{**d, "kcal": 0} for d in DAYS]


def test_0_kcal_is_food_and_null_is_none():
    """E2: kcal is null only on a day with no food entry. A day at 0 kcal has food, its figure, no bar."""
    small, sheet = parsed(charts.eaten_small_svg(ZERO, TODAY)), parsed(charts.eaten_sheet_svg(ZERO, TODAY))
    for root in (small, sheet):
        assert not list(root.iter(f"{SVG}rect")) and "Tue 10 Nov: 0 kcal." in root.find(f"{SVG}desc").text
        assert "no food logged" not in root.find(f"{SVG}desc").text
    assert texts(sheet, "value")[-1] == "0"
    mixed = [{**d, "kcal": None} for d in DAYS[:-1]] + [{**DAYS[-1], "kcal": 0}]
    assert str(charts.eaten_small_svg(mixed, TODAY)) and str(charts.eaten_sheet_svg(mixed, TODAY))


def test_text_from_the_data_is_escaped_in_every_new_chart():
    odd = '"><script>x</script>'
    for svg in (charts.goal_svg(goal_like(label=odd), TODAY, element_id=odd), charts.pace_svg(SESSIONS, odd, TODAY),
                charts.weight_small_svg(SERIES, TODAY, element_id=odd), charts.eaten_sheet_svg(DAYS, TODAY, element_id=odd)):
        root = parsed(svg)
        assert not list(root.iter("script")) and odd in root.get("id") + root.find(f"{SVG}title").text


class _Recorder:
    """app.formatting, with every string its functions return kept."""

    def __init__(self):
        self.said: set[str] = set()

    def __getattr__(self, name):
        real = getattr(formatting, name)
        if not callable(real):
            return real

        def call(*args, **kwargs):
            out = real(*args, **kwargs)
            if isinstance(out, str):
                self.said.add(out)
            return out

        return call


FIXED_WORDS = re.compile(r"last 4 weeks|last 7 days")


def test_every_number_in_a_chart_comes_from_the_formatter(monkeypatch):
    """Take out of each text every string the formatter returned while drawing it, longest first, and
    the chart's fixed words: no digit may be left. A figure the chart wrote itself fails."""
    drawings = (lambda: chart_svg(CHART), lambda: bodyweight_svg(WEIGHED), lambda: charts.weight_sheet_svg(SERIES, TODAY),
                lambda: charts.weight_small_svg(SERIES, TODAY), lambda: charts.eaten_sheet_svg(DAYS, TODAY),
                lambda: charts.eaten_small_svg(DAYS, TODAY, 2400), lambda: charts.goal_svg(goal_like(), TODAY),
                lambda: charts.pace_svg(SESSIONS, "Outdoor Run", TODAY), WIDE_LABELS["lift at 102.5 kg"])
    for draw in drawings:
        recorder = _Recorder()
        monkeypatch.setattr(charts, "formatting", recorder)
        root = ET.fromstring(str(draw()))
        monkeypatch.setattr(charts, "formatting", formatting)
        said = sorted(recorder.said | {goal_like()["plan_line"]}, key=len, reverse=True)  # the service's text
        words = [t.text for t in root.iter(f"{SVG}text")] + [t.text for t in root.iter(f"{SVG}title")]
        for text in words + [root.find(f"{SVG}desc").text]:
            rest = FIXED_WORDS.sub("", text)
            for piece in said:
                rest = rest.replace(piece, "")
            assert not re.search(r"\d", rest), (text, rest)


# ---------------------------------------------------------------- every chart from the real reads
# The sample account on the sample tests' fixed day, an empty account, and an account with one entry of
# each kind. Each desc holds exactly show of the read's own figures (T7); parsed holds every label.

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # a Tuesday: tests/test_sample.py's fixed day
FIXED = date(2026, 11, 10)


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def sampled(make_user, fixed_clock):
    user = make_user()
    assert "error" not in services.add_sample_data(user.id, "web")
    assert users.today_for(user) == FIXED
    return user


@pytest.fixture
def one_of_each(make_user, fixed_clock):
    """One entry of each kind, today: a weigh-in, a food entry, a lift session, a cardio session with a
    distance, a measurement, and a body weight goal."""
    user = make_user()
    for result in (services.log_bodyweight(user.id, {"kg": 80.4}, "claude"),
                   services.log_food(user.id, {"entries": [{"description": "Dal", "kcal": 450}]}, "claude"),
                   services.log_lifts(user.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]},
                                      "claude"),
                   services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5},
                                       "claude"),
                   services.log_measurement(user.id, {"kind": "waist", "value": 86.5}, "claude"),
                   services.set_goal(user.id, {"kind": "bodyweight", "target_value": 77,
                                               "target_date": "2027-01-05"}, "claude")):
        assert "error" not in result, result
    return user


def desc_of(root) -> str:
    return root.find(f"{SVG}desc").text


def test_the_weight_charts_from_get_trends(sampled):
    trends = services.get_trends(sampled.id)
    series, today = trends["bodyweight"]["series"], date.fromisoformat(trends["today"])
    first, last = series[0], series[-1]
    said = (f"Smoothed body weight over the last 4 weeks, from {formatting.show(first['smoothed'], 'bodyweight_derived')} "
            f"on {formatting.day(first['date'], today)} to {formatting.show(last['smoothed'], 'bodyweight_derived')} "
            f"on {formatting.day(last['date'], today)}.")
    for draw in (charts.weight_small_svg, charts.weight_sheet_svg):
        root = parsed(draw(series, today))
        assert desc_of(root) == said
        [line] = of_class(root, "polyline", "series")
        assert len(line.get("points").split()) == len(series) == trends["bodyweight"]["weigh_ins_28d"]
        page_text_is_clean(root)
    assert formatting.show(last["smoothed"], "bodyweight_derived") == \
        formatting.show(trends["bodyweight"]["smoothed"], "bodyweight_derived")


def test_the_eaten_charts_from_get_days(sampled):
    days = services.get_days(sampled.id)
    target = services.get_trends(sampled.id)["calories"]["target_kcal"]
    today = date.fromisoformat(days["today"])
    week = days["days"][-7:]
    said = ["Calories eaten each day, last 7 days."] + [
        f"{formatting.day(d['date'], today)}: {formatting.show(d['kcal'], 'kcal')}." for d in week]
    small = parsed(charts.eaten_small_svg(days["days"], today, target))
    assert desc_of(small) == " ".join(said + ([f"Calorie target {formatting.show(target, 'kcal')}."] if target else []))
    assert len(list(small.iter(f"{SVG}rect"))) == 7 and classes(list(small.iter(f"{SVG}rect"))[-1]) == ["bar", "is-today"]
    sheet = parsed(charts.eaten_sheet_svg(days["days"], today))
    assert desc_of(sheet) == " ".join(said)
    assert texts(sheet, "value")[-1] == formatting.show(week[-1]["kcal"], "kcal", unit=False)
    for root in (small, sheet):
        page_text_is_clean(root)


def test_the_body_weight_chart_as_progress_calls_it(sampled):
    from app import web

    # A weigh-in older than the sample's four weeks: the page draws the 28 days only.
    assert "error" not in services.log_bodyweight(sampled.id, {"kg": 81.5, "date": "2026-10-01"}, "claude")
    chart = web._trend(sampled.id)["bodyweight"]
    trends = services.get_trends(sampled.id)
    assert chart["points"] == trends["bodyweight"]["series"]  # since 28 days back: the same points
    assert chart["points"][0]["date"] == "2026-10-14"
    root = parsed(bodyweight_svg(chart, today=FIXED))
    points, goal = chart["points"], chart["goal"]
    assert desc_of(root) == (
        f"{formatting.show(len(points), 'count')} weigh-ins from {formatting.day(points[0]['date'], FIXED)} to "
        f"{formatting.day(points[-1]['date'], FIXED)}. Latest {formatting.show(chart['latest']['kg'], 'weigh_in')} on "
        f"{formatting.day(chart['latest']['date'], FIXED)}. The line is the smoothed trend, now "
        f"{formatting.show(trends['bodyweight']['smoothed'], 'bodyweight_derived')}. Goal "
        f"{formatting.show(goal['target_kg'], 'goal_target')} kg by {formatting.day(goal['target_date'], FIXED)}.")
    assert len(list(root.iter(f"{SVG}circle"))) == 28
    page_text_is_clean(root)


def test_each_goal_chart_from_goal_charts(sampled):
    from app.services import goals as goal_reads

    read = services.goal_charts(sampled.id)
    today = date.fromisoformat(read["today"])
    assert len(read["goals"]) == 3
    for goal in read["goals"]:
        quantity = goal_reads.goal_quantity(goal["kind"], goal["unit"])
        assert charts.goal_quantity(goal) == quantity
        root = parsed(charts.goal_svg({**goal, "line_ended": False}, today))  # an extra key changes nothing
        assert str(charts.goal_svg(goal, today)) == str(charts.goal_svg({**goal, "line_ended": True}, today))
        desc = desc_of(root)
        figure = lambda v: formatting.show(v, quantity)  # noqa: E731
        assert f"Started {figure(goal['start_value'])} on {formatting.day(goal['start_date'], today)}." in desc
        assert goal["plan_line"] and desc.split(". ", 1)[1].startswith(goal["plan_line"] + " Target ")
        assert f"by {formatting.day(goal['target_date'], today)}." in desc
        rate = (lambda v: formatting.show(v, "time_pace")) if quantity == "goal_time" else \
            (lambda v: f"{formatting.show(v, 'rate')} {goal['unit']} a week")
        assert f"Needed {rate(goal['needed_per_week'])}." in desc and f"So far {rate(goal['actual_per_week'])}." in desc
        assert desc.endswith(f"At this pace it is reached on {formatting.day(goal['projected_date'], today)}.")
        assert root.find(f"{SVG}title").text == f"{goal['label']} by {formatting.day(goal['target_date'], today)}"
        [ring] = of_class(root, "circle", "is-latest")
        assert ring.find(f"{SVG}title").text == f"{formatting.day(today, today)}: {figure(goal['latest'])}"
        assert len(of_class(root, "line", "projection")) == 1
        page_text_is_clean(root)


def test_the_pace_chart_from_get_cardio(sampled):
    read = services.get_cardio(sampled.id, "Outdoor Run")
    today = date.fromisoformat(read["today"])
    root = parsed(charts.pace_svg(read["sessions"], read["exercise"], today))
    oldest_first = list(reversed(read["sessions"]))
    assert desc_of(root) == " ".join(
        ["Pace per km for each Outdoor Run session, oldest first."]
        + [f"{formatting.day(s['date'], today)}: {formatting.show(s['pace_min_per_km'], 'pace')}." for s in oldest_first]
        + ["Lower is faster."])
    assert [formatting.show(s["pace_min_per_km"], "pace") for s in oldest_first] == [s["pace"] for s in oldest_first]
    assert texts(root, "value")[-1] == read["bests"]["fastest_pace"]["pace"]  # the latest is the fastest
    page_text_is_clean(root)


def test_the_lift_charts_from_get_progress(sampled):
    from app import web

    read = services.get_progress(sampled.id, "Back Squat")
    lift = read["lift"]
    chart = lift_chart(web._lift_points(lift), lift["goals"], lift["exercise"])
    today = date.fromisoformat(read["today"])
    root = parsed(chart_svg(chart, today=today))
    points, goal = lift["estimated_max_over_time"], chart["goal"]
    desc = desc_of(root)
    assert desc.startswith(
        f"{formatting.show(len(points), 'count')} sessions from {formatting.day(points[0]['date'], today)} to "
        f"{formatting.day(points[-1]['date'], today)}. Latest {formatting.show(chart['latest']['value'], 'estimated_max')} "
        f"on {formatting.day(chart['latest']['date'], today)}. Goal "
        f"{formatting.show(goal['target_kg'], 'goal_target')} kg by {formatting.day(goal['target_date'], today)}: "
        f"{goal['status']}")
    drawn = bool(lines(root).get("projection") is not None)
    assert (f", projected {formatting.show(goal['projected'], 'estimated_max')}." in desc) == drawn
    page_text_is_clean(root)
    pull = services.get_progress(sampled.id, "Pull-Up")["lift"]
    assert lift_chart(web._lift_points(pull), pull["goals"], pull["exercise"]) is None  # no added weight: no chart
    assert str(chart_svg(None)) == ""
    with db.new_session() as s:
        assert lifts.history(s, sampled.id, today)["reps_only"]["Pull-Up"]["reps"] == 6


def test_every_chart_is_empty_on_an_empty_account(make_user, fixed_clock):
    from app import web

    user = make_user()
    trends, days = services.get_trends(user.id), services.get_days(user.id)
    today = date.fromisoformat(days["today"])
    series = (trends.get("bodyweight") or {}).get("series")
    assert str(charts.weight_small_svg(series, today)) == "" and str(charts.weight_sheet_svg(series, today)) == ""
    assert str(charts.eaten_small_svg(days["days"], today)) == "" and str(charts.eaten_sheet_svg(days["days"], today)) == ""
    assert web._trend(user.id)["bodyweight"] is None and str(bodyweight_svg(None)) == ""
    assert services.goal_charts(user.id)["goals"] == []
    run = services.get_cardio(user.id, "Outdoor Run")
    assert run["sessions"] == [] and str(charts.pace_svg(run["sessions"], run["exercise"], today)) == ""
    squat = services.get_progress(user.id, "Back Squat")["lift"]
    assert lift_chart(web._lift_points(squat), squat["goals"], squat["exercise"]) is None


def test_one_entry_of_each_kind_draws_what_one_point_can(one_of_each):
    from app import web

    user = one_of_each
    trends, days = services.get_trends(user.id), services.get_days(user.id)
    today = date.fromisoformat(days["today"])
    series = trends["bodyweight"]["series"]
    assert len(series) == 1  # one weigh-in: the page says the chart starts from the second
    assert str(charts.weight_small_svg(series, today)) == "" and str(charts.weight_sheet_svg(series, today)) == ""
    assert web._trend(user.id)["bodyweight"] is None
    small, sheet = parsed(charts.eaten_small_svg(days["days"], today)), parsed(charts.eaten_sheet_svg(days["days"], today))
    for root in (small, sheet):
        assert len(list(root.iter(f"{SVG}rect"))) == 1 and "Tue 10 Nov: 450 kcal." in desc_of(root)
        assert "Mon 9 Nov: no food logged." in desc_of(root)
    run = services.get_cardio(user.id, "Outdoor Run")
    assert len(run["sessions"]) == 1 and str(charts.pace_svg(run["sessions"], run["exercise"], today)) == ""
    squat = services.get_progress(user.id, "Back Squat")["lift"]
    root = parsed(chart_svg(lift_chart(web._lift_points(squat), squat["goals"], squat["exercise"]), today=today))
    assert desc_of(root).startswith("1 session on Tue 10 Nov. Latest ")
    assert [t for t in texts(root) if not t[0].isdigit()] == ["Tue 10 Nov"]
    [goal] = services.goal_charts(user.id)["goals"]
    root = parsed(charts.goal_svg(goal, today))
    assert [classes(c) for c in root.iter(f"{SVG}circle")] == [["dot", "is-latest"]]
    assert not root.findall(f"{SVG}polyline") and of_class(root, "line", "plan")
    assert goal["plan_line"] and goal["plan_line"] in desc_of(root)
    page_text_is_clean(root)


def test_a_goal_with_nothing_logged_says_so_from_the_real_read(make_user, fixed_clock):
    """A goal set from a given start with nothing logged: plan_line is null, so the desc says nothing is
    logged and gives no now figure."""
    user = make_user()
    assert "error" not in services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "waist",
                                                      "target_value": 84, "target_date": "2027-02-02",
                                                      "start_value": 90}, "claude")
    [goal] = services.goal_charts(user.id)["goals"]
    assert goal["plan_line"] is None and goal["latest"] is None
    root = parsed(charts.goal_svg(goal, FIXED))
    desc = desc_of(root)
    assert desc.startswith(f"Started {formatting.show(90, 'cm')} on Tue 10 Nov. Nothing logged yet. Target 84 cm by ")
    assert not of_class(root, "circle", "is-latest") and not of_class(root, "line", "projection")
    page_text_is_clean(root)
