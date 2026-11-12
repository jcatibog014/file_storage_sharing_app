"""My Drive: your own files and folders."""
from flask import Blueprint, g, redirect, render_template, url_for

from ..auth import login_required

bp = Blueprint("drive", __name__)


@bp.get("/")
def index():
    return redirect(url_for("drive.home") if g.user else url_for("auth.login"))


@bp.get("/drive")
@login_required
def home():
    return render_template("drive.html")
