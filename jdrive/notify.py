"""Notifications: the bell in the menu. Shares and files arriving also email
people (sharing.py sends share emails itself); these are the in-app notes."""
from sqlalchemy import func, select, update

from .models import Notification

KEEP = 100  # notes per person


def send(s, user_id, text, url, moment):
    if user_id is None:
        return
    s.add(Notification(user_id=user_id, at=moment, text=text[:300], url=url))


def unread_count(user):
    from . import db
    if user is None:
        return 0
    return db.session().scalar(select(func.count()).select_from(Notification)
                               .where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0


def latest(s, user):
    return list(s.scalars(select(Notification).where(Notification.user_id == user.id)
                          .order_by(Notification.at.desc(), Notification.id.desc()).limit(KEEP)))


def mark_all_read(s, user, moment):
    s.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
              .values(read_at=moment))


def init_app(app):
    app.jinja_env.globals["unread_count"] = unread_count
