"""Inline SVG charts for the web pages. Pure functions: chart data in, markup out.

  chart_svg(chart) -> Markup        estimated max per session for one lift, the goal line, the projection
  bodyweight_svg(chart) -> Markup   weigh-ins, the smoothed trend, the goal line

The data comes from app.services.progress (lift_chart, bodyweight_chart). Colours come from the
stylesheet, through the class names. Every chart carries a <title> and a <desc> that state its
numbers, so it reads without the picture. Text from the data is escaped.

Every number and date goes through app.formatting, with a plain space before a unit (text inside an
SVG). today is the person's own date, for the date form: without it, the chart's last day stands in.
"""

from datetime import date

from markupsafe import Markup, escape

from app import formatting


def _goal_kg(value) -> str:
    """A goal's target in kg, as entered: "100 kg"."""
    return f"{formatting.show(value, 'goal_target')} kg"

CHART_W, CHART_H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 16, 30


def _svg_open(element_id: str, title: str, desc: str) -> list[str]:
    """The opening tag, title and description. Every piece of text, the element id included, is escaped."""
    eid = escape(element_id)
    tid, did = f"{eid}-title", f"{eid}-desc"
    return [f'<svg class="chart" id="{eid}" viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
            f'aria-labelledby="{tid} {did}" xmlns="http://www.w3.org/2000/svg">',
            f'<title id="{tid}">{escape(title)}</title>', f'<desc id="{did}">{escape(desc)}</desc>']


def chart_svg(chart: dict | None, element_id: str = "progress-chart", today: date | None = None) -> Markup:
    """Estimated max per session for one lift, a horizontal goal line at the target, and the
    projection as a dashed line from the latest point to the projected value at the goal date.

    chart: {lift, points [{date, value}], latest {date, value}, goal {target_kg, target_date, status,
    projected} | None}, or None (renders nothing). A goal date already passed draws no projection,
    and a projection far outside the data is clamped into the plot instead of stretching it.
    """
    if not chart or not chart.get("points"):
        return Markup("")
    pts = [(date.fromisoformat(p["date"]), float(p["value"])) for p in chart["points"]]
    goal = chart.get("goal") or None
    target = float(goal["target_kg"]) if goal and goal.get("target_kg") is not None else None
    goal_date = date.fromisoformat(goal["target_date"]) if goal and goal.get("target_date") else None
    projected = float(goal["projected"]) if goal and goal.get("projected") is not None else None

    if goal_date is not None and goal_date <= pts[-1][0]:
        projected = None  # the goal date has passed: nothing left to project
    first = pts[0][0]
    last = max([pts[-1][0]] + ([goal_date] if goal_date else []))
    # The y-range comes from the sessions and the target; a wild projection is clamped into it, not followed.
    values = [v for _, v in pts] + ([target] if target is not None else [])
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, 2.5)
    lo, hi = max(0.0, lo - pad), hi + pad
    if projected is not None:
        projected = min(max(projected, lo), hi)
    span = max((last - first).days, 1)
    inner_w, inner_h = CHART_W - PAD_L - PAD_R, CHART_H - PAD_T - PAD_B

    def x(d: date) -> float:
        return round(PAD_L + (d - first).days / span * inner_w, 1)

    def y(v: float) -> float:
        return round(PAD_T + (hi - v) / (hi - lo) * inner_h, 1)

    latest = chart["latest"]
    today = today or last
    desc = (f"{len(pts)} sessions from {formatting.day(first, today)} to {formatting.day(pts[-1][0], today)}. "
            f"Latest {formatting.show(latest['value'], 'estimated_max')} on {formatting.day(latest['date'], today)}.")
    if goal:
        desc += f" Goal {_goal_kg(goal['target_kg'])} by {formatting.day(goal['target_date'], today)}: {goal['status']}"
        desc += (f", projected {formatting.show(goal['projected'], 'estimated_max')}."
                 if goal.get("projected") is not None else ".")
    out = _svg_open(element_id, f"Estimated max over time: {chart['lift']}", desc)
    for n in range(5):  # horizontal grid in four bands, labelled in kg
        v = lo + (hi - lo) * n / 4
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{CHART_W - PAD_R}" y1="{y(v)}" y2="{y(v)}" />')
        out.append(f'<text class="axis" x="{PAD_L - 6}" y="{y(v) + 4}" text-anchor="end">'
                   f'{formatting.show(v, "estimated_max", unit=False)}</text>')
    out.append(f'<text class="axis" x="{PAD_L}" y="{CHART_H - 8}">{formatting.day(first, today)}</text>')
    out.append(f'<text class="axis" x="{CHART_W - PAD_R}" y="{CHART_H - 8}" text-anchor="end">'
               f'{formatting.day(last, today)}</text>')
    if target is not None:
        out.append(f'<line class="goal" x1="{PAD_L}" x2="{CHART_W - PAD_R}" y1="{y(target)}" y2="{y(target)}" />')
        out.append(f'<text class="goal-label" x="{CHART_W - PAD_R}" y="{y(target) - 5}" text-anchor="end">'
                   f'Goal {_goal_kg(target)}</text>')
    if projected is not None and goal_date is not None:
        out.append(f'<line class="projection" x1="{x(pts[-1][0])}" y1="{y(pts[-1][1])}" x2="{x(goal_date)}" '
                   f'y2="{y(projected)}" stroke-dasharray="6 4" />')
    if len(pts) > 1:
        out.append(f'<polyline class="series" fill="none" points="{" ".join(f"{x(d)},{y(v)}" for d, v in pts)}" />')
    for d, v in pts:
        out.append(f'<circle class="dot" cx="{x(d)}" cy="{y(v)}" r="3"><title>{formatting.day(d, today)}: '
                   f'{formatting.show(v, "estimated_max")}</title></circle>')
    out.append("</svg>")
    return Markup("\n".join(out))


