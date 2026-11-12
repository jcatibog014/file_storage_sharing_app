"""The data model: every table, as a Python class (SQLAlchemy 2.0 ORM).

To change a table, edit it here, then make a migration (see AGENTS.md).
Times are naive UTC (dates.utcnow()).
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    """One account. The email is the login, and it must be real: the account
    works only after the 6-digit code we email to it is typed in (codes.py)."""
    __tablename__ = "users"
    # A deleted demo's id is never given to the next new user, so an old
    # cookie (user_id + session_version) can't open someone else's account.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # always lower-case
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    # None until the emailed code is typed in. Shares waiting for this email
    # are only handed over after that (sharing.claim_pending).
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Adding 1 logs the account out everywhere (load_user compares it with the cookie).
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # Try Demo: each visitor gets their own sample people, deleted at demo_expires_at.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    demo_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    @property
    def is_verified(self):
        return self.email_verified_at is not None

    @property
    def initial(self):
        return (self.name or "?")[:1].upper()

    @property
    def first_name(self):
        return (self.name or "").split(" ")[0]


class EmailCode(Base):
    """A 6-digit code we emailed: to prove an email is real (purpose "verify")
    or to set a new password ("reset"). Only a hash is kept, it runs out after
    15 minutes, and 5 wrong tries use it up."""
    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    tries_left: Mapped[int] = mapped_column(Integer, default=5)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class RateLimit(Base):
    """How often something was tried in the current window (limits.py), kept in
    the database so it holds across the live site's restarts and workers."""
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)
