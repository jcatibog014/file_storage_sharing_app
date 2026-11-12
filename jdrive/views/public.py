"""Shared links: /s/<token>, open to anyone who has the address, logged in or not.

The token is checked on every request, and every item asked for must be
inside the linked item's tree (links.path_inside). Anything else is a 404.
"""
from flask import Blueprint, abort, g, make_response, redirect, render_template

from .. import db, files, links, permissions, storage
from ..models import FILE

bp = Blueprint("public", __name__)


def _link_or_404(s, token):
    link = links.find(s, token)
    if not links.is_open(link):
        abort(404)
    return link


def _private(response):
    """The address holds a secret: no caching, no passing it on, no search engines."""
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


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
    return _show(s, link, token, path)


@bp.get("/s/<token>/i/<int:item_id>")
def inside(token, item_id):
    s = db.session()
    link = _link_or_404(s, token)
    path = links.path_inside(s, link, item_id)
    if path is None:
        abort(404)
    return _show(s, link, token, path)


@bp.get("/s/<token>/d/<int:item_id>")
def download(token, item_id):
    s = db.session()
    link = _link_or_404(s, token)
    path = links.path_inside(s, link, item_id)
    if path is None or path[-1].kind != FILE:
        abort(404)
    item = path[-1]
    version = files.current_version(s, item)
    if version is None:
        abort(404)
    return _private(redirect(storage.get().presign_get(version.blob.key, item.name, version.blob.content_type)))
