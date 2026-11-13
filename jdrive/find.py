"""Finding things: Search, Starred and Recent.

Search looks through everything the person may open: their own drive, and
every tree shared with them. Anything in the Trash, or that they can no
longer open, is left out (permissions.check on every result).
"""
from sqlalchemy import delete, func, select

from . import permissions, tree
from .models import Item, Recent, Share, Star

MAX_RESULTS = 100
RECENT_LENGTH = 50
TYPE_CHOICES = [("", "Any type"), ("folder", "Folders"), ("image", "Pictures"), ("pdf", "PDFs"),
                ("doc", "Documents"), ("sheet", "Spreadsheets"), ("slides", "Slides"), ("video", "Videos"),
                ("audio", "Audio"), ("text", "Text"), ("zip", "Archives"), ("code", "Code"), ("other", "Other")]
WHERE_CHOICES = [("all", "Anywhere"), ("mine", "My Drive"), ("shared", "Shared with me")]


def _like(text):
    """A LIKE pattern that matches the words as typed (% and _ are just letters here)."""
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search(s, user, words, kind="", where="all"):
    from .files import kind_of
    words = (words or "").strip()[:100]
    if not words and not kind:
        return []
    pools = []
    if where in ("all", "mine"):
        pools.append(Item.owner_id == user.id)
    if where in ("all", "shared"):
        roots = list(s.scalars(select(Share.item_id).where(Share.user_id == user.id)))
        ids = set()
        for root in roots[:200]:
            ids.update(tree.descendant_ids(s, root))
        if ids:
            pools.append(Item.id.in_(ids) & (Item.owner_id != user.id))
    if not pools:
        return []
    query = select(Item).where(Item.trashed_at.is_(None))
    query = query.where(pools[0] if len(pools) == 1 else (pools[0] | pools[1]))
    if words:
        query = query.where(func.lower(Item.name).like(_like(words), escape="\\"))
    results = []
    for item in s.scalars(query.order_by(Item.updated_at.desc(), Item.id.desc()).limit(MAX_RESULTS * 3)):
        if kind and kind_of(item) != kind:
            continue
        access = permissions.check(s, user, item.id)  # leaves out anything inside a trashed folder too
        if access is not None:
            results.append(access)
        if len(results) >= MAX_RESULTS:
            break
    return results


# ---------- Stars ----------

def is_starred(s, user, item):
    return s.get(Star, (user.id, item.id)) is not None


def toggle_star(s, user, item, moment):
    """Star or unstar. Returns True if it is starred now."""
    star = s.get(Star, (user.id, item.id))
    if star is None:
        s.add(Star(user_id=user.id, item_id=item.id, created_at=moment))
        return True
    s.delete(star)
    return False


def starred(s, user):
    rows = s.scalars(select(Star).where(Star.user_id == user.id).order_by(Star.created_at.desc()))
    found = []
    for star in rows:
        access = permissions.check(s, user, star.item_id)
        if access is not None:
            found.append(access)
    return found


def starred_ids(s, user, items):
    ids = [item.id for item in items]
    if not ids:
        return set()
    return set(s.scalars(select(Star.item_id).where(Star.user_id == user.id, Star.item_id.in_(ids))))


# ---------- Recent ----------

def touch(s, user, item, moment):
    """Remember that this person opened (or added) this item just now."""
    if user is None:
        return
    seen = s.get(Recent, (user.id, item.id))
    if seen is None:
        s.add(Recent(user_id=user.id, item_id=item.id, seen_at=moment))
    else:
        seen.seen_at = moment


def recent(s, user):
    rows = list(s.scalars(select(Recent).where(Recent.user_id == user.id)
                          .order_by(Recent.seen_at.desc()).limit(RECENT_LENGTH * 2)))
    found = []
    for row in rows:
        access = permissions.check(s, user, row.item_id)
        if access is not None:
            found.append((access, row.seen_at))
        if len(found) >= RECENT_LENGTH:
            break
    return found


def forget_old(s, user, keep=RECENT_LENGTH * 4):
    """Keep the Recent list short."""
    old = select(Recent.item_id).where(Recent.user_id == user.id).order_by(Recent.seen_at.desc()).offset(keep)
    s.execute(delete(Recent).where(Recent.user_id == user.id, Recent.item_id.in_(old.scalar_subquery())))
