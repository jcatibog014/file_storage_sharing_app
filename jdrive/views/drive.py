"""My Drive: folders and files, uploading, downloading, renaming, moving, deleting.

Every page and form about an item goes through permissions.require(), which
gives a 404 for anything the person can't see.
"""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from .. import db, files, permissions, storage
from ..auth import login_required
from ..dates import utcnow
from ..models import FILE, FOLDER, Item, Upload

bp = Blueprint("drive", __name__)


def item_url(item):
    if item is None:
        return url_for("drive.home")
    return url_for("drive.folder", item_id=item.id) if item.is_folder else url_for("drive.file", item_id=item.id)


def parent_url(item):
    return url_for("drive.folder", item_id=item.parent_id) if item.parent_id else url_for("drive.home")


def back_to(default):
    """Back to the page the form came from, if it was on this site."""
    from ..auth import safe_next
    return redirect(safe_next(request.form.get("next") or "", default))


def place(s, parent_id, action):
    """Where something new goes: (owner_id, parent folder or None). None = the
    top of your own My Drive; a folder needs the right role on it."""
    if parent_id in (None, "", "root"):
        return g.user.id, None
    try:
        parent_id = int(parent_id)
    except (TypeError, ValueError):
        abort(404)
    access = permissions.require(db.session(), g.user, parent_id, action, kind=FOLDER)
    return access.item.owner_id, access.item


def view_mode():
    """List or grid, remembered for this login."""
    wanted = request.args.get("view")
    if wanted in ("list", "grid"):
        session["view"] = wanted
    return session.get("view", "list")


def sort_choice():
    sort = request.args.get("sort", "name")
    if sort not in files.SORTS:
        sort = "name"
    return sort, request.args.get("order") == "desc"


@bp.get("/")
def index():
    return redirect(url_for("drive.home") if g.user else url_for("auth.login"))


@bp.get("/drive")
@login_required
def home():
    s = db.session()
    sort, descending = sort_choice()
    items = files.children(s, g.user.id, None, sort, descending)
    return render_template("folder.html", access=None, folder=None, path=[], items=items, sort=sort,
                           descending=descending, view=view_mode(), can_add=True, menu_here="my-drive")


@bp.get("/folders/<int:item_id>")
@login_required
def folder(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "view", kind=FOLDER)
    sort, descending = sort_choice()
    items = files.children(s, access.item.owner_id, access.item.id, sort, descending)
    return render_template("folder.html", access=access, folder=access.item, path=access.path, items=items,
                           sort=sort, descending=descending, view=view_mode(), can_add=access.can("upload"),
                           menu_here="my-drive" if access.item.owner_id == g.user.id else "shared")


@bp.get("/files/<int:item_id>")
@login_required
def file(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "view", kind=FILE)
    version = files.current_version(s, access.item)
    return render_template("file.html", access=access, item=access.item, path=access.path, version=version,
                           menu_here="my-drive" if access.item.owner_id == g.user.id else "shared")


@bp.get("/files/<int:item_id>/download")
@login_required
def download(item_id):
    """Check, then send the browser to a signed link that works for 60 seconds.
    The bucket address is never in the page: each click asks again."""
    s = db.session()
    access = permissions.require(s, g.user, item_id, "download", kind=FILE)
    version = files.current_version(s, access.item)
    if version is None:
        abort(404)
    url = storage.get().presign_get(version.blob.key, access.item.name, version.blob.content_type)
    return redirect(url)


@bp.post("/folders/new")
@login_required
def new_folder():
    s = db.session()
    owner_id, parent = place(s, request.form.get("parent_id"), "new_folder")
    try:
        folder = files.create_folder(s, g.user, owner_id, parent, request.form.get("name", ""), utcnow())
    except files.Problem as problem:
        flash(str(problem), "error")
        return redirect(item_url(parent))
    s.commit()
    flash(f"Made the folder {folder.name}.")
    return redirect(item_url(parent))


@bp.post("/upload")
@login_required
def upload_form():
    """The no-JavaScript upload: files come through the server."""
    s = db.session()
    owner_id, parent = place(s, request.form.get("parent_id"), "upload")
    chosen = [f for f in request.files.getlist("files") if f and f.filename]
    if not chosen:
        flash("Choose a file to upload first.", "error")
        return redirect(item_url(parent))
    done = []
    for file_storage in chosen:
        try:
            done.append(files.upload_through_server(s, g.user, owner_id, parent, file_storage, utcnow()))
            s.commit()
        except files.Problem as problem:
            s.rollback()
            flash(f"{file_storage.filename}: {problem}", "error")
    if done:
        flash(f"Uploaded {done[0].name}." if len(done) == 1 else f"Uploaded {len(done)} files.")
    return redirect(item_url(parent))