def bodyweight_svg(chart: dict | None, element_id: str = "bodyweight-chart", today: date | None = None) -> Markup:
    """Weigh-ins as dots, the smoothed trend as a line, and a horizontal goal line at the target.

    chart: {points [{date, kg, smoothed}], latest {date, kg}, goal {target_kg, target_date} | None}, or
    None. Renders nothing under two weigh-ins. The line joins each point's `smoothed` value (the
    exponentially weighted average up to that weigh-in); points without one are left out of the line,
    and a line needs two. Same look as chart_svg.
    """
    if not chart or len(chart.get("points") or []) < 2:
        return Markup("")
    pts = [(date.fromisoformat(p["date"]), float(p["kg"])) for p in chart["points"]]
    smooth = [(date.fromisoformat(p["date"]), float(p["smoothed"])) for p in chart["points"]
              if p.get("smoothed") is not None]
    goal = chart.get("goal") or None
    target = float(goal["target_kg"]) if goal and goal.get("target_kg") is not None else None
    first, last = pts[0][0], pts[-1][0]
    values = [v for _, v in pts + smooth] + ([target] if target is not None else [])
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, 1.0)
    lo, hi = max(0.0, lo - pad), hi + pad
    span = max((last - first).days, 1)
    inner_w, inner_h = CHART_W - PAD_L - PAD_R, CHART_H - PAD_T - PAD_B

    def x(d: date) -> float:
        return round(PAD_L + (d - first).days / span * inner_w, 1)

    def y(v: float) -> float:
        return round(PAD_T + (hi - v) / (hi - lo) * inner_h, 1)

    latest = chart["latest"]
    today = today or last
    desc = (f"{len(pts)} weigh-ins from {formatting.day(first, today)} to {formatting.day(last, today)}. "
            f"Latest {formatting.show(latest['kg'], 'weigh_in')} on {formatting.day(latest['date'], today)}.")
    if len(smooth) > 1:
        desc += f" The line is the smoothed trend, now {formatting.show(smooth[-1][1], 'bodyweight_derived')}."
    if target is not None:
        desc += f" Goal {_goal_kg(target)}" + (f" by {formatting.day(goal['target_date'], today)}."
                                               if goal.get("target_date") else ".")
    out = _svg_open(element_id, "Body weight over time", desc)
    for n in range(5):
        v = lo + (hi - lo) * n / 4
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{CHART_W - PAD_R}" y1="{y(v)}" y2="{y(v)}" />')
        out.append(f'<text class="axis" x="{PAD_L - 6}" y="{y(v) + 4}" text-anchor="end">'
                   f'{formatting.show(v, "bodyweight_derived", unit=False)}</text>')
    out.append(f'<text class="axis" x="{PAD_L}" y="{CHART_H - 8}">{formatting.day(first, today)}</text>')
    out.append(f'<text class="axis" x="{CHART_W - PAD_R}" y="{CHART_H - 8}" text-anchor="end">'
               f'{formatting.day(last, today)}</text>')
    if target is not None:
        out.append(f'<line class="goal" x1="{PAD_L}" x2="{CHART_W - PAD_R}" y1="{y(target)}" y2="{y(target)}" />')
        out.append(f'<text class="goal-label" x="{CHART_W - PAD_R}" y="{y(target) - 5}" text-anchor="end">'
                   f'Goal {_goal_kg(target)}</text>')
    if len(smooth) > 1:
        out.append(f'<polyline class="series smoothed" fill="none" '
                   f'points="{" ".join(f"{x(d)},{y(v)}" for d, v in smooth)}" />')
    for d, v in pts:
        out.append(f'<circle class="dot weigh-in" cx="{x(d)}" cy="{y(v)}" r="4"><title>{formatting.day(d, today)}: '
                   f'{formatting.show(v, "weigh_in")}</title></circle>')
    out.append("</svg>")
    return Markup("\n".join(out))
