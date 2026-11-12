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

from flask import current_app, url_for
from sqlalchemy import select

from . import tree
from .models import ShareLink


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


def is_open(link):
    """Can this link be used right now? (Stage 5 adds expiry dates.)"""
    return link is not None and link.disabled_at is None


def path_inside(s, link, item_id):
    """The path from the linked item down to item_id, or None if item_id isn't inside the link's tree."""
    path = tree.ancestors(s, item_id)
    ids = [item.id for item in path]
    if link.item_id not in ids:
        return None
    return path[ids.index(link.item_id):]
