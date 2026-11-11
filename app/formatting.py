"""Every number and date a person reads. Pure functions on plain values: no database, no clock.

The one place a number is rounded for display. Rounding is Decimal(repr(value)), halves away from
zero (ROUND_HALF_UP), never Python's round. A quantity kept in minutes (m:ss, time_gap,
time_pace) is turned into seconds first, then rounded to whole seconds, or tenths widened.

Numbers
  STEP: {quantity: shown step, in the quantity's stored unit}, read-only. The widened step is a tenth of it.
  rounded(value, quantity, widened=False, step=None) gives a float, for a result field
      A quantity kept in minutes needs step, or it raises ValueError
  is_zero(value, quantity) gives a bool: whether the value rounds to 0 at the shown step
  widen([now, plan], quantity) gives a bool: close-call rule 2, the two print the same, the gap is real
  show(value, quantity, page=False, widened=False, *, unit=True) gives a str: the number and its unit
      unit=False gives the number alone, for a cell under a header that names the unit
  clock(minutes, widened=False) gives a str: the one m:ss function, "75:00" past 59 minutes
  field(value, quantity) gives a str: a form input's value, at the stored decimals

Dates. Each takes a date, a datetime or an ISO string, and "today" the same way.
  day(d, today)                 "Tue 10 Nov", "Tue 10 Nov 2025" in another year
  day_heading(d, today)         "Today, Tuesday 10 November", "Sunday 8 November 2025"
  days(start, end, today)       "3 to 9 Nov", "28 Oct to 3 Nov", "29 Dec 2025 to 4 Jan 2026"
  day_utc(dt, zone, today)      a UTC time as the person's date, in the day() form
  weekday(d), weekday_letter(d) "Tue", "T"

A value that rounds to zero prints "0": never "-0", never "+0". No output ever has an exponent.
show(None, quantity) raises TypeError: the caller shows words instead. An unknown quantity raises
ValueError.
"""

from datetime import date, datetime, timezone, tzinfo
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, Context, Decimal, localcontext
from types import MappingProxyType
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- the quantity table

# quantity: (shown step, unit text, decimals a form input keeps). For a quantity in minutes the
# step is in seconds. The unit is None when the number stands alone. One row per quantity kind.
_TABLE = {
    "weigh_in": ("0.01", "kg", 2),
    "weight_kg": ("0.01", "kg", 2),
    "bodyweight_derived": ("0.1", "kg", 2),
    "estimated_max": ("0.1", "kg", 2),
    "reps": ("1", None, 0),
    "kcal": ("1", "kcal", 0),
    "kcal_per_serving": ("1", "kcal", 2),
    "grams": ("0.01", "g", 2),
    "net_kcal": ("1", "kcal", 0),
    "kcal_left": ("1", "kcal", 0),
    "maintenance": ("10", "kcal", 0),
    "percent": ("1", "%", 0),
    "met": ("0.1", None, 2),
    "session_time": ("1", None, 2),
    "week_minutes": ("1", "min", 1),
    "km": ("0.01", "km", 2),
    "pace": ("1", None, 2),
    "speed": ("0.1", "km/h", 2),
    "incline": ("0.1", "%", 2),
    "bpm": ("1", "bpm", 2),
    "cm": ("0.1", "cm", 2),
    "hours": ("0.01", "h", 2),
    "steps": ("1", "steps", 0),
    "ml": ("1", "ml", 0),
    "servings": ("0.01", None, 2),
    "count": ("1", None, 0),
    "count_avg": ("0.1", None, 2),
    "rate": ("0.01", None, 2),
    "weight_change": ("0.01", "kg", 2),
    "time_pace": ("1", None, 2),
    "goal_time": ("1", None, 2),
    "goal_target": ("0.01", None, 2),
    "time_gap": ("1", None, 2),
}
_MINUTES = {"session_time", "pace", "goal_time", "time_pace", "time_gap"}  # kept in minutes
_CLOCK = {"session_time", "pace", "goal_time"}                             # shown as m:ss
_SIGNED = {"rate", "weight_change", "time_pace"}                           # a change: + or -
_NO_SPACE = {"percent", "incline"}                                         # "41%", "2.5%"

# Read-only: show never reads it, so a caller must not be able to change it.
STEP = MappingProxyType({q: (1 / 60 if q in _MINUTES else float(Decimal(step)))
                         for q, (step, _, _) in _TABLE.items()})

