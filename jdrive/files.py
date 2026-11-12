"""Files and folders: names, uploads, moving, deleting.

Every function here trusts that the caller has already asked permissions.py.
They take the database session (s) and change rows; the caller commits.

The upload, in three steps (the object-storage lesson):

1. start_upload: we check the name, size and space, make a "pending" blob
   with a random key, and give the browser a signed PUT link for exactly
   that key, type and size, valid for 10 minutes.
2. The browser PUTs the file straight into the bucket. Our server never
   carries the bytes, so big files don't tie it up.
3. finish_upload: we ask the bucket (HEAD) whether the object is really
   there with the promised size. Only then does the file appear in the
   folder. If anything is off, the object is deleted and nothing appears.
"""
import mimetypes
import re
from datetime import timedelta
from pathlib import PurePosixPath

from sqlalchemy import delete, exists, func, select

from . import storage, tree
from .models import FILE, FOLDER, Blob, FileVersion, Item, Upload

MAX_FILE_SIZE = 100 * 1000 * 1000   # 100 MB per file
NAME_MAX_LENGTH = 255
UPLOAD_LIFETIME = timedelta(hours=1)
TRASH_DAYS = 30                     # the Trash deletes things for good after this
MAX_VERSIONS = 10                   # older versions than this are deleted
MAX_OPEN_UPLOADS = 50               # started but not finished, per person
FALLBACK_TYPE = "application/octet-stream"
TYPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}")


class Problem(Exception):
    """Something the person can fix: the message says what."""


# ---------- Names and types ----------

def clean_name(text):
    """A name as people see it: no slashes or control characters, trimmed."""
    name = "".join(ch for ch in (text or "") if ord(ch) >= 32 and ord(ch) != 127)
    name = name.replace("/", "-").replace("\\", "-").strip().strip(".").strip()
    if not name:
        raise Problem("Give it a name.")
    if len(name) > NAME_MAX_LENGTH:
        raise Problem(f"Use at most {NAME_MAX_LENGTH} characters.")
    return name


def content_type_for(name, declared):
    """The type we store and send back. A browser's word is only taken if it looks
    like a real type; otherwise we guess from the name, or say "just bytes"."""
    declared = (declared or "").split(";")[0].strip().lower()
    if TYPE_PATTERN.fullmatch(declared):
        return declared
    guessed, _ = mimetypes.guess_type(name)
    return guessed or FALLBACK_TYPE


EXTENSION_KINDS = {
    "pdf": "pdf",
    "doc": "doc", "docx": "doc", "odt": "doc", "rtf": "doc", "pages": "doc",
    "xls": "sheet", "xlsx": "sheet", "ods": "sheet", "csv": "sheet", "numbers": "sheet",
    "ppt": "slides", "pptx": "slides", "odp": "slides", "key": "slides",
    "zip": "zip", "rar": "zip", "7z": "zip", "gz": "zip", "tar": "zip",
    "txt": "text", "md": "text", "log": "text",
    "py": "code", "js": "code", "html": "code", "css": "code", "json": "code", "java": "code",
    "c": "code", "cpp": "code", "ts": "code", "sql": "code", "xml": "code", "yml": "code", "yaml": "code",
}
KIND_NAMES = {
    "folder": "Folder", "image": "Picture", "pdf": "PDF", "doc": "Document", "sheet": "Spreadsheet",
    "slides": "Slides", "video": "Video", "audio": "Audio", "text": "Text", "zip": "Archive",
    "code": "Code", "other": "File",
}


def extension(name):
    return PurePosixPath(name).suffix.lower().lstrip(".")


def kind_of(item):
    """"folder", "image", "pdf", ... for the icon and the type filter."""
    if item.kind == FOLDER:
        return "folder"
    by_extension = EXTENSION_KINDS.get(extension(item.name))
    if by_extension:
        return by_extension
    major = (item.content_type or "").split("/")[0]
    if major in ("image", "video", "audio"):
        return major
    if major == "text":
        return "text"
    return "other"


def icon_name(item):
    kind = kind_of(item)
    return "folder" if kind == "folder" else ("file" if kind == "other" else f"file-{kind}")


