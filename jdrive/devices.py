"""Logged-in devices (Settings > Where You're Logged In).

Every login makes a LoginSession row. The cookie holds its id and a random
secret; the row keeps only the secret's sha256. load_user() checks both on
every request, so ending a row logs that device out at once, even though
the cookie itself is still in that browser.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update

from .models import LoginSession

SEEN_EVERY = timedelta(minutes=5)   # last_seen_at is written at most this often
KEEP_ENDED = timedelta(days=90)     # ended rows: enough to know a device seen before


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def describe(user_agent):
    """"Edge on Windows" from a User-Agent header (just for people to read)."""
    agent = user_agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                           ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in agent), "A browser")
    system = next((name for key, name in (("Windows", "Windows"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Android", "Android"), ("Mac OS", "Mac"), ("Linux", "Linux"))
                   if key in agent), "")
    return f"{browser} on {system}" if system else browser


def start(s, user, device, ip, moment):
    """A new login. Returns (session id, secret) for the cookie."""
    secret = secrets.token_urlsafe(32)
    row = LoginSession(user_id=user.id, secret_hash=_hash(secret), created_at=moment, last_seen_at=moment,
                       device=device[:120], ip=(ip or "")[:64])
    s.add(row)
    s.flush()
    return row.id, secret


def check(s, user, session_id, secret, moment):
    """True if this cookie's login is still on. Notes when the device was last seen."""
    if not session_id or not secret:
        return False
    row = s.get(LoginSession, session_id)
    if row is None or row.user_id != user.id or row.ended_at is not None:
        return False
    if not hmac.compare_digest(row.secret_hash, _hash(secret)):
        return False
    if moment - row.last_seen_at > SEEN_EVERY:
        row.last_seen_at = moment
        s.commit()
    return True


def end(s, session_id, moment):
    s.execute(update(LoginSession).where(LoginSession.id == session_id, LoginSession.ended_at.is_(None))
              .values(ended_at=moment))


def end_others(s, user, keep_id, moment):
    """Log out every other device. Returns how many."""
    return s.execute(update(LoginSession).where(LoginSession.user_id == user.id, LoginSession.id != keep_id,
                                                LoginSession.ended_at.is_(None)).values(ended_at=moment)).rowcount


def active(s, user):
    return list(s.scalars(select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.ended_at.is_(None))
                          .order_by(LoginSession.last_seen_at.desc())))


def seen_before(s, user, device):
    return s.scalar(select(LoginSession.id).where(LoginSession.user_id == user.id, LoginSession.device == device)
                    .limit(1)) is not None


def has_any(s, user):
    return s.scalar(select(LoginSession.id).where(LoginSession.user_id == user.id).limit(1)) is not None


def purge(s, moment):
    """The timer: forget long-ended logins."""
    return s.execute(delete(LoginSession).where(LoginSession.ended_at < moment - KEEP_ENDED)).rowcount
