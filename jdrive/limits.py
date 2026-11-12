"""Rate limits: "at most N tries per window" for logins, codes and link passwords.

Each key ("login-ip:1.2.3.4", "code-send:7") has a counter and the time its
window started. The counters live in the database, so they hold across
restarts and every worker sees the same number.
"""
from datetime import timedelta

from flask import request
from sqlalchemy import delete, select

from .models import RateLimit


def hit(s, key, limit, window_seconds, moment):
    """Count one try. True if it is allowed, False once the limit is reached.
    The caller commits (a refused try is not counted again)."""
    key = key[:200]
    window = timedelta(seconds=window_seconds)
    # FOR UPDATE on PostgreSQL: two requests at once can't both read the old count.
    row = s.scalar(select(RateLimit).where(RateLimit.key == key).with_for_update())
    if row is None:
        s.add(RateLimit(key=key, count=1, window_start=moment))
        s.flush()
        return True
    if row.window_start + window <= moment:
        row.count, row.window_start = 1, moment
        return True
    if row.count >= limit:
        return False
    row.count += 1
    return True


def clear(s, key):
    """Forget a key's tries (after a right password)."""
    s.execute(delete(RateLimit).where(RateLimit.key == key[:200]))


def purge(s, moment, older_than=timedelta(days=1)):
    """Old windows are no use to anyone. The timer (tick.py) clears them."""
    return s.execute(delete(RateLimit).where(RateLimit.window_start < moment - older_than)).rowcount


def client_ip():
    """The visitor's address. On Render, ProxyFix makes this the real one."""
    return request.remote_addr or "unknown"