@bp.route("/items/<int:item_id>/rename", methods=["GET", "POST"])
@login_required
def rename(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "rename")
    error = None
    if request.method == "POST":
        try:
            files.rename(s, access.item, request.form.get("name", ""), utcnow())
        except files.Problem as problem:
            error = str(problem)
        else:
            s.commit()
            flash(f"Renamed to {access.item.name}.")
            return redirect(parent_url(access.item))
    return render_template("rename.html", access=access, item=access.item, error=error), 400 if error else 200


@bp.route("/items/<int:item_id>/move", methods=["GET", "POST"])
@login_required
def move(item_id):
    """Pick a folder (in the same owner's drive), then Move here."""
    s = db.session()
    access = permissions.require(s, g.user, item_id, "move")
    item = access.item
    if request.method == "POST":
        target = None
        if request.form.get("target_id"):
            target = permissions.require(s, g.user, int(request.form["target_id"]), "upload", kind=FOLDER).item
        elif item.owner_id != g.user.id:
            abort(403, "Only the owner can move things to the top of their My Drive.")
        try:
            files.move(s, item, target, utcnow())
        except files.Problem as problem:
            flash(str(problem), "error")
            return redirect(url_for("drive.move", item_id=item.id, at=target.id if target else None))
        s.commit()
        flash(f"Moved {item.name} to {target.name if target else 'My Drive'}.")
        return redirect(item_url(target))
    # Browsing: start in the folder the item is in.
    at_id = request.args.get("at", type=int)  # 0 = the top of My Drive
    if at_id is None:
        at_id = item.parent_id
    at_id = at_id or None
    at_access = permissions.check(s, g.user, at_id, "upload") if at_id else None
    if at_access is not None and (at_access.item.owner_id != item.owner_id or not at_access.item.is_folder):
        at_access = None
    at = at_access.item if at_access else None
    if at is None and item.owner_id != g.user.id:
        # Someone else's drive: you can only move within the shared folders you can edit.
        at_access = permissions.check(s, g.user, item.parent_id, "upload") if item.parent_id else None
        at = at_access.item if at_access else None
    folders = [f for f in files.children(s, item.owner_id, at.id if at else None) if f.is_folder and f.id != item.id]
    problem = files.move_problem(s, item, at)
    return render_template("move.html", access=access, item=item, at=at, at_path=at_access.path if at_access else [],
                           folders=folders, problem=problem, can_go_top=item.owner_id == g.user.id)


@bp.post("/items/<int:item_id>/delete")
@login_required
def delete(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "delete_forever")
    item = access.item
    name, back = item.name, parent_url(item)
    blob_ids = files.delete_forever(s, item)
    s.commit()
    files.sweep_blobs(s, blob_ids)
    flash(f"Deleted {name}.")
    return redirect(back)


# ---------- The upload API (app.js) ----------

def json_problem(message, status=400):
    return jsonify({"ok": False, "error": message}), status


@bp.post("/api/uploads")
@login_required
def api_start_upload():
    """Step 1 of an upload: check, then hand out a signed PUT link."""
    s = db.session()
    data = request.get_json(silent=True) or {}
    target = None
    if data.get("target_id"):
        try:
            target_id = int(data["target_id"])
        except (TypeError, ValueError):
            abort(404)
        target = permissions.require(s, g.user, target_id, "new_version", kind=FILE).item
        owner_id, parent = target.owner_id, None
    else:
        owner_id, parent = place(s, data.get("parent_id"), "upload")
    try:
        size = int(data.get("size", -1))
        upload, link = files.start_upload(s, g.user, owner_id, parent, data.get("name", ""), size,
                                          data.get("type", ""), utcnow(), target=target)
    except (TypeError, ValueError):
        return json_problem("That file's size isn't right.")
    except files.Problem as problem:
        return json_problem(str(problem))
    s.commit()
    return jsonify({"ok": True, "upload_id": upload.id, **link})


@bp.post("/api/uploads/<int:upload_id>/done")
@login_required
def api_finish_upload(upload_id):
    """Step 3: the browser says the PUT worked. We check the bucket ourselves."""
    s = db.session()
    upload = s.get(Upload, upload_id)
    if upload is None or upload.user_id != g.user.id or upload.expires_at <= utcnow():
        return json_problem("That upload isn't here any more. Please try again.", 404)
    # The folder may have been deleted or unshared since step 1: ask again.
    if upload.target_item_id:
        permissions.require(s, g.user, upload.target_item_id, "new_version", kind=FILE)
    elif upload.parent_id:
        permissions.require(s, g.user, upload.parent_id, "upload", kind=FOLDER)
    elif upload.owner_id != g.user.id:
        abort(404)
    blob_id = upload.blob_id
    try:
        item = files.finish_upload(s, g.user, upload, utcnow())
    except files.Problem as problem:
        s.rollback()
        s.delete(s.get(Upload, upload_id))
        s.commit()
        files.sweep_blobs(s, [blob_id])
        return json_problem(str(problem))
    s.commit()
    return jsonify({"ok": True, "item": {"id": item.id, "name": item.name, "url": item_url(item)}})


def init_app(app):
    app.jinja_env.globals.update(item_url=item_url, parent_url=parent_url)
