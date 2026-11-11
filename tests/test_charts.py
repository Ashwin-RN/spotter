"""app/charts.py, and the chart data app.services.progress builds for it."""

import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, metrics, services, users
from app.charts import bodyweight_svg, chart_svg
from app.metrics import bodyweight_average
from app.models import Goal
from app.services import body, lifts
from app.services.goals import goal_numbers, load_numbers
from app.services.progress import bodyweight_chart, lift_chart

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
    labels = [t.text for t in root.iter(f"{SVG}text") if t.get("class") == "axis"]
    assert "400" not in labels and "projected 400 kg" in root.find(f"{SVG}desc").text


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
