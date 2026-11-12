"""The Share page (people and the link) and Shared With Me."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import db, links, permissions, sharing
from ..auth import login_required, not_for_demo
from ..dates import utcnow
from ..models import Share
from .drive import item_url

bp = Blueprint("share", __name__)


@bp.route("/items/<int:item_id>/share", methods=["GET", "POST"])
@login_required
def page(item_id):
    s = db.session()
    access = permissions.require(s, g.user, item_id, "share")
    item = access.item
    moment = utcnow()
    errors = {}
    form = {"emails": "", "role": permissions.VIEWER, "note": ""}
    if request.method == "POST":
        action = request.form.get("action", "")
        here = url_for("share.page", item_id=item.id)
        if action == "add":
            form = {"emails": request.form.get("emails", ""), "role": request.form.get("role", ""),
                    "note": request.form.get("note", "")}
            if g.user.is_demo and any(not email.endswith(".invalid") for email in sharing.split_emails(form["emails"])):
                errors["emails"] = "The demo can't email real people. Sign up to share for real."
            else:
                try:
                    added, updated = sharing.add_people(s, g.user, item, sharing.split_emails(form["emails"]),
                                                        form["role"], moment, form["note"])
                except sharing.Problem as problem:
                    s.rollback()
                    errors["emails"] = str(problem)
                else:
                    s.commit()
                    parts = []
                    if added:
                        parts.append(f"Shared with {added} {'person' if added == 1 else 'people'}.")
                    if updated:
                        parts.append(f"Changed {updated} {'role' if updated == 1 else 'roles'}.")
                    flash(" ".join(parts) or "They already have that role.")
                    return redirect(here)
        elif action in ("role", "remove", "transfer"):
            share = s.get(Share, request.form.get("share_id", type=int) or 0)
            if share is None or share.item_id != item.id:
                abort(404)
            name = share.user.name if share.user else share.email
            if action == "role":
                try:
                    sharing.change_role(share, request.form.get("role", ""))
                except sharing.Problem as problem:
                    flash(str(problem), "error")
                    return redirect(here)
                s.commit()
                flash(f"{name} is now {permissions.ROLE_NAMES[share.role]}.")
            elif action == "remove":
                s.delete(share)
                s.commit()
                flash(f"Removed {name}.")
            else:
                permissions.require(s, g.user, item.id, "transfer")
                not_for_demo()
                try:
                    sharing.transfer(s, g.user, item, share, moment)
                except Exception as problem:  # sharing.Problem or files.Problem: both say what to do
                    s.rollback()
                    flash(str(problem), "error")
                    return redirect(here)
                s.commit()
                flash(f"{name} owns {item.name} now. You are a Co-owner.")
                return redirect(url_for("share.shared_with_me"))
            return redirect(here)
        elif action == "link_on":
            links.turn_on(s, g.user, item, moment)
            s.commit()
            flash("Anyone with the link can open it now.")
            return redirect(here)
        elif action == "link_off":
            link = links.for_item(s, item)
            if link is not None:
                links.turn_off(link, moment)
                s.commit()
            flash("The link is off. Only the people listed can open it.")
            return redirect(here)
        elif action == "link_new":
            link = links.for_item(s, item)
            if link is not None:
                links.renew(link)
                s.commit()
            flash("Made a new link. The old one doesn't work any more.")
            return redirect(here)
        else:
            abort(400, "Choose something to do.")
    link = links.for_item(s, item)
    return render_template("share.html", access=access, item=item, path=access.path,
                           shares=sharing.shares_on(s, item), inherited=sharing.inherited_shares(s, access.path),
                           link=link, link_address=links.address(link) if link else None, errors=errors, form=form,
                           ROLE_CHOICES=[(role, permissions.ROLE_NAMES[role]) for role in permissions.SHARE_ROLES]), \
        400 if errors else 200


@bp.post("/items/<int:item_id>/leave")
@login_required
def leave(item_id):
    """Take yourself off something shared with you."""
    s = db.session()
    share = s.scalar(select(Share).where(Share.item_id == item_id, Share.user_id == g.user.id))
    if share is None:
        abort(404)
    name = share.item.name
    s.delete(share)
    s.commit()
    flash(f"You left {name}.")
    return redirect(url_for("share.shared_with_me"))


@bp.get("/shared")
@login_required
def shared_with_me():
    s = db.session()
    rows = sharing.shared_with_me(s, g.user)
    return render_template("shared.html", rows=rows, menu_here="shared")


def init_app(app):
    app.jinja_env.globals.update(item_url=item_url)
