"""The Activity page (your files' history) and Notifications (the bell)."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from .. import activity, db, notify
from ..auth import login_required
from ..dates import utcnow

bp = Blueprint("activity_views", __name__)

FILTERS = [("", "Everything"), ("uploaded", "Uploads"), ("downloaded", "Downloads"), ("shared", "Shares"),
           ("link_opened", "Link visits"), ("trashed", "Trash"), ("renamed", "Renames"), ("moved", "Moves")]


@bp.get("/activity")
@login_required
def page():
    verb = request.args.get("only", "")
    if verb not in dict(FILTERS):
        verb = ""
    s = db.session()
    rows = activity.for_owner(s, g.user, verb=verb or None)
    return render_template("activity.html", rows=rows, verb=verb, FILTERS=FILTERS, menu_here="activity")


@bp.get("/notifications")
@login_required
def notifications():
    s = db.session()
    rows = notify.latest(s, g.user)
    unread = {row.id for row in rows if row.read_at is None}
    notify.mark_all_read(s, g.user, utcnow())
    s.commit()
    return render_template("notifications.html", rows=rows, unread=unread, menu_here="notifications")


@bp.post("/notifications/read")
@login_required
def read_all():
    s = db.session()
    notify.mark_all_read(s, g.user, utcnow())
    s.commit()
    return redirect(url_for("activity_views.notifications"))
