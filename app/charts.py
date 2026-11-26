"""Inline SVG charts for the web pages. Pure functions: a service's figures in, markup out, and a goal's quantity
asked of the service that sets it (goals.goal_quantity).

  chart_svg(chart)                    a lift's estimated max over time, its goal line and projection
  bodyweight_svg(chart)               Progress: weigh-ins, the smoothed trend, the goal line
  weight_small_svg(series, today)     Today's chart card: the smoothed weight over 4 weeks, no axis
  weight_sheet_svg(series, today)     the Weight sheet: the same line, three labelled grid lines
  eaten_small_svg(days, today)        Today's chart card: a bar a day for 7 days, the calorie target
  eaten_sheet_svg(days, today)        the Calories sheet: seven bars, each with its kcal and weekday
  goal_svg(goal, today)               Goals: the plan line, the actual line, the projection, today
  pace_svg(sessions, activity, today) an activity's page: a dot per session with its pace

Units (contract units.md R17). Each chart of a weight, a distance or a pace takes units, the person's (None is kg
and km), and is drawn in the shown unit: every point, the goal line and the projection are placed by
formatting.convert, the tick lists are the same (KG_STEPS serves lb), and every label is in that unit. A tick is
already a number in the shown unit, so its label is show(unit=False) with no units; every figure from the data is
show(stored, units=units), never a converted number. A goal of a weight takes its word from unit_word, never from
its stored unit, which stays "kg".

The data comes from app.services: progress (lift_chart, bodyweight_chart), get_trends, get_days,
goal_charts and get_cardio. Every chart is one <svg> with role="img", a <title> and a <desc> that state
its figures, so it reads without the picture. Text from the data is escaped. Colours, strokes, text
sizes and motion come from the stylesheet, through the class names: no style attribute, no script, no
outside reference. Every series line carries pathLength="1", which the stylesheet draws in once on load.

Every number and date goes through app.formatting, with a plain space before a unit (text inside an
SVG): show, with unit=False on an axis tick, and day. today is the person's own date, for the date
form, and a point dated after it counts as today (section E1). A chart with too little to draw returns
empty markup, and the page says why in its own words.

Text never leaves the canvas. Each margin is worked out from the widest label it holds, at the text
size the stylesheet sets on a phone (AXIS, SMALL_AXIS chart units), the largest a chart's text is
drawn at in its own units. A label is placed by its estimated width, and labels that would collide give
way, the latest keeping its own.
"""

import math
from datetime import date, timedelta

from markupsafe import Markup, escape

from app import formatting
from app.services import goals

SVG_NS = "http://www.w3.org/2000/svg"

# Text size in chart units: the stylesheet's svg.chart .axis and svg.chart.small .axis on a phone, the
# largest any chart text is drawn at. tests/test_css.py holds the stylesheet to these.
AXIS = 15
SMALL_AXIS = 30
# A character's width in ems, by kind, on the wide side of the system fonts (Segoe UI, Roboto, SF):
# figures are tabular. M, W, m and w are wider than other letters (Segoe UI's W is 0.934 em), so they
# take their own width. Text set at 600 and up is a little wider.
EMS = {"digit": 0.58, "narrow": 0.3, "upper": 0.68, "lower": 0.54, "wide": 0.94}
WIDE = frozenset("MWmw")
BOLDER = 1.06
ROOM = 4  # headroom beyond a label's estimated width
GAP = 6   # between a label and what it labels, and between two labels

# Scale steps a grid may use, in each quantity's stored unit, finest first. None finer than the
# quantity's shown step; a time in whole seconds. Past the end of a list, the steps go on growing.
KG_STEPS = (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100)
WHOLE_STEPS = (1, 2, 5, 10, 20, 50, 100)
LIFT_STEPS = (1, 2, 2.5, 5, 10, 20, 25, 50, 100)
SECOND_STEPS = tuple(s / 60 for s in (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1200))
STEPS = {"bodyweight_derived": KG_STEPS, "estimated_max": KG_STEPS, "cm": KG_STEPS, "bpm": WHOLE_STEPS,
         "goal_time": SECOND_STEPS, "pace": SECOND_STEPS}
MOST_TICKS = 5


# ---------------------------------------------------------------- the parts every chart shares

def _c(value: float) -> str:
    """A coordinate: two decimals at most, no trailing zeros. Geometry, not a figure a person reads."""
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded == int(rounded) else repr(rounded)


def _date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _on(d: date, today: date | None) -> date:
    """The date a point is drawn at: a point dated after today counts as today (E1)."""
    return d if today is None or d <= today else today


def _em(ch: str) -> float:
    if ch.isdigit():
        return EMS["digit"]
    if ch in " ,.:;'-":
        return EMS["narrow"]
    if ch in WIDE:
        return EMS["wide"]
    return EMS["upper"] if ch.isupper() else EMS["lower"]


def text_width(text: str, size: float = AXIS, bold: bool = False) -> float:
    """A label's estimated width in chart units."""
    return sum(_em(ch) for ch in text) * size * (BOLDER if bold else 1)


def _count(n: int, one: str, many: str) -> str:
    return f"{formatting.show(n, 'count')} {one if n == 1 else many}"


