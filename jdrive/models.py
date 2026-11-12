"""The data model: every table, as a Python class (SQLAlchemy 2.0 ORM).

To change a table, edit it here, then make a migration (see AGENTS.md).
Times are naive UTC (dates.utcnow()).
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, false, func
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


# ---------- Files and folders ----------

FOLDER = "folder"
FILE = "file"


class Blob(Base):
    """One object in the bucket. Its key is random ("b/<32 hex>"), never the
    file's name, so a key says nothing about the file and can't be guessed.

    A blob starts "pending" when an upload begins and becomes "ready" once we
    have checked that the object really arrived with the promised size. Blobs
    are shared: two versions (a restored old version, or every demo's copy of a
    sample file) can point at the same blob, so a blob is only deleted from the
    bucket when nothing points at it any more (files.sweep_blobs)."""
    __tablename__ = "blobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    content_type: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Sample files for Try Demo: kept for good, whoever points at them.
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Item(Base):
    """A file or a folder. One table for both, because they live in the same
    tree: parent_id is the folder it is in (None = the top of its owner's
    My Drive). Deleting a folder deletes everything in it (ON DELETE CASCADE).

    owner_id is the owner of the whole tree: a file an Editor uploads into a
    shared folder still belongs to the folder's owner (and counts towards the
    owner's storage), the way OneDrive does it. created_by_id remembers who
    really added it."""
    __tablename__ = "items"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    # Files only: a copy of the current version's size and type, for quick lists.
    size: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    content_type: Mapped[str | None] = mapped_column(String(120))

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])

    @property
    def is_folder(self):
        return self.kind == FOLDER


class FileVersion(Base):
    """One version of a file. The newest (highest number) is the current one."""
    __tablename__ = "file_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    # RESTRICT: a blob can't vanish while a version still points at it.
    blob_id: Mapped[int] = mapped_column(ForeignKey("blobs.id", ondelete="RESTRICT"), index=True)
    size: Mapped[int] = mapped_column(BigInteger)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    blob: Mapped[Blob] = relationship()
    uploaded_by: Mapped[User | None] = relationship()


class Upload(Base):
    """An upload that has started: the browser was given a signed link to put
    the file straight into the bucket, and will tell us when it is done
    (files.finish_upload). Unfinished ones run out after an hour (tick.py)."""
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    blob_id: Mapped[int] = mapped_column(ForeignKey("blobs.id", ondelete="CASCADE"), index=True)
    # Where the new file goes (None = the top of the owner's My Drive)...
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # ...or which file it is a new version of.
    target_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    blob: Mapped[Blob] = relationship()


# ---------- Sharing ----------

class Share(Base):
    """Someone added by email to a file or folder, with a role. A share on a
    folder covers everything inside it (permissions.py).

    An email with no confirmed account yet is a waiting share (user_id None).
    It is handed over only when that address is PROVEN, by the emailed code
    (sharing.claim_waiting), so typing someone else's email at sign-up gets you nothing."""
    __tablename__ = "shares"
    __table_args__ = (UniqueConstraint("item_id", "email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    item: Mapped[Item] = relationship()
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])


class ShareLink(Base):
    """"Anyone with the link": one per item. The link's secret (its token) is
    never stored. It is an HMAC of the link's id and a random nonce, made with
    SECRET_KEY, so the owner can see it again, but a copy of the database alone
    opens nothing. We find a link by the sha256 of its token. A new nonce
    ("Make a new link") makes the old address stop working."""
    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(10), default="viewer")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime)
    open_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    item: Mapped[Item] = relationship()
