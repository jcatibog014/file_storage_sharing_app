"""Filters for the templates: dates and times the way people read them."""
from datetime import datetime

from .dates import to_local, today, utcnow


def nice_date(moment):
    """Oct 30, 2026 (written by hand: %-d doesn't work on Windows).
    Takes a date or a moment from the database (naive UTC)."""
    if isinstance(moment, datetime):
        moment = to_local(moment)
    return f"{moment:%b} {moment.day}, {moment.year}"


def short_date(day):
    """Oct 30 (this year) or Oct 30, 2027."""
    if isinstance(day, datetime):
        day = to_local(day)
    return f"{day:%b} {day.day}" if day.year == today().year else nice_date(day)


def clock(moment):
    """2:30 PM"""
    local = to_local(moment)
    return f"{local.hour % 12 or 12}:{local:%M %p}"


def nice_datetime(moment):
    """Oct 30, 2026, 2:30 PM"""
    return f"{nice_date(moment)}, {clock(moment)}"


def ago(moment):
    """How long ago, the way a feed says it: just now, 5 min ago, 3 h ago, Oct 30."""
    seconds = (utcnow() - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 7 * 86400:
        days = int(seconds // 86400)
        return "yesterday" if days == 1 else f"{days} days ago"
    return short_date(moment)


def duration(minutes):
    """45 min, 3 h 20 min, 2 d 4 h (for how long something is kept)."""
    minutes = int(round(minutes or 0))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def plural(count, word, many=None):
    """1 file, 3 files."""
    return f"{count} {word if count == 1 else many or word + 's'}"


def filesize(size):
    """1 byte, 512 bytes, 3.4 KB, 12 MB, 1.2 GB. 1 KB = 1,000 bytes, like phones and Drive show it."""
    size = int(size or 0)
    if size < 1000:
        return "1 byte" if size == 1 else f"{size} bytes"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1000
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if size < 10 else f"{size:.0f} {unit}"
    return f"{size:.0f} TB"


def local_day_iso(moment):
    """2026-12-31: the Philippine day of a moment, for a date box."""
    return to_local(moment).date().isoformat()


def init_app(app):
    for function in (nice_date, short_date, clock, nice_datetime, ago, duration, filesize, local_day_iso):
        app.jinja_env.filters[function.__name__] = function
    app.jinja_env.globals["plural"] = plural