def _svg_open(element_id: str, title: str, desc: str, width: int, height: int, small: bool = False) -> list[str]:
    """The opening tag, title and description. Every piece of text, the element id included, is escaped."""
    eid = escape(element_id)
    tid, did = f"{eid}-title", f"{eid}-desc"
    kind = "chart small" if small else "chart"
    return [f'<svg class="{kind}" id="{eid}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{tid} {did}" xmlns="{SVG_NS}">',
            f'<title id="{tid}">{escape(title)}</title>', f'<desc id="{did}">{escape(desc)}</desc>']


def _close(out: list[str]) -> Markup:
    out.append("</svg>")
    return Markup("\n".join(out))


def _steps(steps: tuple):
    """The steps in order, then on past the last: times 2, 5 and 10, and so on."""
    yield from steps
    base = steps[-1]
    for _ in range(12):
        for m in (2, 5, 10):
            yield base * m
        base *= 10


def _ticks(lo: float, hi: float, steps: tuple, most: int = MOST_TICKS) -> list[float]:
    """Evenly spaced scale marks, the outer two enclosing lo to hi: the finest step that needs at most
    `most` marks. Never more than `most`."""
    lo, hi = min(lo, hi), max(lo, hi)
    for step in _steps(steps):
        start = math.floor(lo / step + 1e-9) * step
        end = math.ceil(hi / step - 1e-9) * step
        if end - start < step / 2:
            end = start + step
        count = int(round((end - start) / step)) + 1
        if count <= most:
            return [round(start + n * step, 6) for n in range(count)]
    return [lo, hi]


def _three_ticks(lo: float, hi: float, steps: tuple) -> list[float]:
    """Exactly three scale marks, evenly spaced, enclosing lo to hi: the finest step that reaches."""
    for step in _steps(steps):
        start = math.floor(min(lo, hi) / step + 1e-9) * step
        if start + 2 * step >= max(lo, hi) - 1e-9:
            return [round(start + n * step, 6) for n in range(3)]
    return [lo, (lo + hi) / 2, hi]


def _scale(lo: float, hi: float, top: float, bottom: float):
    """value -> y, with lo at the bottom and hi at the top."""
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return bottom - (v - lo) / span * (bottom - top)

    return y


def _dates(first: date, last: date, left: float, right: float):
    """date -> x, from first at the left to last at the right."""
    span = max((last - first).days, 1)

    def x(d: date) -> float:
        return left + (d - first).days / span * (right - left)

    return x


def _tick_labels(ticks: list[float], quantity: str, unit: str | None) -> list[str]:
    """Each tick's label at the quantity's shown step. The unit is named once, on the top tick."""
    labels = [formatting.show(v, quantity, unit=False) for v in ticks]
    if unit:
        labels[-1] = f"{labels[-1]} {unit}"
    return labels


def _margin(labels: list[str], size: float = AXIS) -> float:
    """The room a column of end-anchored labels needs, with the gap to the plot."""
    return max(text_width(t, size) for t in labels) + GAP + ROOM


def _grid(out: list[str], ticks: list[float], labels: list[str], y, left: float, right: float) -> None:
    """A grid line and its label at each tick, the labels end-anchored at the plot's left edge."""
    for v, label in zip(ticks, labels):
        out.append(f'<line class="grid" x1="{_c(left)}" x2="{_c(right)}" y1="{_c(y(v))}" y2="{_c(y(v))}" />')
        out.append(f'<text class="axis end" x="{_c(left - GAP)}" y="{_c(y(v) + AXIS / 3)}">{escape(label)}</text>')


def _box(text: str, centre: float, width: float, size: float = AXIS, bold: bool = False,
         start: float = ROOM / 2) -> tuple[float, float]:
    """A label's left and right edge, centred on `centre` where it fits, held inside start to width."""
    w = text_width(text, size, bold)
    left = min(max(centre - w / 2, start), width - w - ROOM / 2)
    return left, left + w


def _label(out: list[str], cls: str, text: str, box: tuple[float, float], y: float) -> None:
    """A label written start-anchored at its box's left edge."""
    out.append(f'<text class="{cls}" x="{_c(box[0])}" y="{_c(y)}">{escape(text)}</text>')


def _end_dates(out: list[str], first: tuple[date, float], last: tuple[date, float], y_text: float,
               width: float, today: date) -> None:
    """The first date under the plot's left end and the last under its right end, each inside the
    canvas. When they would meet, or are one date, only the last is written."""
    first_text, last_text = formatting.day(first[0], today), formatting.day(last[0], today)
    w_first, w_last = text_width(first_text), text_width(last_text)
    left_box = (max(first[1], ROOM / 2), max(first[1], ROOM / 2) + w_first)
    right_edge = min(max(last[1], w_last + ROOM / 2), width - ROOM / 2)
    right_box = (right_edge - w_last, right_edge)
    if first[0] != last[0] and left_box[1] + GAP <= right_box[0]:
        _label(out, "axis", first_text, left_box, y_text)
    _label(out, "axis", last_text, right_box, y_text)


def _points(pairs) -> str:
    return " ".join(f"{_c(x)},{_c(y)}" for x, y in pairs)


def _goal_weight(value, units=None) -> str:
    """A goal's target weight, as entered, in the person's unit: "100 kg", "220.45 lb"."""
    return f"{formatting.show(value, 'weight_target', units=units)} {formatting.unit_word('weight', units)}"


# ---------------------------------------------------------------- a lift: estimated max over time

LIFT_W, LIFT_H = 360, 200
LIFT_TOP, LIFT_BOTTOM = 24, 166