def unique_name(s, owner_id, parent_id, name, skip_id=None):
    """The name, or "name (1).ext", "name (2).ext"... if the folder already has it."""
    siblings = select(func.lower(Item.name)).where(Item.owner_id == owner_id, Item.trashed_at.is_(None),
                                                   Item.parent_id.is_(parent_id) if parent_id is None
                                                   else Item.parent_id == parent_id)
    if skip_id is not None:
        siblings = siblings.where(Item.id != skip_id)
    taken = set(s.scalars(siblings))
    if name.lower() not in taken:
        return name
    path = PurePosixPath(name)
    stem, suffix = (path.stem, path.suffix) if path.suffix and path.stem else (name, "")
    number = 1
    while f"{stem} ({number}){suffix}".lower() in taken:
        number += 1
    return f"{stem} ({number}){suffix}"


# ---------- Reading ----------

SORTS = {"name": "Name", "modified": "Last changed", "size": "Size"}


def children(s, owner_id, parent_id, sort="name", descending=False):
    """What is in a folder (or the top of a My Drive): folders first, then files."""
    query = select(Item).where(Item.owner_id == owner_id, Item.trashed_at.is_(None))
    query = query.where(Item.parent_id.is_(None)) if parent_id is None else query.where(Item.parent_id == parent_id)
    column = {"modified": Item.updated_at, "size": Item.size}.get(sort, func.lower(Item.name))
    order = column.desc() if descending else column.asc()
    folders_first = (Item.kind != FOLDER)  # False (folders) sorts before True
    return list(s.scalars(query.order_by(folders_first, order, Item.id)))


def is_trashed(path):
    """Is anything on the path (the item or a folder above it) in the Trash?"""
    return any(item.trashed_at is not None for item in path)


def versions(s, item):
    """Newest first."""
    return list(s.scalars(select(FileVersion).where(FileVersion.item_id == item.id)
                          .order_by(FileVersion.number.desc())))


def current_version(s, item):
    return s.scalar(select(FileVersion).where(FileVersion.item_id == item.id)
                    .order_by(FileVersion.number.desc()).limit(1))


def folder_size_and_count(s, folder_id):
    ids = tree.descendant_ids(s, folder_id, include_self=False)
    if not ids:
        return 0, 0
    total, count = s.execute(select(func.coalesce(func.sum(Item.size), 0), func.count())
                             .where(Item.id.in_(ids), Item.kind == FILE)).one()
    return total, count


# ---------- Making things ----------

def create_folder(s, user, owner_id, parent, name, moment):
    """A new folder in parent (None = the top of owner_id's My Drive)."""
    name = clean_name(name)
    if parent is not None:
        if not parent.is_folder:
            raise Problem("Folders can only go inside folders.")
        if len(tree.ancestors(s, parent.id)) >= tree.MAX_DEPTH:
            raise Problem(f"Folders can go at most {tree.MAX_DEPTH} levels deep.")
    parent_id = parent.id if parent else None
    folder = Item(kind=FOLDER, name=unique_name(s, owner_id, parent_id, name), parent_id=parent_id,
                  owner_id=owner_id, created_by_id=user.id, created_at=moment, updated_at=moment)
    s.add(folder)
    s.flush()
    return folder


def check_size(size):
    if size < 0:
        raise Problem("That file's size isn't right.")
    if size > MAX_FILE_SIZE:
        raise Problem(f"Files can be at most {MAX_FILE_SIZE // 1000 // 1000} MB.")


# Other modules add checks before a file is stored (stage 4: storage space).
# Each is a function (s, owner_id, extra_bytes) that raises Problem.
space_checks = []


def check_space(s, owner_id, size):
    for check in space_checks:
        check(s, owner_id, size)


def start_upload(s, user, owner_id, parent, name, size, declared_type, moment, target=None):
    """Step 1: returns (upload, link). link = {"url", "method", "headers"} for the browser."""
    name = clean_name(name)
    check_size(size)
    check_space(s, owner_id, size)
    open_uploads = s.scalar(select(func.count()).select_from(Upload)
                            .where(Upload.user_id == user.id, Upload.expires_at > moment))
    if open_uploads >= MAX_OPEN_UPLOADS:
        raise Problem("That's a lot of uploads at once. Wait for some to finish.")
    content_type = content_type_for(name, declared_type)
    blob = Blob(key=storage.new_key(), size=size, content_type=content_type, state="pending", created_at=moment)
    s.add(blob)
    s.flush()
    upload = Upload(user_id=user.id, blob_id=blob.id, parent_id=parent.id if parent else None, owner_id=owner_id,
                    target_item_id=target.id if target else None, name=name, size=size,
                    content_type=content_type, created_at=moment, expires_at=moment + UPLOAD_LIFETIME)
    s.add(upload)
    s.flush()
    return upload, storage.get().presign_put(blob.key, content_type, size)