_SECOND = Decimal(60)
_NBSP = "\u00a0"
_PRECISION = 1000  # enough digits for any float at any step, so nothing is lost or put in an exponent
# The one Decimal context: every Decimal operation here runs in a copy of it, so a caller's own
# context (its precision, rounding or traps) never changes a figure.
_CTX = Context(prec=_PRECISION, rounding=ROUND_HALF_UP, Emax=MAX_EMAX, Emin=MIN_EMIN, traps=[], flags=[])


def _decimal(value) -> Decimal:
    """The value as Decimal(repr(value)). None and anything not a number raise TypeError."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"A number is needed, not {type(value).__name__}.")
    number = value if isinstance(value, Decimal) else Decimal(repr(value))
    if not number.is_finite():
        raise ValueError("A finite number is needed.")
    return number


def _row(quantity: str) -> tuple:
    try:
        return _TABLE[quantity]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown quantity: {quantity}.") from None


def _units(number: Decimal, step: Decimal) -> int:
    """How many steps the number is, halves away from zero. A whole number, so no -0."""
    with localcontext(_CTX):
        return int((number / step).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _shown(value, quantity: str, widened: bool) -> tuple[int, Decimal]:
    """(steps, step): the value rounded for display, in seconds for a quantity in minutes."""
    with localcontext(_CTX):
        step = Decimal(_row(quantity)[0])
        number = _decimal(value)
        if quantity in _MINUTES:
            number *= _SECOND
        if widened:
            step /= 10
        return _units(number, step), step


def _digits(units: int, step: Decimal, keep_zeros: bool, commas: bool = True) -> str:
    """The size of units x step as plain digits: no sign, no exponent. Trailing zeros after the
    point go unless keep_zeros."""
    places = max(0, -step.as_tuple().exponent)
    with localcontext(_CTX):
        scaled = abs(units) * int(step.scaleb(places))  # the value in units of 10 ** -places
    whole, part = divmod(scaled, 10 ** places)
    text = f"{whole:,}" if commas else str(whole)
    if places:
        decimals = str(part).rjust(places, "0")
        decimals = decimals if keep_zeros else decimals.rstrip("0")
        if decimals:
            text += "." + decimals
    return text


def _sign(units: int, signed: bool) -> str:
    if units < 0:
        return "-"
    return "+" if signed and units > 0 else ""


def _time(tenths: int, hours: bool, widened: bool) -> str:
    """Tenths of a second as m:ss (or h:mm:ss when hours and an hour or more), signed."""
    sign = "-" if tenths < 0 else ""
    seconds, tenth = divmod(abs(tenths), 10)
    minutes, second = divmod(seconds, 60)
    if hours and minutes >= 60:
        hour, minute = divmod(minutes, 60)
        text = f"{hour}:{minute:02d}:{second:02d}"
    else:
        text = f"{minutes}:{second:02d}"
    return sign + text + (f".{tenth}" if widened else "")


# ---------------------------------------------------------------- numbers

def rounded(value, quantity: str, widened: bool = False, step: float | None = None) -> float:
    """A result field rounded at the quantity's shown step (or the widened step), as a float.

    step overrides the quantity's step and quantizes the value to it in its stored unit, with no
    conversion to seconds: 0.01 for a cardio time goal's should_be_today and ahead_by.
    A quantity kept in minutes needs step: without it, ValueError, since whole seconds as
    minutes (29.516666...) are no field's precision."""
    _row(quantity)
    with localcontext(_CTX):
        if step is None:
            if quantity in _MINUTES:
                raise ValueError(f"{quantity} is kept in minutes: pass step, such as step=0.01, to round it.")
            units, size = _shown(value, quantity, widened)
        else:
            size = _decimal(step)
            if widened:
                size /= 10
            units = _units(_decimal(value), size)
        return float(units * size) + 0.0


def is_zero(value, quantity: str) -> bool:
    """Whether the value rounds to 0 at the quantity's shown step. The one rule for it."""
    return _shown(value, quantity, False)[0] == 0


def widen(pair, quantity: str) -> bool:
    """Close-call rule 2: now and plan print the same at the shown step, and the gap between
    them does not round to 0. The gap is now - plan, as floats."""
    now, plan = pair
    return show(now, quantity) == show(plan, quantity) and not is_zero(now - plan, quantity)