def chart_svg(chart: dict | None, element_id: str = "progress-chart", today: date | None = None,
              units=None) -> Markup:
    """Estimated max per session for one lift, a horizontal goal line at the target, and the
    projection as a dotted line from the latest point to the projected value at the goal date. A lift
    page's chart card. units: the person's; the chart is drawn in its weight unit.

    chart: {lift, points [{date, value}], latest {date, value}, goal {target_kg, target_date, status,
    projected} | None}, or None (renders nothing). A goal date already passed draws no projection, and
    then the desc names none. A projection far outside the data is held inside the plot instead of
    stretching it; the desc keeps its figure.
    """
    if not chart or not chart.get("points"):
        return Markup("")
    def shown(v: float) -> float:  # a stored kg in the unit the chart is drawn in
        return formatting.convert(v, "estimated_max", units)

    raw = [(_date(p["date"]), float(p["value"])) for p in chart["points"]]
    today = _date(today) if today else max(d for d, _ in raw)
    pts = sorted(((_on(d, today), shown(v), d, v) for d, v in raw), key=lambda p: p[0])
    goal = chart.get("goal") or None
    stored_target = float(goal["target_kg"]) if goal and goal.get("target_kg") is not None else None
    target = shown(stored_target) if stored_target is not None else None
    goal_date = _date(goal["target_date"]) if goal and goal.get("target_date") else None
    projected = shown(float(goal["projected"])) if goal and goal.get("projected") is not None else None

    if goal_date is not None and goal_date <= pts[-1][0]:
        projected = None  # the goal date has passed: nothing left to project
    drawn = projected is not None and goal_date is not None
    first = pts[0][0]
    last = max([pts[-1][0]] + ([goal_date] if drawn else []))
    # The y-range comes from the sessions and the target; a wild projection is held inside it, not followed.
    values = [v for _, v, _, _ in pts] + ([target] if target is not None else [])
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, 2.5)
    ticks = _ticks(max(0.0, lo - pad), hi + pad, LIFT_STEPS)
    labels = _tick_labels(ticks, "estimated_max", formatting.unit_word("weight", units))
    left, right = _margin(labels), LIFT_W - ROOM
    if drawn:
        projected = min(max(projected, ticks[0]), ticks[-1])
    y = _scale(ticks[0], ticks[-1], LIFT_TOP, LIFT_BOTTOM)
    x = _dates(first, last, left + 6, right - 6)

    latest = chart["latest"]
    n = len(pts)
    span = (f"on {formatting.day(raw[0][0], today)}" if n == 1 else
            f"from {formatting.day(raw[0][0], today)} to {formatting.day(raw[-1][0], today)}")
    desc = (f"{_count(n, 'session', 'sessions')} {span}. "
            f"Latest {formatting.show(latest['value'], 'estimated_max', units=units)} on "
            f"{formatting.day(latest['date'], today)}.")
    if goal:
        desc += (f" Goal {_goal_weight(goal['target_kg'], units)} by {formatting.day(goal['target_date'], today)}: "
                 f"{goal['status']}")
        desc += f", projected {formatting.show(goal['projected'], 'estimated_max', units=units)}." if drawn else "."
    out = _svg_open(element_id, f"Estimated max over time: {chart['lift']}", desc, LIFT_W, LIFT_H)
    _grid(out, ticks, labels, y, left, right)
    axis_x = _dates(first, last, left, right)
    _end_dates(out, (first, axis_x(first)), (last, axis_x(last)), LIFT_H - 8, LIFT_W, today)
    if target is not None:
        out.append(f'<line class="goal" x1="{_c(left)}" x2="{_c(right)}" y1="{_c(y(target))}" y2="{_c(y(target))}" />')
        out.append(f'<text class="goal-label end" x="{_c(right)}" y="{_c(y(target) - 6)}">'
                   f'Goal {_goal_weight(stored_target, units)}</text>')
    if drawn:
        out.append(f'<line class="projection" x1="{_c(x(pts[-1][0]))}" y1="{_c(y(pts[-1][1]))}" '
                   f'x2="{_c(x(goal_date))}" y2="{_c(y(projected))}" stroke-dasharray="1 6" />')
    if n > 1:
        out.append(f'<polyline class="series" pathLength="1" points="{_points((x(d), y(v)) for d, v, _, _ in pts)}" />')
    for i, (d, v, real, stored) in enumerate(pts):
        kind, r = ("dot is-latest", 4) if i == n - 1 else ("dot", 3)
        out.append(f'<circle class="{kind}" cx="{_c(x(d))}" cy="{_c(y(v))}" r="{r}"><title>'
                   f'{formatting.day(real, today)}: {formatting.show(stored, "estimated_max", units=units)}'
                   '</title></circle>')
    return _close(out)


# ---------------------------------------------------------------- Progress: body weight

BW_W, BW_H = 360, 214
BW_TOP, BW_BOTTOM = 26, 172


