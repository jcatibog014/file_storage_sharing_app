"""The activity log: who did what, when, to which file or folder.

log() is the only writer, and rows are never changed or deleted (except
with the owner's account). The owner sees everything that happened to their
files on the Activity page; anyone who can open an item sees its own
history (and, for a folder, what happened inside it) in its side panel.
"""
from sqlalchemy import or_, select

from . import tree
from .models import Activity, Item

# verb -> how the sentence reads after the person's name.
SENTENCES = {
    "uploaded": "uploaded",
    "new_version": "uploaded a new version of",
    "version_restored": "made an old version current of",
    "version_deleted": "deleted an old version of",
    "folder_made": "made the folder",
    "renamed": "renamed",
    "moved": "moved",
    "trashed": "moved to the Trash",
    "restored": "restored",
    "deleted": "deleted for good",
    "downloaded": "downloaded",
    "zipped": "downloaded as a ZIP",
    "shared": "shared",
    "unshared": "removed someone from",
    "role_changed": "changed a role on",
    "left": "left",
    "owner_changed": "passed on ownership of",
    "link_on": "turned on the link to",
    "link_off": "turned off the link to",
    "link_new": "made a new link to",
    "link_settings": "changed the link settings of",
    "link_opened": "opened the link to",
    "link_upload": "sent a file into",
}
LINK_VISITOR = "Someone with the link"


def log(s, actor, item, verb, moment, detail=None, label=None):
    """Add one line. actor is the person (or None for a link visitor, with label)."""
    s.add(Activity(at=moment, owner_id=item.owner_id, actor_id=actor.id if actor else None,
                   actor_label=None if actor else (label or LINK_VISITOR), item_id=item.id,
                   item_name=item.name[:255], item_kind=item.kind, verb=verb,
                   detail=(detail or None) and str(detail)[:300]))


def sentence(row, with_item=True):
    """The words after the name. On an item's own page the item's name is left off,
    so "uploaded a new version of" becomes "uploaded a new version"."""
    words = SENTENCES.get(row.verb, row.verb)
    if not with_item:
        for ending in (" of", " to", " into", " from"):
            if words.endswith(ending):
                return words[: -len(ending)]
    return words


def who(row, viewer):
    if row.actor_id and viewer is not None and row.actor_id == viewer.id:
        return "You"
    if row.actor is not None:
        return row.actor.name
    return row.actor_label or "Someone"


def for_item(s, item, limit=30):
    """The item's own history; for a folder, what happened inside it too."""
    ids = tree.descendant_ids(s, item.id) if item.is_folder else [item.id]
    return list(s.scalars(select(Activity).where(Activity.item_id.in_(ids))
                          .order_by(Activity.at.desc(), Activity.id.desc()).limit(limit)))


def for_owner(s, user, limit=100, verb=None):
    """The Activity page: everything that happened to your files, and everything you did."""
    query = select(Activity).where(or_(Activity.owner_id == user.id, Activity.actor_id == user.id))
    if verb:
        query = query.where(Activity.verb == verb)
    return list(s.scalars(query.order_by(Activity.at.desc(), Activity.id.desc()).limit(limit)))


def item_still_there(s, row):
    return row.item_id is not None and s.get(Item, row.item_id) is not None


def init_app(app):
    app.jinja_env.globals.update(activity_sentence=sentence, activity_who=who)
