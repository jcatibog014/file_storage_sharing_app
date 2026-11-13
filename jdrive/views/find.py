"""Search, Starred, Recent, and previews (pictures, PDFs, video, audio, text, folder ZIPs)."""
import re

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, stream_with_context, url_for

from .. import activity, db, files, find, links, permissions, previews, storage
from ..auth import login_required, safe_next
from ..dates import utcnow
from ..models import FILE, FOLDER

bp = Blueprint("find", __name__)


@bp.get("/search")
@login_required
def search():
    words = request.args.get("q", "")
    kind = request.args.get("type", "")
    where = request.args.get("where", "all")
    if kind not in dict(find.TYPE_CHOICES):
        kind = ""
    if where not in dict(find.WHERE_CHOICES):
        where = "all"
    results = find.search(db.session(), g.user, words, kind, where)
    return render_template("search.html", results=results, words=words, kind=kind, where=where,
                           TYPE_CHOICES=find.TYPE_CHOICES, WHERE_CHOICES=find.WHERE_CHOICES, menu_here="search")


@bp.get("/starred")
@login_required
def starred():
    return render_template("starred.html", rows=find.starred(db.session(), g.user), menu_here="starred")


@bp.get("/recent")
@login_required
def recent():
    s = db.session()
    rows = find.recent(s, g.user)
    find.forget_old(s, g.user)
    s.commit()
    return render_template("recent.html", rows=rows, menu_here="recent")


@bp.post("/items/<int:item_id>/star")
@login_required
def star(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "view")
    now_starred = find.toggle_star(s, g.user, access.item, utcnow())
    s.commit()
    flash(f"Starred {access.item.name}." if now_starred else f"Took the star off {access.item.name}.")
    return redirect(safe_next(request.form.get("next"), url_for("find.starred")))


# ---------- Previews ----------

def _framable(response):
    """A preview redirect is shown inside our own page (the PDF frame), so it may be framed by us."""
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/files/<int:item_id>/preview")
@login_required
def preview(item_id):
    """Check, then send the browser to a signed inline link (made right now)."""
    s = db.session()
    access = permissions.require(s, g.user, item_id, "view", kind=FILE)
    version = files.current_version(s, access.item)
    link = previews.inline_link(version, access.item) if version else None
    if link is None:
        abort(404)
    return _framable(redirect(link))


@bp.get("/files/<int:item_id>/thumb")
@login_required
def thumb(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "view", kind=FILE)
    version = files.current_version(s, access.item)
    blob = previews.thumbnail(s, version) if version else None
    if blob is None:
        abort(404)
    return _framable(redirect(previews.thumb_link(blob)))


def _zip_response(s, folder, plan_owner_note=""):
    try:
        plan = previews.zip_plan(s, folder)
    except previews.TooBig:
        abort(413, f"That folder is too big to download at once (at most {previews.ZIP_MAX_FILES} files and "
                   f"{previews.ZIP_MAX_BYTES // 1000 // 1000} MB). Download the folders inside it one by one.")
    name = re.sub(r'[\\/:*?"<>|]+', "-", folder.name) or "folder"
    response = Response(stream_with_context(previews.zip_stream(plan)), mimetype="application/zip")
    response.headers["Content-Disposition"] = storage.content_disposition(f"{name}.zip")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/folders/<int:item_id>/zip")
@login_required
def folder_zip(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "download", kind=FOLDER)
    activity.log(s, g.user, access.item, "zipped", utcnow())
    s.commit()
    return _zip_response(s, access.item)


# ---------- The same for shared links (no account needed) ----------

def _link_access(s, token, item_id):
    link = links.find(s, token)
    if not links.is_open(link) or not links.is_unlocked(link) or link.role != links.VIEWER:
        abort(404)
    path = links.path_inside(s, link, item_id)
    if path is None:
        abort(404)
    return link, path[-1]


@bp.get("/s/<token>/p/<int:item_id>")
def link_preview(token, item_id):
    s = db.session()
    _link, item = _link_access(s, token, item_id)
    if item.kind != FILE:
        abort(404)
    version = files.current_version(s, item)
    link = previews.inline_link(version, item) if version else None
    if link is None:
        abort(404)
    response = _framable(redirect(link))
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("/s/<token>/t/<int:item_id>")
def link_thumb(token, item_id):
    s = db.session()
    _link, item = _link_access(s, token, item_id)
    version = files.current_version(s, item) if item.kind == FILE else None
    blob = previews.thumbnail(s, version) if version else None
    if blob is None:
        abort(404)
    response = _framable(redirect(previews.thumb_link(blob)))
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("/s/<token>/z/<int:item_id>")
def link_zip(token, item_id):
    s = db.session()
    link, item = _link_access(s, token, item_id)
    if item.kind != FOLDER:
        abort(404)
    if not link.allow_download:
        abort(403, "The owner turned off downloads for this link.")
    return _zip_response(s, item)


def init_app(app):
    app.jinja_env.globals.update(preview_kind=previews.preview_kind)