def bodyweight_svg(chart: dict | None, element_id: str = "bodyweight-chart", today: date | None = None,
                   units=None) -> Markup:
    """A hollow dot per weigh-in, the smoothed trend as a line, labelled grid lines in whole kg (or whole lb),
    the first and last date, and a horizontal goal line at the target. Progress' Body weight card.

    chart: progress.bodyweight_chart: {points [{date, kg, smoothed}], latest {date, kg}, goal {target_kg,
    target_date} | None}, or None. Renders nothing under two weigh-ins. The line joins each point's
    smoothed value; points without one are left out of the line, and a line needs two.
    """
    if not chart or len(chart.get("points") or []) < 2:
        return Markup("")
    def shown(v: float) -> float:  # a stored kg in the unit the chart is drawn in
        return formatting.convert(v, "weigh_in", units)

    raw = [(_date(p["date"]), float(p["kg"]), p.get("smoothed")) for p in chart["points"]]
    today = _date(today) if today else max(d for d, _, _ in raw)
    rows = sorted(((_on(d, today), kg, sm, d) for d, kg, sm in raw), key=lambda r: r[0])
    pts = [(d, shown(kg), real, kg) for d, kg, _, real in rows]
    smooth = [(d, shown(float(sm)), float(sm)) for d, _, sm, _ in rows if sm is not None]
    goal = chart.get("goal") or None
    stored_target = float(goal["target_kg"]) if goal and goal.get("target_kg") is not None else None
    target = shown(stored_target) if stored_target is not None else None
    first, last = pts[0][0], pts[-1][0]
    values = [v for _, v, _, _ in pts] + [v for _, v, _ in smooth] + ([target] if target is not None else [])
    ticks = _ticks(min(values), max(values), WHOLE_STEPS)
    labels = _tick_labels(ticks, "bodyweight_derived", formatting.unit_word("weight", units))
    left, right = _margin(labels), BW_W - ROOM
    y = _scale(ticks[0], ticks[-1], BW_TOP, BW_BOTTOM)
    x = _dates(first, last, left + 6, right - 6)

    latest = chart["latest"]
    desc = (f"{_count(len(pts), 'weigh-in', 'weigh-ins')} from {formatting.day(raw[0][0], today)} to "
            f"{formatting.day(raw[-1][0], today)}. "
            f"Latest {formatting.show(latest['kg'], 'weigh_in', units=units)} on "
            f"{formatting.day(latest['date'], today)}.")
    if len(smooth) > 1:
        desc += (" The line is the smoothed trend, now "
                 f"{formatting.show(smooth[-1][2], 'bodyweight_derived', units=units)}.")
    if target is not None:
        desc += f" Goal {_goal_weight(stored_target, units)}" + (
            f" by {formatting.day(goal['target_date'], today)}." if goal.get("target_date") else ".")
    out = _svg_open(element_id, "Body weight over time", desc, BW_W, BW_H)
    _grid(out, ticks, labels, y, left, right)
    axis_x = _dates(first, last, left, right)
    _end_dates(out, (first, axis_x(first)), (last, axis_x(last)), BW_H - 8, BW_W, today)
    if target is not None:
        out.append(f'<line class="goal" x1="{_c(left)}" x2="{_c(right)}" y1="{_c(y(target))}" y2="{_c(y(target))}" />')
        out.append(f'<text class="goal-label end" x="{_c(right)}" y="{_c(y(target) - 7)}">'
                   f'Goal {_goal_weight(stored_target, units)}</text>')
    for d, v, real, stored in pts:
        out.append(f'<circle class="dot weigh-in" cx="{_c(x(d))}" cy="{_c(y(v))}" r="3"><title>'
                   f'{formatting.day(real, today)}: {formatting.show(stored, "weigh_in", units=units)}'
                   '</title></circle>')
    if len(smooth) > 1:
        out.append(f'<polyline class="series smoothed" pathLength="1" '
                   f'points="{_points((x(d), y(v)) for d, v, _ in smooth)}" />')
    return _close(out)


# ---------------------------------------------------------------- Today: the smoothed weight, 4 weeks

def _smoothed(series, today: date, units=None) -> list[tuple[date, float, date, float]]:
    """(drawn date, smoothed in the shown unit, real date, smoothed as stored), in drawn order: a point after
    today counts as today."""
    rows = [(_on(_date(p["date"]), today), formatting.convert(float(p["smoothed"]), "bodyweight_derived", units),
             _date(p["date"]), float(p["smoothed"]))
            for p in series or [] if p.get("smoothed") is not None]
    return sorted(rows, key=lambda r: r[0])


def _weight_desc(line, today: date, units=None) -> str:
    (_, _, d1, v1), (_, _, d2, v2) = line[0], line[-1]
    return (f"Smoothed body weight over the last 4 weeks, from "
            f"{formatting.show(v1, 'bodyweight_derived', units=units)} on {formatting.day(d1, today)} to "
            f"{formatting.show(v2, 'bodyweight_derived', units=units)} on {formatting.day(d2, today)}.")


def _area(line_xy: list[tuple[float, float]], base: float) -> str:
    """The area under a line, down to base: decorative, the line above it carries the figures."""
    (x0, _), (x1, _) = line_xy[0], line_xy[-1]
    return f"M{_c(x0)},{_c(base)} " + " ".join(f"L{_c(x)},{_c(y)}" for x, y in line_xy) + f" L{_c(x1)},{_c(base)} Z"


SMALL_W, SMALL_WEIGHT_H = 320, 132


