"""Storage space: how much each person's files take, and the limit.

Everything the owner owns counts: every version of every file, and the
Trash too (until it is emptied), plus uploads that have started but not
finished, so two uploads at once can't both squeeze past the limit.
Files an Editor adds to a shared folder count for the folder's OWNER.
"""
from sqlalchemy import func, select

from . import files
from .dates import utcnow
from .models import FileVersion, Item, Upload, User


def used(s, owner_id):
    stored = s.scalar(select(func.coalesce(func.sum(FileVersion.size), 0))
                      .join(Item, Item.id == FileVersion.item_id).where(Item.owner_id == owner_id)) or 0
    waiting = s.scalar(select(func.coalesce(func.sum(Upload.size), 0))
                       .where(Upload.owner_id == owner_id, Upload.expires_at > utcnow())) or 0
    return int(stored) + int(waiting)


def check(s, owner_id, extra):
    """files.space_checks: refuse a file that wouldn't fit."""
    owner = s.get(User, owner_id)
    if owner is None:
        return
    if used(s, owner_id) + extra > owner.quota_bytes:
        from .formatting import filesize
        left = max(0, owner.quota_bytes - used(s, owner_id))
        whose = "your" if owner_id == _current_user_id() else f"{owner.first_name}'s"
        raise files.Problem(f"There isn't enough space in {whose} drive: {filesize(left)} left. "
                            "Empty the Trash or delete old versions to make room.")


def _current_user_id():
    from flask import g, has_request_context
    return g.user.id if has_request_context() and g.get("user") is not None else None


def usage(user):
    """For the bar in the menu: (used, limit, percent)."""
    from . import db
    amount = used(db.session(), user.id)
    percent = min(100, round(amount * 100 / user.quota_bytes)) if user.quota_bytes else 100
    return amount, user.quota_bytes, percent


def init_app(app):
    if check not in files.space_checks:
        files.space_checks.append(check)
    app.jinja_env.globals["storage_usage"] = usage
