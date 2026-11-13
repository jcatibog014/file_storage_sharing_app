"""Walking the folder tree with recursive SQL (WITH RECURSIVE ...).

A folder only knows its parent. To find every folder above an item (for the
breadcrumbs, and to see which shares cover it) or everything below a folder
(to delete it, or put it in a ZIP), the database follows parent_id step by
step in one query: a "recursive common table expression". SQLite and
PostgreSQL both understand it, and SQLAlchemy writes it for us.
"""
from sqlalchemy import literal, select

from .models import Item

# Folders inside folders can go this deep. It keeps every walk short.
MAX_DEPTH = 32


def ancestors(s, item_id):
    """[top folder, ..., the item itself]: the path from the top of the tree."""
    start = select(Item.id, Item.parent_id, literal(0).label("depth")).where(Item.id == item_id).cte("up", recursive=True)
    step = select(Item.id, Item.parent_id, (start.c.depth + 1).label("depth")).join(
        start, Item.id == start.c.parent_id).where(start.c.depth < MAX_DEPTH)
    path = start.union_all(step)
    rows = s.execute(select(path.c.id, path.c.depth)).all()
    by_id = {item.id: item for item in s.scalars(select(Item).where(Item.id.in_([row.id for row in rows])))}
    return [by_id[row.id] for row in sorted(rows, key=lambda row: -row.depth) if row.id in by_id]


def descendant_ids(s, folder_id, include_self=True):
    """The ids of everything inside a folder, at any depth."""
    start = select(Item.id, literal(0).label("depth")).where(Item.id == folder_id).cte("down", recursive=True)
    step = select(Item.id, (start.c.depth + 1).label("depth")).join(
        start, Item.parent_id == start.c.id).where(start.c.depth < MAX_DEPTH + 1)
    rows = s.execute(select(start.union_all(step).c.id)).scalars().all()
    return [row for row in rows if include_self or row != folder_id]


def depth_below(s, folder_id):
    """How many levels of folders are inside this one (0 = none)."""
    start = select(Item.id, literal(0).label("depth")).where(Item.id == folder_id).cte("levels", recursive=True)
    step = select(Item.id, (start.c.depth + 1).label("depth")).join(
        start, Item.parent_id == start.c.id).where(Item.kind == "folder", start.c.depth < MAX_DEPTH + 1)
    tree = start.union_all(step)
    return s.scalar(select(tree.c.depth).order_by(tree.c.depth.desc()).limit(1)) or 0