def clock(minutes, widened: bool = False) -> str:
    """Minutes as m:ss: 27.5 is "27:30", 75 is "75:00". Widened: tenths of a second, "29:30.0"."""
    units, _ = _shown(minutes, "goal_time", widened)
    return _time(units if widened else units * 10, False, widened)


def show(value, quantity: str, page: bool = False, widened: bool = False, *, unit: bool = True) -> str:
    """The number and its unit, rounded by the quantity's rule. page=True puts a no-break space
    before the unit. widened=True rounds to the widened step and keeps every widened decimal.
    unit=False gives the number alone: the same rounding, comma, sign and decimals, no unit."""
    written = _row(quantity)[1]
    units, step = _shown(value, quantity, widened)
    if quantity in _CLOCK:
        return _time(units if widened else units * 10, quantity == "session_time", widened)
    digits = _digits(units, step, widened)
    if quantity == "time_gap":
        number, written = digits, ("second" if digits == "1" else "seconds")
    elif quantity == "time_pace":
        number, written = _sign(units, True) + digits, ("second a week" if digits == "1" else "seconds a week")
    elif quantity == "kcal_left":
        number, written = digits, ("kcal over" if units < 0 else "kcal left")
    else:
        number = _sign(units, quantity in _SIGNED) + digits
    if not unit or written is None:
        return number
    return number + ("" if quantity in _NO_SPACE else (_NBSP if page else " ")) + written


def field(value, quantity: str) -> str:
    """A form input's value: the number at the decimals the store keeps, trailing zeros dropped.
    No comma, no unit, no plus. A quantity in minutes stays decimal minutes. Parses again with
    rules.to_float."""
    places = _row(quantity)[2]
    with localcontext(_CTX):
        step = Decimal(1).scaleb(-places)
    units = _units(_decimal(value), step)
    return _sign(units, False) + _digits(units, step, False, commas=False)


# ---------------------------------------------------------------- dates

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def _date(value) -> date:
    """A date from a date, a datetime (its own date part) or an ISO date or datetime string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            return datetime.fromisoformat(text).date()
    raise TypeError(f"A date is needed, not {type(value).__name__}.")


def _moment(value) -> datetime:
    """A datetime from a datetime, an ISO string or a date (midnight). No zone means UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value.strip())
    else:
        raise TypeError(f"A time is needed, not {type(value).__name__}.")
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _zone(zone) -> tzinfo:
    """A zone from a tzinfo or an IANA name. Unset or not a real zone is UTC, as users.zone_for."""
    if isinstance(zone, tzinfo):
        return zone
    if zone:
        try:
            return ZoneInfo(str(zone))
        except (KeyError, ValueError, OSError):
            pass
    return timezone.utc


def _short(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1][:3]}"


def day(d, today) -> str:
    """'Tue 10 Nov', and 'Tue 10 Nov 2025' in a year other than today's. Today too."""
    d, today = _date(d), _date(today)
    text = f"{weekday(d)} {_short(d)}"
    return text if d.year == today.year else f"{text} {d.year}"


def day_heading(d, today) -> str:
    """'Today, Tuesday 10 November', 'Yesterday, Monday 9 November', else 'Sunday 8 November'.
    The year goes on when it is not today's year."""
    d, today = _date(d), _date(today)
    text = f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"
    if d.year != today.year:
        text += f" {d.year}"
    if d == today:
        return f"Today, {text}"
    if (today - d).days == 1:
        return f"Yesterday, {text}"
    return text


def days(start, end, today) -> str:
    """A range with "to" and no weekdays: '3 to 9 Nov', '28 Oct to 3 Nov'. Both ends in one other
    year: the year once, '3 to 9 Nov 2025'. Ends in two years each carry their year. One day
    alone: '9 Nov'."""
    start, end, today = _date(start), _date(end), _date(today)
    if start.year != end.year:
        return f"{_short(start)} {start.year} to {_short(end)} {end.year}"
    year = "" if start.year == today.year else f" {start.year}"
    if start == end:
        return _short(start) + year
    first = str(start.day) if start.month == end.month else _short(start)
    return f"{first} to {_short(end)}{year}"


def day_utc(dt, zone, today) -> str:
    """A UTC time as the person's own date, in the day() form. zone: a tzinfo or an IANA name."""
    return day(_moment(dt).astimezone(_zone(zone)).date(), today)


def weekday(d) -> str:
    """'Tue'."""
    return _DAYS[_date(d).weekday()][:3]


def weekday_letter(d) -> str:
    """'T'."""
    return _DAYS[_date(d).weekday()][0]
