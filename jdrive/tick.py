"""The timer: housekeeping that runs every few minutes.

- uploads that were started but never finished run out (after an hour);
- the Trash deletes what has been there for 30 days;
- objects nothing points at any more are deleted from the bucket;
- sign-ups that never typed their code are deleted after a day (so a
  mistyped or someone else's email isn't held forever);
- old rate-limit counters are cleared.

It runs from GitHub Actions (.github/workflows/tick.yml): a POST to
/tasks/tick with the secret X-Tick-Key header (any other request gets a 404,
so nobody can find it), and from the command line: flask --app jdrive tick.
"""
import hmac
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, jsonify, request
from flask.cli import with_appcontext
from sqlalchemy import delete

from . import db, devices, files, limits
from .dates import utcnow
from .models import User

bp = Blueprint("tick", __name__)
UNVERIFIED_LIFETIME = timedelta(days=1)
# Other modules add jobs (stage 8: deleting run-out demos). Each is (name, function(s, moment) -> count).
jobs = []


def run(s, moment):
    done = {}
    done["uploads_expired"] = files.expire_uploads(s, moment)
    blob_ids = files.purge_trash(s, moment)
    s.commit()
    done["trash_deleted"] = len(blob_ids)
    files.sweep_blobs(s, blob_ids)
    for name, job in jobs:
        done[name] = job(s, moment)
        s.commit()
    done["unconfirmed_deleted"] = s.execute(delete(User).where(
        User.email_verified_at.is_(None), User.is_demo.is_(False),
        User.created_at < moment - UNVERIFIED_LIFETIME)).rowcount
    done["limits_cleared"] = limits.purge(s, moment)
    done["old_logins_forgotten"] = devices.purge(s, moment)
    s.commit()
    done["objects_swept"] = files.sweep_blobs(s)  # anything left over (old versions, gone demos)
    return done


@bp.post("/tasks/tick")
def tick():
    key = current_app.config.get("TICK_KEY")
    sent = request.headers.get("X-Tick-Key", "")
    if not key or not hmac.compare_digest(sent, key):
        abort(404)
    return jsonify(run(db.session(), utcnow()))


@click.command("tick")
@with_appcontext
def tick_command():
    """Run the housekeeping once."""
    for name, count in run(db.session(), utcnow()).items():
        click.echo(f"{name}: {count}")


def init_app(app):
    exempt = set(app.config.get("CSRF_EXEMPT", ()))
    exempt.add("tick.tick")  # a machine calls it, with its own secret instead of a form token
    app.config["CSRF_EXEMPT"] = exempt
    app.cli.add_command(tick_command)
