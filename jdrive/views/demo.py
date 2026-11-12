"""Try Demo: one click, and the visitor has their own sample drive for 24 hours."""
from flask import Blueprint, flash, g, redirect, url_for

from .. import db, seed
from ..auth import log_in
from ..dates import utcnow

bp = Blueprint("demo", __name__)


@bp.post("/demo")
def start():
    """A fresh sample drive for this visitor alone. Nobody sees anyone else's demo."""
    if g.user is not None and g.user.is_demo:
        return redirect(url_for("drive.home"))  # this browser already has one
    s = db.session()
    moment = utcnow()
    seed.purge(s, moment)
    s.commit()
    if seed.count(s) >= seed.MAX_DEMOS:
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    me = seed.build(s, moment)
    s.commit()
    log_in(me, moment)
    # Shown once, as a toast (_toasts.html); the small Demo tag by the logo stays.
    flash("You are Janelle Demo. Upload, share and look around: this sample drive is deleted after 24 hours.",
          "demo")
    return redirect(url_for("drive.home"))
