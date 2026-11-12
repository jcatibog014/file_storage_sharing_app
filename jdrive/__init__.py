"""J: Drive, a place to upload, organise, download and share files.
"Your files, on the J side."

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Python doesn't know this one, and browsers want the right type for the app manifest
# (the name and icons used when someone adds J: Drive to a home screen).
mimetypes.add_type("application/manifest+json", ".webmanifest")

APP_NAME = "J: Drive"
TAGLINE = "Your files, on the J side."
DEV_SECRET_KEY = "dev-only-not-secret"


def content_security_policy(bucket_origin=None):
    """Content-Security-Policy: what the browser may load and talk to.

    Scripts, styles and fonts come only from this site, so even if someone
    sneaked a <script> into a file name, the browser would refuse to run it.
    The bucket's own address (Backblaze B2 on the live site) is allowed for
    the things that really come from it: the upload itself (connect-src) and
    previews (pictures, video, PDF).
    """
    extra = f" {bucket_origin}" if bucket_origin else ""
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        f"img-src 'self' data:{extra}",
        f"media-src 'self'{extra}",
        f"frame-src 'self'{extra}",  # PDF previews
        f"connect-src 'self'{extra}",
        "worker-src 'self'",  # the service worker, /sw.js
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie, the local bucket's links and the email codes.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "jdrive.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        ON_RENDER=on_render,
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # The biggest thing Flask itself takes: one file in the no-JavaScript upload
        # form, or a PUT to the local bucket. (Live, files go straight to B2.)
        MAX_CONTENT_LENGTH=110 * 1000 * 1000,
        # Email (mailer.py): Brevo on the live site, or Gmail with an app password.
        BREVO_API_KEY=os.environ.get("BREVO_API_KEY"),
        MAIL_FROM=os.environ.get("MAIL_FROM"),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        # The bucket (storage.py). Without S3_BUCKET, files go to instance/bucket/.
        S3_ENDPOINT=os.environ.get("S3_ENDPOINT"),  # e.g. https://s3.us-west-004.backblazeb2.com
        S3_REGION=os.environ.get("S3_REGION"),  # e.g. us-west-004
        S3_BUCKET=os.environ.get("S3_BUCKET"),
        S3_KEY_ID=os.environ.get("S3_KEY_ID"),
        S3_SECRET=os.environ.get("S3_SECRET"),
        BUCKET_DIR=str(Path(app.instance_path) / "bucket"),
        # The secret the GitHub Actions timer sends to POST /tasks/tick (tick.py).
        TICK_KEY=os.environ.get("TICK_KEY"),
        BASE_URL=os.environ.get("BASE_URL"),
    )
    if test_config:
        app.config.update(test_config)
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us. This makes Flask
        # believe the X-Forwarded-* headers: links start with https://, and the rate
        # limits see the visitor's address instead of the proxy's.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, db, formatting
    from .views import drive

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, drive.bp):
        app.register_blueprint(blueprint)

    app.jinja_env.globals.update(APP_NAME=APP_NAME, TAGLINE=TAGLINE, has_demo="auth.demo" in app.view_functions)
    policy = content_security_policy()

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/sw.js")
    def service_worker():
        # From the top of the site, not /static/: a service worker only looks after
        # the pages below the address it came from.
        response = app.send_static_file("sw.js")
        response.headers["Cache-Control"] = "no-cache"  # always check for a new version
        return response

    @app.get("/favicon.ico")
    def favicon():
        # Some browsers ask for it at the root, whatever the page says.
        return app.send_static_file("icons/favicon.ico")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", policy)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # trust our file types
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")  # no showing us inside other sites
        if g.get("user") is not None:
            # Pages with someone's files on them are never kept by the browser or a proxy.
            response.headers.setdefault("Cache-Control", "no-store")
        if on_render:
            # Always use HTTPS for this site from now on (for a year).
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We couldn't find that. It may have been moved or deleted, "
                               "or it hasn't been shared with you.")

    @app.errorhandler(409)
    def conflict(error):
        return error_page(409, error.description or "That can't be done right now.")

    @app.errorhandler(413)
    def too_big(error):
        return error_page(413, "That file is too big. Files can be at most 100 MB.")

    @app.errorhandler(429)
    def too_many(error):
        return error_page(429, error.description or "Too many tries. Please wait a little and try again.")

    @app.errorhandler(503)
    def unavailable(error):
        return error_page(503, error.description or "Please try again in a minute.")

    return app