def weight_small_svg(series: list | None, today: date, element_id: str = "chart-weight-4w", units=None) -> Markup:
    """The Today chart card "Weight": the smoothed line over the last 28 days, the area under it and
    the end dot. No axis.

    series: get_trends bodyweight.series, [{date, kg, smoothed}], the weigh-ins of the last 28 days,
    oldest first. Empty under two smoothed points: the page says "No weigh-ins in the last 28 days."
    or "One weigh-in in the last 28 days. The chart starts from the second one."
    """
    today = _date(today)
    line = _smoothed(series, today, units)
    if len(line) < 2:
        return Markup("")
    values = [v for _, v, _, _ in line]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.12, 0.1)
    y = _scale(lo - pad, hi + pad, 14, 110)
    x = _dates(line[0][0], line[-1][0], 8, SMALL_W - 8)
    xy = [(x(d), y(v)) for d, v, _, _ in line]
    out = _svg_open(element_id, "Weight, last 4 weeks", _weight_desc(line, today, units), SMALL_W, SMALL_WEIGHT_H,
                    small=True)
    out.append(f'<path class="area" d="{_area(xy, SMALL_WEIGHT_H - 4)}" />')
    out.append(f'<polyline class="series smoothed" pathLength="1" points="{_points(xy)}" />')
    out.append(f'<circle class="dot end-dot" cx="{_c(xy[-1][0])}" cy="{_c(xy[-1][1])}" r="7" />')
    return _close(out)


SHEET_W, SHEET_WEIGHT_H = 360, 176
SHEET_TOP, SHEET_BOTTOM = 16, 146


def weight_sheet_svg(series: list | None, today: date, element_id: str = "chart-weight-sheet",
                     units=None) -> Markup:
    """The Weight sheet's "Smoothed weight": the same line and area, three labelled grid lines, the
    first and last date. series and the empty case as weight_small_svg."""
    today = _date(today)
    line = _smoothed(series, today, units)
    if len(line) < 2:
        return Markup("")
    values = [v for _, v, _, _ in line]
    ticks = _three_ticks(min(values), max(values), KG_STEPS)
    labels = _tick_labels(ticks, "bodyweight_derived", formatting.unit_word("weight", units))
    left, right = _margin(labels), SHEET_W - ROOM
    y = _scale(ticks[0], ticks[-1], SHEET_TOP, SHEET_BOTTOM)
    x = _dates(line[0][0], line[-1][0], left, right - 4)
    xy = [(x(d), y(v)) for d, v, _, _ in line]
    out = _svg_open(element_id, "Weight, last 4 weeks", _weight_desc(line, today, units), SHEET_W, SHEET_WEIGHT_H)
    _grid(out, ticks, labels, y, left, right)
    _end_dates(out, (line[0][0], xy[0][0]), (line[-1][0], xy[-1][0]), SHEET_WEIGHT_H - 6, SHEET_W, today)
    out.append(f'<path class="area" d="{_area(xy, SHEET_BOTTOM)}" />')
    out.append(f'<polyline class="series smoothed" pathLength="1" points="{_points(xy)}" />')
    out.append(f'<circle class="dot end-dot" cx="{_c(xy[-1][0])}" cy="{_c(xy[-1][1])}" r="4" />')
    return _close(out)


# ---------------------------------------------------------------- Today: eaten a day, 7 days
# A day has food when its kcal is not null (E2): 0 kcal is food. A bar is drawn only above 0.

def _week(days: list | None) -> list[tuple[date, int | None]]:
    """The last 7 of get_days' days, as (date, kcal or None), oldest first."""
    return [(_date(d["date"]), d.get("kcal")) for d in (days or [])[-7:]]


def _has_food(week) -> bool:
    return any(kcal is not None for _, kcal in week)


def _eaten_desc(week: list[tuple[date, int | None]], today: date, target: float | None) -> str:
    said = ["Calories eaten each day, last 7 days."]
    for d, kcal in week:
        said.append(f"{formatting.day(d, today)}: " + (formatting.show(kcal, "kcal") if kcal is not None
                                                        else "no food logged") + ".")
    if target is not None:
        said.append(f"Calorie target {formatting.show(target, 'kcal')}.")
    return " ".join(said)


def _bars(out: list[str], week, y, slot: float, bar_w: float, base: float, radius: float, today: date) -> list:
    """One rect per day above 0 kcal, today's bright. Returns each day's (centre x, kcal or None)."""
    centres = []
    out.append('<g class="bars">')
    for n, (d, kcal) in enumerate(week):
        centre = slot * n + slot / 2
        centres.append((centre, kcal))
        if kcal is None or kcal <= 0:
            continue  # no bar: no food, or food at 0 kcal
        kind = "bar is-today" if d == today else "bar"
        top = y(kcal)
        out.append(f'<rect class="{kind}" x="{_c(centre - bar_w / 2)}" y="{_c(top)}" width="{_c(bar_w)}" '
                   f'height="{_c(base - top)}" rx="{_c(radius)}" />')
    out.append("</g>")
    return centres


SMALL_EATEN_H = 150


def eaten_small_svg(days: list | None, today: date, target_kcal: float | None = None,
                    element_id: str = "chart-calories-7d") -> Markup:
    """The Today chart card "Eaten a day": one bar per day for 7 days, today's bright, a one-letter day
    under each, and a dotted goal line at the calorie target when one is set. A day with no food has no
    bar.

    days: trends.get_days days, [{date, kcal | null, ...}] (28, oldest first): the last 7 are drawn.
    target_kcal: get_trends calories.target_kcal. Empty when every one of the 7 days has kcal null: the
    page says "No food logged in the last 7 days."
    """
    today = _date(today)
    week = _week(days)
    if not _has_food(week):
        return Markup("")
    hi = max([kcal or 0 for _, kcal in week] + ([target_kcal] if target_kcal else [])) or 1
    base = 112
    y = _scale(0, hi, 12, base)
    out = _svg_open(element_id, "Calories, last 7 days", _eaten_desc(week, today, target_kcal),
                    SMALL_W, SMALL_EATEN_H, small=True)
    centres = _bars(out, week, y, SMALL_W / 7, 28.3, base, 7, today)
    for (centre, _), (d, _) in zip(centres, week):
        kind = "axis mid is-today" if d == today else "axis mid"
        out.append(f'<text class="{kind}" x="{_c(centre)}" y="{SMALL_EATEN_H - 9}">{formatting.weekday_letter(d)}</text>')
    if target_kcal:
        out.append(f'<line class="goal" x1="4" x2="{SMALL_W - 4}" y1="{_c(y(target_kcal))}" y2="{_c(y(target_kcal))}" />')
    return _close(out)


