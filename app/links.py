"""Every web link Spotter hands out, built in one place.

The origin is the site address from the settings (the origin of MCP_RESOURCE_URL). It never comes
from a request, so a forged Host header cannot change a link. Every page is behind sign-in, and no
link carries an id that grants access, a token or a secret: a link only says where to go.

    page("progress_lift", lift="Back Squat")  ->  https://<site>/progress/back-squat
"""

import re
from datetime import date

from app.config import get_settings

_PATHS = {
    "home": "/",
    "sign_in": "/sign-in",
    "settings": "/settings",
    "today": "/today",
    "day": "/day/{date}",
    "progress": "/progress",
    "progress_lift": "/progress/{lift}",
    "goals": "/goals",
    "foods": "/foods",
}
NAMES = tuple(_PATHS)


def slug(name: str) -> str:
    """A lift name as it appears in a link: "Back Squat" gives back-squat, "Farmer's Carry" farmers-carry."""
    text = name.casefold().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _day(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()  # raises ValueError on anything but a date


def page(name: str, **params) -> str:
    """The full address of a named page. An unknown name, or a missing or extra parameter, raises."""
    if name not in _PATHS:
        raise ValueError(f"No page called {name!r}. Pages: {', '.join(NAMES)}.")
    path = _PATHS[name]
    wanted = set(re.findall(r"{(\w+)}", path))
    if set(params) != wanted:
        raise TypeError(f"Page {name!r} takes {sorted(wanted) or 'no parameters'}, not {sorted(params)}.")
    if "date" in params:
        path = path.format(date=_day(params["date"]))
    if "lift" in params:
        lift = slug(str(params["lift"]))
        if not lift:
            raise ValueError("A lift link needs a lift name.")
        path = path.format(lift=lift)
    origin = get_settings().site_origin
    if origin is None:
        raise RuntimeError("Links need MCP_RESOURCE_URL to be set: it gives the site's address.")
    return origin + path
