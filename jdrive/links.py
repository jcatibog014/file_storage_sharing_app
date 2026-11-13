""""Anyone with the link": a secret address that opens one file or folder.

The secret is the token in the address (/s/<token>). We never store it:

    token = base64url(HMAC-SHA256(SECRET_KEY, "share-link:<id>:<nonce>"))

and the database keeps only sha256(token), to find the link when someone
opens it. So:
- the owner can always see the link again (we can make the token again);
- a stolen copy of the database opens nothing (it has no SECRET_KEY);
- "Make a new link" changes the nonce, and the old address stops working.

A link gives its role (Viewer) only inside the linked item's tree: a folder
link opens that folder and everything in it, never the folders around it.
"""
import base64
import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, time as day_time, timedelta

from flask import current_app, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from . import limits, tree
from .dates import from_local, utcnow
from .models import ShareLink

VIEWER, UPLOADER = "viewer", "uploader"
LINK_ROLES = {VIEWER: "Can view", UPLOADER: "Can upload"}
PASSWORD_MIN_LENGTH = 4
UNLOCK_SECONDS = 3600          # a right password is remembered this long, in this browser
PASSWORD_TRIES = 10            # per link and network, every 15 minutes


class Problem(Exception):
    pass


def _token(link):
    key = current_app.config["SECRET_KEY"].encode()
    digest = hmac.new(key, f"share-link:{link.id}:{link.nonce}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def token_for(link):
    return _token(link)


def address(link, external=True):
    return url_for("public.link", token=_token(link), _external=external)


def for_item(s, item):
    return s.scalar(select(ShareLink).where(ShareLink.item_id == item.id))


def turn_on(s, actor, item, moment):
    """Make the item's link (or switch it back on)."""
    link = for_item(s, item)
    if link is None:
        link = ShareLink(item_id=item.id, nonce="", token_hash="pending-" + secrets.token_hex(16),
                         created_by_id=actor.id, created_at=moment)
        s.add(link)
        s.flush()  # the token needs the id
        renew(link)
    link.disabled_at = None
    return link


def turn_off(link, moment):
    link.disabled_at = moment


def renew(link):
    """A fresh secret: the old address stops working."""
    link.nonce = secrets.token_hex(16)
    link.token_hash = _hash(_token(link))


def find(s, token):
    """The link for a token from an address, or None. Off links count as none."""
    if not token or len(token) > 100:
        return None
    link = s.scalar(select(ShareLink).where(ShareLink.token_hash == _hash(token)))
    if link is None or link.disabled_at is not None:
        return None
    # The hash matched; check the token itself too, in constant time.
    if not hmac.compare_digest(_token(link), token):
        return None
    return link


def is_open(link, moment=None):
    """Can this link be used right now? Not if it is off or has run out."""
    if link is None or link.disabled_at is not None:
        return False
    return link.expires_at is None or link.expires_at > (moment or utcnow())


def expiry_from_day(text, moment):
    """"2026-12-31" -> the end of that day, Philippine time, as naive UTC. "" -> None (never)."""
    if not text:
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise Problem("That date isn't right.") from None
    end = from_local(datetime.combine(day, day_time(23, 59, 59)))
    if end <= moment:
        raise Problem("Choose a day after today, or no end date.")
    if end > moment + timedelta(days=366):
        raise Problem("A link can work for at most a year. Make a new one after that.")
    return end


def save_settings(link, item, role, expires_text, allow_download, password_action, password, moment):
    """The Share page's link settings form."""
    if role not in LINK_ROLES:
        raise Problem("Choose Can view or Can upload.")
    if role == UPLOADER and not item.is_folder:
        raise Problem("Upload links are for folders.")
    expires = expiry_from_day(expires_text, moment)
    if password_action == "set":
        if len(password) < PASSWORD_MIN_LENGTH:
            raise Problem(f"Use a password of at least {PASSWORD_MIN_LENGTH} characters.")
        link.password_hash = generate_password_hash(password)
    elif password_action == "remove":
        link.password_hash = None
    link.role, link.expires_at, link.allow_download = role, expires, allow_download


# ---------- The password ----------
# A right password unlocks the link in this browser's session for an hour. The
# session remembers a fingerprint of the link's nonce and password hash, so a
# new password (or a new link) locks everyone out again.

def _fingerprint(link):
    return hashlib.sha256(f"{link.nonce}:{link.password_hash}".encode()).hexdigest()[:16]


def is_unlocked(link):
    if not link.password_hash:
        return True
    remembered = session.get("unlocked_links", {}).get(str(link.id))
    return bool(remembered) and remembered[0] == _fingerprint(link) and remembered[1] > time.time()


def try_password(s, link, password, moment):
    """True if right (and remember it). Wrong tries are limited per link and network."""
    if not limits.hit(s, f"link-password:{link.id}:{limits.client_ip()}", PASSWORD_TRIES, 900, moment):
        raise Problem("Too many wrong tries. Please wait 15 minutes.")
    if not check_password_hash(link.password_hash or "", password or ""):
        return False
    unlocked = dict(session.get("unlocked_links", {}))
    unlocked[str(link.id)] = [_fingerprint(link), time.time() + UNLOCK_SECONDS]
    session["unlocked_links"] = unlocked
    return True


def path_inside(s, link, item_id):
    """The path from the linked item down to item_id, or None if item_id isn't inside the link's tree."""
    path = tree.ancestors(s, item_id)
    ids = [item.id for item in path]
    if link.item_id not in ids or any(item.trashed_at is not None for item in path):
        return None  # outside the link's tree, or in the Trash
    return path[ids.index(link.item_id):]