SHEET_EATEN_H = 196


def eaten_sheet_svg(days: list | None, today: date, element_id: str = "chart-calories-sheet") -> Markup:
    """The Calories sheet's "Each day": seven bars, each with its kcal above it and its weekday under
    it. A day at 0 kcal has its figure and no bar. days and the empty case as eaten_small_svg."""
    today = _date(today)
    week = _week(days)
    if not _has_food(week):
        return Markup("")
    hi = max(kcal or 0 for _, kcal in week) or 1
    base = 168
    y = _scale(0, hi, 32, base)
    out = _svg_open(element_id, "Calories, last 7 days", _eaten_desc(week, today, None), SHEET_W, SHEET_EATEN_H)
    centres = _bars(out, week, y, SHEET_W / 7, 28.8, base, 4, today)
    for (centre, _), (d, _) in zip(centres, week):
        kind = "axis mid is-today" if d == today else "axis mid"
        out.append(f'<text class="{kind}" x="{_c(centre)}" y="{SHEET_EATEN_H - 6}">{formatting.weekday(d)}</text>')
    # Each day's figure over its bar. Figures too wide to sit side by side give way, today's keeping its own.
    figures = [(n, formatting.show(kcal, "kcal", unit=False), centre, kcal)
               for n, (centre, kcal) in enumerate(centres) if kcal is not None]
    boxes = [_box(text, centre, SHEET_W, bold=True) for _, text, centre, _ in figures]
    for i in sorted(_give_way(boxes)):
        _, text, _, kcal = figures[i]
        _label(out, "axis value", text, boxes[i], y(kcal) - 8)
    return _close(out)


# ---------------------------------------------------------------- Goals: a goal's path

GOAL_W, GOAL_H = 360, 204
GOAL_TOP, GOAL_BOTTOM = 36, 168


def goal_quantity(goal: dict) -> str:
    """The quantity a goal's figures are shown at (section C): the service's own, goals.goal_quantity of the goal's
    kind and unit. One the service does not know raises, as get_goals does."""
    return goals.goal_quantity(goal.get("kind"), goal.get("unit"))


def _weighed(goal: dict) -> bool:
    """A goal of a weight: a lift or body weight goal, shown in the person's weight unit (its stored unit is kg)."""
    return goal.get("kind") in goals.ROUNDED


def _goal_unit(goal: dict, units=None) -> str | None:
    """The unit a goal's figure carries after it, or None for a time (m:ss carries no unit)."""
    if goal_quantity(goal) == "goal_time":
        return None
    return formatting.unit_word("weight", units) if _weighed(goal) else goal.get("unit")


def _goal_figure(value, goal: dict, units=None) -> str:
    return formatting.show(value, goal_quantity(goal), units=units)


def _goal_target(goal: dict, units=None) -> str:
    """The target as entered, by the goal unit: "77 kg", "84 cm", a time in m:ss. A weight in the person's unit."""
    if goal_quantity(goal) == "goal_time":
        return formatting.show(goal["target_value"], "goal_time")
    if _weighed(goal):
        return _goal_weight(goal["target_value"], units)
    return f"{formatting.show(goal['target_value'], 'goal_target')} {goal.get('unit')}"


def _goal_rate(value, goal: dict, units=None) -> str:
    """A pace a week: "-0.25 kg a week", or a time's "-19 seconds a week". A weight in the person's unit."""
    if goal_quantity(goal) == "goal_time":
        return formatting.show(value, "time_pace")
    if _weighed(goal):
        return f"{formatting.show(value, 'weight_rate', units=units)} {formatting.unit_word('weight', units)} a week"
    return f"{formatting.show(value, 'rate')} {goal.get('unit')} a week"


def _goal_desc(goal: dict, today: date, units=None) -> str:
    """The start, then the goal's own plan_line as the service wrote it (it carries the close-call
    rules of section D: on the line, widened figures, an ended line), then the target, the paces and the
    projected date. With no plan_line there is nothing logged, and no now figure is given."""
    said = [f"Started {_goal_figure(goal['start_value'], goal, units)} on "
            f"{formatting.day(goal['start_date'], today)}."
            if goal.get("start_value") is not None else f"Started {formatting.day(goal['start_date'], today)}."]
    said.append(goal["plan_line"] if goal.get("plan_line") else "Nothing logged yet.")
    said.append(f"Target {_goal_target(goal, units)} by {formatting.day(goal['target_date'], today)}.")
    if goal.get("needed_per_week") is not None:
        said.append(f"Needed {_goal_rate(goal['needed_per_week'], goal, units)}.")
    if goal.get("actual_per_week") is not None:
        said.append(f"So far {_goal_rate(goal['actual_per_week'], goal, units)}.")
    if goal.get("projected_date"):
        said.append(f"At this pace it is reached on {formatting.day(goal['projected_date'], today)}.")
    return " ".join(said)