def finish_upload(s, user, upload, moment):
    """Step 3: check the bucket really has the file, then show it. Returns the item.
    Raises Problem (and deletes the object) if it isn't right."""
    bucket = storage.get()
    blob = upload.blob
    head = bucket.head(blob.key)
    if head is None:
        raise Problem("The file didn't arrive. Please try the upload again.")
    arrived_type = (head["content_type"] or "").split(";")[0].strip().lower()
    if head["size"] != upload.size or arrived_type != upload.content_type:
        bucket.delete(blob.key)
        raise Problem("The file that arrived isn't the one we expected. Please try again.")
    blob.state = "ready"
    item = _add_file(s, user, upload.owner_id, upload.parent_id, upload.target_item_id,
                     upload.name, blob, moment)
    s.delete(upload)
    s.flush()
    return item


def _add_file(s, user, owner_id, parent_id, target_id, name, blob, moment):
    """A new file in a folder, or a new version of target_id."""
    if target_id is not None:
        item = s.get(Item, target_id)
        if item is None:
            raise Problem("That file isn't here any more.")
        number = (s.scalar(select(func.max(FileVersion.number)).where(FileVersion.item_id == item.id)) or 0) + 1
    else:
        item = Item(kind=FILE, name=unique_name(s, owner_id, parent_id, name), parent_id=parent_id,
                    owner_id=owner_id, created_by_id=user.id, created_at=moment)
        s.add(item)
        number = 1
    item.size, item.content_type, item.updated_at = blob.size, blob.content_type, moment
    s.flush()
    s.add(FileVersion(item_id=item.id, number=number, blob_id=blob.id, size=blob.size,
                      uploaded_by_id=user.id, created_at=moment))
    s.flush()
    _trim_versions(s, item)
    return item


def _trim_versions(s, item):
    """Keep the newest MAX_VERSIONS. The old blobs are swept by the timer (or now, by the caller)."""
    old = versions(s, item)[MAX_VERSIONS:]
    for version in old:
        s.info.setdefault("sweep_blob_ids", []).append(version.blob_id)
        s.delete(version)
    if old:
        s.flush()


def sweep_after_commit(s):
    """Sweep the blobs this request let go of (old versions trimmed)."""
    blob_ids = s.info.pop("sweep_blob_ids", [])
    if blob_ids:
        sweep_blobs(s, blob_ids)


def restore_version(s, user, item, version, moment):
    """Make an old version the current one again: a NEW version pointing at the
    same blob (nothing is copied, and the history stays complete)."""
    if version.item_id != item.id:
        raise Problem("That version belongs to another file.")
    check_space(s, item.owner_id, version.size)
    return _add_file(s, user, item.owner_id, item.parent_id, item.id, item.name, version.blob, moment)


def delete_version(s, item, version):
    """Delete one old version (never the current one). Returns its blob id to sweep."""
    if version.item_id != item.id:
        raise Problem("That version belongs to another file.")
    if version.id == current_version(s, item).id:
        raise Problem("That's the current version. Upload or restore another one first.")
    blob_id = version.blob_id
    s.delete(version)
    s.flush()
    return blob_id


def upload_through_server(s, user, owner_id, parent, file_storage, moment, target=None):
    """The no-JavaScript upload form: the file comes through Flask and we put it
    in the bucket ourselves. Same checks, same result."""
    name = clean_name(file_storage.filename or "")
    stream = file_storage.stream
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    check_size(size)
    check_space(s, owner_id, size)
    content_type = content_type_for(name, file_storage.mimetype)
    blob = Blob(key=storage.new_key(), size=size, content_type=content_type, state="ready", created_at=moment)
    storage.get().put_stream(blob.key, stream, content_type, size)
    s.add(blob)
    s.flush()
    return _add_file(s, user, owner_id, parent.id if parent else None, target.id if target else None,
                     name, blob, moment)


# ---------- Changing things ----------

def rename(s, item, name, moment):
    name = clean_name(name)
    if name == item.name:
        return item
    item.name = unique_name(s, item.owner_id, item.parent_id, name, skip_id=item.id)
    item.updated_at = moment
    return item


def move_problem(s, item, target):
    """Why item can't go into target (None = the top of the owner's My Drive), or None."""
    if target is not None:
        if not target.is_folder:
            return "Things can only be moved into folders."
        if target.owner_id != item.owner_id:
            return "That folder belongs to someone else. Download and upload instead."
        if target.id == item.id or (item.is_folder and target.id in tree.descendant_ids(s, item.id)):
            return "A folder can't go inside itself."
        if item.is_folder and len(tree.ancestors(s, target.id)) + 1 + tree.depth_below(s, item.id) > tree.MAX_DEPTH:
            return f"Folders can go at most {tree.MAX_DEPTH} levels deep."
    if (target.id if target else None) == item.parent_id:
        return "It's already there."
    return None


