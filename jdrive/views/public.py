"""Shared links: /s/<token>, open to anyone who has the address, logged in or not.

Every request checks, in this order:
1. the token is right, and the link is on and hasn't run out (else 404);
2. the password, if the link has one (a small form until it is typed in);
3. what the link may do: a "Can view" link opens the linked item and what is
   inside it (downloads only if allowed); a "Can upload" link only takes new
   files into its folder and never shows what is there.
Anything else is a 404.
"""
from flask import (Blueprint, abort, flash, g, jsonify, make_response, redirect, render_template, request, session,
                   url_for)

from .. import db, files, limits, links, permissions, storage
from ..dates import utcnow
from ..models import FILE, Upload

bp = Blueprint("public", __name__)
UPLOADS_PER_HOUR = 100   # per network, through upload links
SENT_LIST_LENGTH = 20    # "You sent:" remembers this many names in the browser's session


def _private(response, status=None):
    """The address holds a secret: no caching, no passing it on, no search engines."""
    response = make_response(response) if status is None else make_response(response, status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _link_or_404(s, token):
    link = links.find(s, token)
    if not links.is_open(link):
        abort(404)
    return link


def _locked(link, token):
    """The password form, if the link has a password this browser hasn't typed in yet."""
    if links.is_unlocked(link):
        return None
    return _private(render_template("public_password.html", link=link, token=token, error=None), 401)


def _viewer_path(s, link, item_id):
    """The path to an item inside a "Can view" link's tree, or 404."""
    if link.role != links.VIEWER:
        abort(404)
    path = links.path_inside(s, link, item_id)
    if path is None:
        abort(404)
    return path


def _show(s, link, token, path):
    item = path[-1]
    own_access = permissions.check(s, g.user, item.id) if g.user else None
    if item.is_folder:
        children = files.children(s, item.owner_id, item.id)
        return _private(render_template("public_folder.html", link=link, token=token, item=item, path=path,
                                        items=children, own_access=own_access))
    version = files.current_version(s, item)
    return _private(render_template("public_file.html", link=link, token=token, item=item, path=path,
                                    version=version, own_access=own_access))


@bp.get("/s/<token>")
def link(token):
    s = db.session()
    link = _link_or_404(s, token)
    path = links.path_inside(s, link, link.item_id)
    if path is None:
        abort(404)  # in the Trash
    link.open_count += 1
    s.commit()
    locked = _locked(link, token)
    if locked:
        return locked
    if link.role == links.UPLOADER:
        sent = session.get("link_sent", {}).get(str(link.id), [])
        return _private(render_template("public_upload.html", link=link, token=token, item=link.item, sent=sent))
    return _show(s, link, token, path)


@bp.post("/s/<token>/unlock")
def unlock(token):
    s = db.session()
    link = _link_or_404(s, token)
    try:
        right = links.try_password(s, link, request.form.get("password", ""), utcnow())
        error = None if right else "That password isn't right."
    except links.Problem as problem:
        right, error = False, str(problem)
    s.commit()
    if right:
        return redirect(url_for("public.link", token=token))
    return _private(render_template("public_password.html", link=link, token=token, error=error), 401)


@bp.get("/s/<token>/i/<int:item_id>")
def inside(token, item_id):
    s = db.session()
    link = _link_or_404(s, token)
    locked = _locked(link, token)
    if locked:
        return locked
    return _show(s, link, token, _viewer_path(s, link, item_id))


@bp.get("/s/<token>/d/<int:item_id>")
def download(token, item_id):
    s = db.session()
    link = _link_or_404(s, token)
    if _locked(link, token):
        abort(404)
    path = _viewer_path(s, link, item_id)
    if path[-1].kind != FILE:
        abort(404)
    if not link.allow_download:
        abort(403, "The owner turned off downloads for this link.")
    item = path[-1]
    version = files.current_version(s, item)
    if version is None:
        abort(404)
    return _private(redirect(storage.get().presign_get(version.blob.key, item.name, version.blob.content_type)))


# ---------- "Can upload" links: the same three steps as a logged-in upload ----------

def _upload_link(s, token):
    link = _link_or_404(s, token)
    if link.role != links.UPLOADER or not links.is_unlocked(link) or links.path_inside(s, link, link.item_id) is None:
        abort(404)
    return link


def _json_problem(message, status=400):
    return jsonify({"ok": False, "error": message}), status


@bp.post("/s/<token>/api/uploads")
def api_start_upload(token):
    s = db.session()
    link = _upload_link(s, token)
    moment = utcnow()
    if not limits.hit(s, f"link-upload:{limits.client_ip()}", UPLOADS_PER_HOUR, 3600, moment):
        s.commit()
        return _json_problem("That's a lot of uploads for one hour. Please wait a while.", 429)
    data = request.get_json(silent=True) or {}
    folder = link.item
    try:
        upload, signed = files.start_upload(s, None, folder.owner_id, folder, data.get("name", ""),
                                            int(data.get("size", -1)), data.get("type", ""), moment, link=link)
    except (TypeError, ValueError):
        return _json_problem("That file's size isn't right.")
    except files.Problem as problem:
        s.rollback()
        return _json_problem(str(problem))
    s.commit()
    # Only this browser may finish this upload.
    session["link_uploads"] = (session.get("link_uploads", []) + [upload.id])[-50:]
    return jsonify({"ok": True, "upload_id": upload.id, **signed})


@bp.post("/s/<token>/api/uploads/<int:upload_id>/done")
def api_finish_upload(token, upload_id):
    s = db.session()
    link = _upload_link(s, token)
    upload = s.get(Upload, upload_id)
    if (upload is None or upload.link_id != link.id or upload_id not in session.get("link_uploads", [])
            or upload.expires_at <= utcnow()):
        return _json_problem("That upload isn't here any more. Please try again.", 404)
    blob_id = upload.blob_id
    try:
        item = files.finish_upload(s, None, upload, utcnow())
    except files.Problem as problem:
        s.rollback()
        s.delete(s.get(Upload, upload_id))
        s.commit()
        files.sweep_blobs(s, [blob_id])
        return _json_problem(str(problem))
    s.commit()
    sent = dict(session.get("link_sent", {}))
    sent[str(link.id)] = (sent.get(str(link.id), []) + [item.name])[-SENT_LIST_LENGTH:]
    session["link_sent"] = sent
    session["link_uploads"] = [number for number in session.get("link_uploads", []) if number != upload_id]
    return jsonify({"ok": True})


@bp.post("/s/<token>/upload")
def upload_form(token):
    """The no-JavaScript way for an upload link."""
    s = db.session()
    link = _upload_link(s, token)
    moment = utcnow()
    chosen = [f for f in request.files.getlist("files") if f and f.filename]
    if not chosen:
        flash("Choose a file to send first.", "error")
        return redirect(url_for("public.link", token=token))
    names = []
    for file_storage in chosen:
        if not limits.hit(s, f"link-upload:{limits.client_ip()}", UPLOADS_PER_HOUR, 3600, moment):
            flash("That's a lot of uploads for one hour. Please wait a while.", "error")
            break
        try:
            names.append(files.upload_through_server(s, None, link.item.owner_id, link.item, file_storage, moment).name)
            s.commit()
        except files.Problem as problem:
            s.rollback()
            flash(f"{file_storage.filename}: {problem}", "error")
    if names:
        sent = dict(session.get("link_sent", {}))
        sent[str(link.id)] = (sent.get(str(link.id), []) + names)[-SENT_LIST_LENGTH:]
        session["link_sent"] = sent
        flash(f"Sent {names[0]}." if len(names) == 1 else f"Sent {len(names)} files.")
    return redirect(url_for("public.link", token=token))

