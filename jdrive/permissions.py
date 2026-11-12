"""EVERY rule about who may do what to a file or folder lives here.

Four roles, each able to do everything the one before it can:

    Viewer    open, preview, download
    Editor    + upload, new folder, rename, move, new version, move to trash
    Co-owner  + share (people and links), restore from trash
    Owner     + delete for good, give the owner role away; can't be removed

Views never compare roles themselves: they call require() with an action,
and get back the item, its path and the person's role, or a 404. A 404 (not
a 403) for an item you can't see means a guessed id tells you nothing,
not even that it exists. tests/test_permissions.py is a table of every role
against every action.
"""
from dataclasses import dataclass

from flask import abort

from . import tree
from .models import Item

VIEWER, EDITOR, COOWNER, OWNER = "viewer", "editor", "coowner", "owner"
RANK = {None: 0, VIEWER: 1, EDITOR: 2, COOWNER: 3, OWNER: 4}
ROLE_NAMES = {VIEWER: "Viewer", EDITOR: "Editor", COOWNER: "Co-owner", OWNER: "Owner"}
# The roles someone can be given (Owner is only ever passed on, see sharing.py).
SHARE_ROLES = (VIEWER, EDITOR, COOWNER)

# The least role each action needs.
NEEDS = {
    "view": VIEWER,
    "download": VIEWER,
    "upload": EDITOR,
    "new_folder": EDITOR,
    "rename": EDITOR,
    "move": EDITOR,
    "new_version": EDITOR,
    "trash": EDITOR,
    "share": COOWNER,
    "restore": COOWNER,
    "delete_forever": OWNER,
    "transfer": OWNER,
}


def allows(role, action):
    return RANK[role] >= RANK[NEEDS[action]]


def higher(a, b):
    return a if RANK[a] >= RANK[b] else b


# Other modules add ways to have a role (sharing.py: shares; links.py: an open link).
# Each is a function (s, user, path) -> role or None; the highest answer wins.
role_sources = []


def role_on_path(s, user, path):
    """The person's role on the last item of path (the list from tree.ancestors)."""
    if not path:
        return None
    item = path[-1]
    if user is not None and item.owner_id == user.id:
        return OWNER
    role = None
    for source in role_sources:
        role = higher(role, source(s, user, path))
    return role


def role_for(s, user, item):
    return role_on_path(s, user, tree.ancestors(s, item.id))


@dataclass
class Access:
    item: Item
    path: list   # [top folder, ..., item]
    role: str

    def can(self, action):
        return allows(self.role, action)

    @property
    def role_name(self):
        return ROLE_NAMES[self.role]


def check(s, user, item_id, action="view"):
    """An Access if allowed, or None. Never raises."""
    item = s.get(Item, item_id) if item_id is not None else None
    if item is None:
        return None
    path = tree.ancestors(s, item.id)
    role = role_on_path(s, user, path)
    if role is None or not allows(role, action):
        return None
    return Access(item, path, role)


def require(s, user, item_id, action="view", kind=None):
    """The gate for every page and form about one item.

    404 when the person can't see the item at all (or it is the wrong kind),
    403 when they can see it but their role isn't enough for the action."""
    item = s.get(Item, item_id) if item_id is not None else None
    if item is None or (kind is not None and item.kind != kind):
        abort(404)
    path = tree.ancestors(s, item.id)
    role = role_on_path(s, user, path)
    if role is None:
        abort(404)
    if not allows(role, action):
        abort(403, f"You're a {ROLE_NAMES[role]} here, so you can't do that. Ask the owner.")
    return Access(item, path, role)


def init_app(app):
    app.jinja_env.globals.update(ROLE_NAMES=ROLE_NAMES, role_allows=allows)
