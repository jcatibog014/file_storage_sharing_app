"""Previews: looking at a file without downloading it, and folders as ZIPs.

Safe by design:
- Only a short list of types is ever shown inline (INLINE_TYPES). Never HTML
  or SVG: those can carry scripts. Everything else is downloaded instead.
- Pictures, PDFs, video and audio come straight from the bucket, through a
  signed link made at the moment the browser asks (/files/<id>/preview).
- Text is read by the server (the first 200 KB) and shown escaped, as text.
- Thumbnails are made by Pillow from the picture's own pixels into a new
  small WebP, so nothing hidden in the original (or its EXIF data) comes along.
"""
import io
import zipfile
from datetime import timedelta

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from . import storage, tree
from .dates import utcnow
from .models import Blob, FileVersion, Item, Thumbnail

INLINE_TYPES = {
    "image/png": "image", "image/jpeg": "image", "image/gif": "image", "image/webp": "image",
    "application/pdf": "pdf",
    "video/mp4": "video", "video/webm": "video", "video/ogg": "video",
    "audio/mpeg": "audio", "audio/wav": "audio", "audio/x-wav": "audio", "audio/ogg": "audio",
    "audio/mp4": "audio", "audio/webm": "audio",
}
TEXT_ENDINGS = {"txt", "md", "csv", "log", "json", "py", "js", "css", "html", "sql", "xml", "yml", "yaml",
                "java", "c", "cpp", "ts", "ini", "toml"}
TEXT_PREVIEW_BYTES = 200 * 1000
THUMB_SIZE = 480                      # the long side, in pixels
THUMB_MAX_SOURCE = 25 * 1000 * 1000   # bigger pictures get no thumbnail
MEDIA_LINK_SECONDS = 600              # a video may be watched for a while: its link lasts 10 minutes
Image.MAX_IMAGE_PIXELS = 60_000_000   # refuse "decompression bombs" (tiny files, huge pictures)

ZIP_MAX_FILES = 1000
ZIP_MAX_BYTES = 500 * 1000 * 1000


def preview_kind(item):
    """"image", "pdf", "video", "audio", "text" or None (no preview)."""
    if item.is_folder:
        return None
    kind = INLINE_TYPES.get((item.content_type or "").lower())
    if kind:
        return kind
    from .files import extension
    if (item.content_type or "").startswith("text/") or extension(item.name) in TEXT_ENDINGS:
        return "text"
    return None


def inline_link(version, item):
    """A signed link that SHOWS the file (inline), for <img>, <video> and the PDF frame."""
    kind = INLINE_TYPES.get(version.blob.content_type.lower())
    if kind is None:
        return None
    ttl = MEDIA_LINK_SECONDS if kind in ("video", "audio", "pdf") else storage.DOWNLOAD_LINK_SECONDS
    return storage.get().presign_get(version.blob.key, item.name, version.blob.content_type, inline=True, ttl=ttl)


def text_start(version):
    """The start of a text file, as text, and whether there is more."""
    stream = storage.get().open(version.blob.key)
    try:
        data = stream.read(TEXT_PREVIEW_BYTES + 1)
    finally:
        stream.close()
    more = len(data) > TEXT_PREVIEW_BYTES
    return data[:TEXT_PREVIEW_BYTES].decode("utf-8", errors="replace"), more


# ---------- Thumbnails ----------

def thumbnail(s, version):
    """The thumbnail's blob for a picture version, made now if it isn't there yet.
    None if the file isn't a picture we can read."""
    found = s.get(Thumbnail, version.id)
    if found is not None:
        return s.get(Blob, found.blob_id) if found.blob_id else None
    blob = None
    if INLINE_TYPES.get(version.blob.content_type) == "image" and version.size <= THUMB_MAX_SOURCE:
        try:
            blob = _make_thumbnail(s, version)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            blob = None
    s.add(Thumbnail(version_id=version.id, blob_id=blob.id if blob else None, created_at=utcnow()))
    s.commit()
    return blob


def _make_thumbnail(s, version):
    bucket = storage.get()
    stream = bucket.open(version.blob.key)
    try:
        data = stream.read(THUMB_MAX_SOURCE + 1)
    finally:
        stream.close()
    with Image.open(io.BytesIO(data)) as picture:
        picture = ImageOps.exif_transpose(picture)  # phone photos: the right way up
        picture.thumbnail((THUMB_SIZE, THUMB_SIZE))
        if picture.mode not in ("RGB", "RGBA"):
            picture = picture.convert("RGBA" if "A" in picture.getbands() else "RGB")
        out = io.BytesIO()
        picture.save(out, "WEBP", quality=80)  # a new file: no EXIF, nothing from the original but pixels
    key = storage.new_key("t")
    bucket.put_bytes(key, out.getvalue(), "image/webp")
    blob = Blob(key=key, size=out.tell(), content_type="image/webp", state="ready", created_at=utcnow(),
                is_sample=version.blob.is_sample)
    s.add(blob)
    s.flush()
    return blob


def thumb_link(blob):
    return storage.get().presign_get(blob.key, "thumbnail.webp", "image/webp", inline=True, ttl=MEDIA_LINK_SECONDS)


# ---------- A folder as a ZIP ----------

class TooBig(Exception):
    pass


def zip_plan(s, folder):
    """[(name inside the ZIP, blob key)] for every file in the folder, at any depth,
    leaving out anything in the Trash. Raises TooBig past the limits."""
    ids = tree.descendant_ids(s, folder.id, include_self=False)
    items = {item.id: item for item in s.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    items[folder.id] = folder
    plan, total = [], 0
    for item in items.values():
        if item.is_folder or item.id == folder.id:
            continue
        parts, step, hidden = [], item, False
        while step is not None and step.id != folder.id:
            hidden = hidden or step.trashed_at is not None
            parts.append(step.name)
            step = items.get(step.parent_id)
        if hidden or step is None:
            continue
        version = s.scalar(select(FileVersion).where(FileVersion.item_id == item.id)
                           .order_by(FileVersion.number.desc()).limit(1))
        if version is None:
            continue
        total += version.size
        plan.append(("/".join(reversed(parts)), version.blob.key))
        if len(plan) > ZIP_MAX_FILES or total > ZIP_MAX_BYTES:
            raise TooBig()
    return sorted(plan)


class _Sink(io.RawIOBase):
    """Where zipfile writes; the generator hands the bytes on as they come.
    It can't seek, so zipfile writes sizes after each file (data descriptors)."""

    def __init__(self):
        self.chunks = []

    def writable(self):
        return True

    def write(self, data):
        self.chunks.append(bytes(data))
        return len(data)

    def take(self):
        data = b"".join(self.chunks)
        self.chunks.clear()
        return data


def zip_stream(plan):
    """Yield the ZIP piece by piece: the whole folder is never in memory."""
    bucket = storage.get()
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for name, key in plan:
            source = bucket.open(key)
            try:
                info = zipfile.ZipInfo(name, date_time=_now_tuple())
                info.compress_type = zipfile.ZIP_DEFLATED  # a hand-made ZipInfo would otherwise store
                with archive.open(info, "w", force_zip64=True) as target:
                    while chunk := source.read(256 * 1024):
                        target.write(chunk)
                        if sum(map(len, sink.chunks)) >= 256 * 1024:
                            yield sink.take()
            finally:
                source.close()
            yield sink.take()
    yield sink.take()


def _now_tuple():
    from .dates import to_local
    now = to_local(utcnow())
    return (now.year, now.month, now.day, now.hour, now.minute, now.second)


def recent_cutoff():
    return utcnow() - timedelta(days=90)