def _projection_end(goal: dict, today: date, target_date: date) -> date | None:
    """The future end date before the cap and grid cuts, if there is a line to draw."""
    if goal.get("fit") is None or target_date < today:
        return None
    if goal.get("projected_date"):
        end = _date(goal["projected_date"])
    elif (goal.get("projection") or {}).get("projected") is not None:
        end = target_date
    else:
        return None
    return end if end > today else None


def _projection_clip(start: float, end: float, lo: float, hi: float) -> tuple[float, float] | None:
    """The fractions of the segment at which it enters and leaves the grid."""
    change = end - start
    if change == 0:
        return (0.0, 1.0) if lo <= start <= hi else None
    enter, leave = sorted(((lo - start) / change, (hi - start) / change))
    enter, leave = max(0.0, enter), min(1.0, leave)
    return (enter, leave) if enter < leave else None


def goal_svg(goal: dict | None, today: date, element_id: str | None = None, units=None) -> Markup:
    """A goal's path, for its card on Goals: the dashed plan line from start to
    target, the actual line with a dot per point, the now figure ringed at today, the dotted projection
    along the fit from today, the today line with "Today", a target tick with its
    value, labelled grid lines, the start and target dates. One point: the plan line, one ringed dot,
    the today line. No projection without one.

    goal: one of goals.goal_charts' goals, unrounded: {goal_id, label, kind, unit, start_date,
    start_value, target_date, target_value, latest, needed_per_week, actual_per_week, projected_date,
    projection {projected} | None, fit {today, per_day} | None, plan_line,
    path {plan [{date, value}], actual [{date, value}]}}.
    "Now" is the goal's latest, at today: path.actual has a point per date that has one, so it can end
    before today. The desc says the goal's plan_line as written; its own figures go through show,
    never widened. A projected date far past the target date is held at half the plan's length beyond
    it, and the grid edges cut the line along its slope. The desc keeps the real figures. units: the person's; a
    weight goal is drawn in its weight unit, a measurement or a time as stored.
    """
    if not goal or not (goal.get("path") or {}).get("plan"):
        return Markup("")
    quantity, today = goal_quantity(goal), _date(today)

    def shown(v: float) -> float:  # a stored value in the unit the chart is drawn in
        return formatting.convert(v, quantity, units)

    plan = [(_date(p["date"]), shown(float(p["value"]))) for p in goal["path"]["plan"]]  # a line, not logged points
    actual = sorted(((_on(_date(p["date"]), today), float(p["value"]), _date(p["date"]))
                     for p in goal["path"].get("actual") or []), key=lambda p: p[0])
    now = float(goal["latest"]) if goal.get("latest") is not None else None
    start, target_date = _on(_date(goal["start_date"]), today), _date(goal["target_date"])
    known = [start, today, target_date] + [d for d, _ in plan] + [d for d, _, _ in actual]
    first = min(known)
    end = _projection_end(goal, today, target_date)
    if end is not None:
        cap = max(target_date + timedelta(days=(target_date - first).days // 2), today + timedelta(days=1))
        end = min(end, cap)
    target = shown(float(goal["target_value"]))
    values = [v for _, v in plan] + [shown(v) for _, v, _ in actual] + [target]
    values += [shown(now)] if now is not None else []
    ticks = _ticks(min(values), max(values), STEPS[quantity])
    clip = None
    if end is not None:  # the fit from today to the end day, cut where it leaves the grid
        start_value = shown(goal["fit"]["today"])
        end_value = shown(goal["fit"]["today"] + goal["fit"]["per_day"] * (end - today).days)
        clip = _projection_clip(start_value, end_value, ticks[0], ticks[-1])
    if clip is not None:  # only a line that is drawn reaches the dates: a hidden one never stretches them
        known.append(end)
    last = max(known)
    labels = _tick_labels(ticks, quantity, _goal_unit(goal, units))
    target_text = _goal_target(goal, units)
    left = _margin(labels)
    right = GOAL_W - (10 + GAP + text_width(target_text) + ROOM)  # room for the target tick and its label
    y = _scale(ticks[0], ticks[-1], GOAL_TOP, GOAL_BOTTOM)
    x = _dates(first, last, left, right)
    target_y = y(target)
    title = f"{goal.get('label') or 'Goal'} by {formatting.day(target_date, today)}"
    out = _svg_open(element_id or f"chart-goal-{goal.get('goal_id')}", title, _goal_desc(goal, today, units),
                    GOAL_W, GOAL_H)
    _grid(out, ticks, labels, y, left, right)
    _end_dates(out, (_date(goal["start_date"]), x(start)), (target_date, x(target_date)), GOAL_H - 8, GOAL_W, today)
    if len(plan) > 1:
        out.append(f'<line class="plan" x1="{_c(x(plan[0][0]))}" y1="{_c(y(plan[0][1]))}" '
                   f'x2="{_c(x(plan[-1][0]))}" y2="{_c(y(plan[-1][1]))}" />')
    out.append(f'<line class="today-line" x1="{_c(x(today))}" x2="{_c(x(today))}" y1="24" y2="{GOAL_BOTTOM + 10}" />')
    _label(out, "axis today-label", "Today", _box("Today", x(today), GOAL_W, bold=True), 16)
    tick_x = x(target_date)
    out.append(f'<line class="target-tick" x1="{_c(max(tick_x - 10, left))}" x2="{_c(max(tick_x, left + 10))}" '
               f'y1="{_c(target_y)}" y2="{_c(target_y)}" />')
    out.append(f'<text class="goal-label" x="{_c(max(tick_x, left + 10) + GAP)}" y="{_c(target_y + AXIS / 3)}">'
               f'{escape(target_text)}</text>')
    if clip is not None:
        start_x, width = x(today), x(end) - x(today)
        change = end_value - start_value
        (x1, y1), (x2, y2) = [(_c(start_x + width * share), _c(y(start_value + change * share)))
                               for share in clip]
        out.append(f'<line class="projection" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" />')
        out.append(f'<circle class="dot proj-end" cx="{x2}" cy="{y2}" r="3.5" />')
    # The actual line: a point per date. A point at today takes the now figure, so the line meets the ring.
    drawn = [(d, now if (d == today and now is not None) else v, real) for d, v, real in actual]
    if len(drawn) > 1:
        out.append(f'<polyline class="series" pathLength="1" '
                   f'points="{_points((x(d), y(shown(v))) for d, v, _ in drawn)}" />')
    for d, v, real in drawn:
        if d == today and now is not None:
            continue  # the ring below stands for it
        out.append(f'<circle class="dot" cx="{_c(x(d))}" cy="{_c(y(shown(v)))}" r="2.2"><title>'
                   f'{formatting.day(real, today)}: {escape(formatting.show(v, quantity, units=units))}'
                   '</title></circle>')
    if now is not None:
        out.append(f'<circle class="dot is-latest" cx="{_c(x(today))}" cy="{_c(y(shown(now)))}" r="4"><title>'
                   f'{formatting.day(today, today)}: {escape(formatting.show(now, quantity, units=units))}</title>'
                   '</circle>')
    return _close(out)


# ---------------------------------------------------------------- an activity: pace

PACE_W, PACE_H = 360, 196
PACE_TOP, PACE_BOTTOM = 46, 154


def _give_way(boxes: list[tuple[float, float]]) -> set[int]:
    """Which labels are written: the latest always, then each earlier one whose box ends at least GAP
    before the last box kept."""
    kept, at = set(), None
    for n in range(len(boxes) - 1, -1, -1):
        if at is None or boxes[n][1] + GAP <= at:
            kept.add(n)
            at = boxes[n][0]
    return kept


def pace_svg(sessions: list | None, activity: str, today: date, element_id: str = "chart-pace",
             units=None) -> Markup:
    """An activity's Pace card: a dot per session with its pace above it, joined by a line, the latest
    ringed, grid lines in m:ss, a date under each dot. Lower on the chart is faster. Sessions sit evenly
    spaced, oldest at the left. A label is placed by its width and held inside the canvas; where labels
    would collide, the latest keeps its own and earlier ones give way. The desc states every session's
    pace.

    sessions: cardio.get_cardio(activity) sessions, [{date, pace_min_per_km | null, ...}], newest first.
    Empty under 2 sessions with a distance: the card says why. units: the person's; in miles, a pace per mi.
    """
    today = _date(today)
    paced = [(_date(s["date"]), float(s["pace_min_per_km"])) for s in reversed(sessions or [])
             if s.get("pace_min_per_km") is not None]
    if len(paced) < 2:
        return Markup("")
    per = formatting.unit_word("distance", units)
    drawn = [formatting.convert(v, "pace", units) for _, v in paced]  # each pace in the shown unit
    values = drawn
    ticks = _ticks(min(values), max(values), SECOND_STEPS, most=4)
    labels = _tick_labels(ticks, "pace", None)
    left, right = _margin(labels), PACE_W - ROOM
    y = _scale(ticks[0], ticks[-1], PACE_TOP, PACE_BOTTOM)
    slot = (right - left) / len(paced)
    xs = [left + slot * (n + 0.5) for n in range(len(paced))]
    said = [f"Pace per {per} for each {activity} session, oldest first."]
    said += [f"{formatting.day(d, today)}: {formatting.show(v, 'pace', units=units)}." for d, v in paced]
    said.append("Lower is faster.")
    out = _svg_open(element_id, f"Pace per {per}: {activity}", " ".join(said), PACE_W, PACE_H)
    _grid(out, ticks, labels, y, left, right)
    days = [formatting.day(d, today) for d, _ in paced]
    paces = [formatting.show(v, "pace", units=units) for _, v in paced]
    day_boxes = [_box(t, x, PACE_W) for t, x in zip(days, xs)]
    pace_boxes = [_box(t, x, PACE_W, bold=True, start=left) for t, x in zip(paces, xs)]
    for n in sorted(_give_way(day_boxes)):
        _label(out, "axis", days[n], day_boxes[n], PACE_H - 8)
    out.append(f'<polyline class="series" pathLength="1" '
               f'points="{_points((xs[n], y(v)) for n, v in enumerate(drawn))}" />')
    shown = _give_way(pace_boxes)
    for n, v in enumerate(drawn):
        kind, r = ("dot is-latest", 4.5) if n == len(paced) - 1 else ("dot", 3.5)
        out.append(f'<circle class="{kind}" cx="{_c(xs[n])}" cy="{_c(y(v))}" r="{r}"><title>'
                   f'{days[n]}: {paces[n]} per {per}</title></circle>')
        if n in shown:
            _label(out, "axis value", paces[n], pace_boxes[n], y(v) - 10)
    return _close(out)