def move(s, item, target, moment):
    problem = move_problem(s, item, target)
    if problem:
        raise Problem(problem)
    item.parent_id = target.id if target else None
    item.name = unique_name(s, item.owner_id, item.parent_id, item.name, skip_id=item.id)
    item.updated_at = moment
    s.flush()
    return item


# ---------- The Trash ----------

def trash(s, user, item, moment):
    """Into the Trash: hidden from every list and page (with everything inside it),
    but still taking space, until it is restored or deleted for good."""
    item.trashed_at = moment
    item.trashed_by_id = user.id
    s.flush()


def restore(s, item, moment):
    """Out of the Trash, back where it was. If its folder is gone or in the
    Trash itself, it goes to the top of the owner's My Drive instead.
    Returns True if it went back to its own folder."""
    back_home = True
    if item.parent_id is not None and is_trashed(tree.ancestors(s, item.parent_id)):
        item.parent_id = None
        back_home = False
    item.trashed_at = None
    item.trashed_by_id = None
    item.name = unique_name(s, item.owner_id, item.parent_id, item.name, skip_id=item.id)
    item.updated_at = moment
    s.flush()
    return back_home


def trashed_items(s, user):
    """The Trash page: what you own that is in the Trash, and what you put there yourself."""
    return list(s.scalars(select(Item).where(Item.trashed_at.is_not(None),
                                             (Item.owner_id == user.id) | (Item.trashed_by_id == user.id))
                          .order_by(Item.trashed_at.desc(), Item.id)))


def purge_trash(s, moment, owner_id=None):
    """Delete for good what has been in the Trash for TRASH_DAYS. Returns blob ids to sweep."""
    query = select(Item).where(Item.trashed_at <= moment - timedelta(days=TRASH_DAYS))
    if owner_id is not None:
        query = query.where(Item.owner_id == owner_id)
    blob_ids = []
    for item in list(s.scalars(query.limit(200))):
        if s.get(Item, item.id) is not None:  # not already gone with a folder above it
            blob_ids += delete_forever(s, item)
    return blob_ids


def delete_forever(s, item):
    """Delete an item (and, for a folder, everything in it). Returns the blob ids
    it used: after the commit, sweep_blobs deletes those nothing else uses."""
    ids = tree.descendant_ids(s, item.id) if item.is_folder else [item.id]
    blob_ids = list(s.scalars(select(FileVersion.blob_id).where(FileVersion.item_id.in_(ids)).distinct()))
    s.execute(delete(Item).where(Item.id == item.id))
    s.flush()
    return blob_ids


def thumb_use():
    """Blobs used as picture thumbnails count as in use too (stage 6 fills this in)."""
    from sqlalchemy import false as sql_false
    return sql_false()


def sweep_blobs(s, blob_ids=None):
    """Delete objects that nothing points at any more, first from the bucket,
    then their rows. Sample files are kept. Commits as it goes; if the bucket
    fails, the row stays and the timer (tick.py) tries again later."""
    bucket = storage.get()
    in_use = exists().where(FileVersion.blob_id == Blob.id)
    waiting = exists().where(Upload.blob_id == Blob.id)
    query = select(Blob).where(~in_use, ~waiting, ~thumb_use(), Blob.is_sample.is_(False))
    if blob_ids is not None:
        if not blob_ids:
            return 0
        query = query.where(Blob.id.in_(blob_ids))
    count = 0
    for blob in list(s.scalars(query.limit(500))):
        bucket.delete(blob.key)
        s.delete(blob)
        s.commit()
        count += 1
    return count


def expire_uploads(s, moment):
    """Uploads that were started but never finished: forget them, and sweep their objects."""
    blob_ids = list(s.scalars(select(Upload.blob_id).where(Upload.expires_at <= moment)))
    s.execute(delete(Upload).where(Upload.expires_at <= moment))
    s.commit()
    return sweep_blobs(s, blob_ids)


def init_app(app):
    app.jinja_env.globals.update(kind_of=kind_of, icon_name=icon_name, KIND_NAMES=KIND_NAMES,
                                 MAX_VERSIONS=MAX_VERSIONS, TRASH_DAYS=TRASH_DAYS)
