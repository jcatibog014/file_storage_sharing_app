"""Signing up, proving the email is real, logging in and out, and the
protection every form gets.

Sign-up takes a name, an email and a password, then emails a 6-digit code
(codes.py). The account works only after the code is typed in. Until then
nobody is logged in, and shares waiting for that email stay waiting. So
signing up with someone else's address gets you nothing.

Passwords are stored only as hashes. The login cookie holds the user's id and
their session_version; load_user() checks both on every request, so an old
cookie (after a password change, or "Log out other devices") stops working
straight away.
"""
import functools
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from . import codes, db, devices, limits, mailer, twostep
from .dates import utcnow
from .models import User

bp = Blueprint("auth", __name__)

NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 8
# Good enough to catch typos. The emailed code is the real check.
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# After 5 wrong passwords in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
# And one address may try at most this many times in 15 minutes, whatever the account.
LOGINS_PER_IP = 30

WRONG_LOGIN = "Wrong email or password."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."
TOO_MANY = "Too many tries from your network. Please wait 15 minutes and try again."
# Hooks other modules add (sharing.py hands over waiting shares) when an email is proven.
on_verified = []


# ---------- Checking what people type ----------

def clean_email(text):
    return (text or "").strip().lower()[:EMAIL_MAX_LENGTH + 1]


def email_problem(email):
    if not email:
        return "Enter your email."
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        return "That doesn't look like an email address."
    if email.endswith(".invalid"):
        return "Use a real email address: we send a code to it."
    return None


def name_problem(name):
    if not name:
        return "Enter your name."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


# ---------- Users in the database ----------

def find_user(s, email):
    return s.scalar(select(User).where(User.email == clean_email(email)))


def create_user(s, name, email, password, verified_at=None, **fields):
    """Add a user. The caller commits."""
    user = User(name=name, email=clean_email(email), password_hash=generate_password_hash(password),
                email_verified_at=verified_at, **fields)
    s.add(user)
    s.flush()
    return user


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out


def _failed(s, user, moment):
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
    s.commit()


def check_login(s, email, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the email has an account."""
    if not limits.hit(s, f"login-ip:{limits.client_ip()}", LOGINS_PER_IP, 900, moment):
        s.commit()
        return None, TOO_MANY
    user = find_user(s, email)
    if user is None or user.is_demo:
        # Same work as a real check, so the answer takes as long either way.
        check_password_hash(_dummy_hash(), password or "")
        s.commit()
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        s.commit()
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        _failed(s, user, moment)
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


@functools.cache
def _dummy_hash():
    return generate_password_hash(secrets.token_urlsafe(16))


def safe_next(target, default=None):
    """Only go back to a page on this site after logging in.

    "/folders/7" is fine. "https://evil.example" and "//evil.example" are not:
    a fake link could send people there after a real login. Browsers drop tabs
    and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("drive.home")


def log_in(user, moment=None):
    """Start a login on this device (a LoginSession row, devices.py) and say so in the cookie."""
    s = db.session()
    moment = moment or utcnow()
    device = devices.describe(request.headers.get("User-Agent", ""))
    new_device = not user.is_demo and devices.has_any(s, user) and not devices.seen_before(s, user, device)
    session_id, secret = devices.start(s, user, device, limits.client_ip(), moment)
    s.commit()
    if new_device:
        mailer.send(user.email, "New login to your J: Drive",
                    f"Hi {user.first_name},\n\nSomeone just logged in to your J: Drive from {device}. "
                    f"If that was you, all is well.\n\nIf it wasn't, change your password now and choose "
                    f"\"Log out other devices\" in Settings: {url_for('auth.settings', _external=True)}\n\nJ: Drive")
    csrf = session.get("csrf")
    session.clear()  # a new session for the new user (old cookies can't be reused)
    if csrf and user.is_demo:
        session["csrf"] = csrf  # demo pages that are open keep working
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version
    session["sid"], session["ss"] = session_id, secret


def mark_verified(s, user, moment):
    """The emailed code was right: the email is real and theirs."""
    user.email_verified_at = moment
    for hook in on_verified:
        hook(s, user, moment)


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        s = db.session()
        user = s.get(User, user_id)
        if (user is None or session.get("session_version") != user.session_version or not user.is_verified
                or (user.is_demo and user.demo_expires_at and user.demo_expires_at <= utcnow())
                or not devices.check(s, user, session.get("sid"), session.get("ss"), utcnow())):
            # Keep the CSRF token: only the login part of this cookie is out of date.
            for key in ("user_id", "session_version", "sid", "ss"):
                session.pop(key, None)
        else:
            g.user = user


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method not in ("POST", "DELETE") or request.endpoint in current_app.config.get("CSRF_EXEMPT", ()):
        return
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def not_for_demo(message="The demo can't change this. Sign up to make it your own."):
    if g.user is not None and g.user.is_demo:
        abort(403, message)


# ---------- Pages ----------

def _start_verifying(user, next_page=""):
    """The Enter Your Code page is about this person (they aren't logged in yet)."""
    session["verify_id"] = user.id
    session["verify_next"] = safe_next(next_page) if next_page else ""


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("drive.home"))
    next_page = request.values.get("next", "")
    error = None
    email = ""
    if request.method == "POST":
        s = db.session()
        moment = utcnow()
        email = clean_email(request.form.get("email", ""))
        user, error = check_login(s, email, request.form.get("password", ""), moment)
        if user and not user.is_verified:
            # The right password, but the email was never proven: send a code first.
            problem = codes.send(s, user, codes.VERIFY, moment)
            s.commit()
            _start_verifying(user, next_page)
            flash(problem or f"Almost there. We sent a code to {user.email}.", "error" if problem else "message")
            return redirect(url_for("auth.verify"))
        if user:
            if twostep.is_on(user):
                return twostep.start(user, next_page)
            log_in(user)
            return redirect(safe_next(next_page))
    status = 401 if error else 200
    return render_template("login.html", error=error, email=email, next=next_page), status


@bp.route("/login/code", methods=["GET", "POST"])
def login_code():
    """Step two of logging in, for people with two-step sign-in on."""
    s = db.session()
    user = s.get(User, session["twostep_user"]) if session.get("twostep_user") else None
    if user is None or not twostep.is_on(user):
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        moment = utcnow()
        ok, error = twostep.try_login_code(s, user, request.form.get("code", ""), moment)
        s.commit()
        if ok:
            next_page = session.pop("twostep_next", "")
            log_in(user, moment)
            left = twostep.codes_left(s, user)
            if left <= 2:
                flash(f"You have {left} recovery {'code' if left == 1 else 'codes'} left. Make new ones in Settings.",
                      "error")
            return redirect(safe_next(next_page))
    return render_template("login_code.html", error=error), 401 if error else 200


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user is not None and g.user.is_demo:
        # "Sign up for real" from the demo: leave the demo first.
        session.pop("user_id", None)
        session.pop("session_version", None)
        g.user = None
    if g.user is not None:
        return redirect(url_for("drive.home"))
    next_page = request.values.get("next", "")
    errors = {}
    name = request.args.get("name", "")
    email = clean_email(request.args.get("email", ""))
    if request.method == "POST":
        # A hidden field people never see. Only robots fill it in, and they get
        # the same page as everyone else, so they learn nothing from it.
        if request.form.get("website"):
            return redirect(url_for("auth.login"))
        s = db.session()
        moment = utcnow()
        name = request.form.get("name", "").strip()
        email = clean_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        for key, problem in (("name", name_problem(name)), ("email", email_problem(email)),
                             ("password", password_problem(password, request.form.get("confirm_password", "")))):
            if problem:
                errors[key] = problem
        if not errors and not limits.hit(s, f"signup-ip:{limits.client_ip()}", 20, 3600, moment):
            errors["email"] = TOO_MANY
        if not errors:
            user = find_user(s, email)
            if user is not None and (user.is_verified or user.is_demo):
                errors["email"] = "That email already has an account. Log in instead."
            else:
                if user is None:
                    user = create_user(s, name, email, password)
                else:
                    # Someone signed up with this email before but never typed the code.
                    # It was never proven theirs, so the person who CAN read the inbox wins.
                    user.name = name
                    set_password(user, password)
                problem = codes.send(s, user, codes.VERIFY, moment)
                s.commit()
                _start_verifying(user, next_page)
                if problem:
                    flash(problem, "error")
                return redirect(url_for("auth.verify"))
        s.commit()
    return render_template("signup.html", errors=errors, name=name, email=email,
                           next=next_page), 400 if errors else 200


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Type the 6-digit code from the email. Right: the account is real and you are in."""
    s = db.session()
    user = s.get(User, session["verify_id"]) if session.get("verify_id") else None
    if user is None:
        return redirect(url_for("auth.signup"))
    if user.is_verified:
        session.pop("verify_id", None)
        return redirect(url_for("auth.login"))
    error = None
    moment = utcnow()
    if request.method == "POST":
        if request.form.get("action") == "resend":
            problem = codes.send(s, user, codes.VERIFY, moment)
            s.commit()
            flash(problem or f"We sent a new code to {user.email}.", "error" if problem else "message")
            return redirect(url_for("auth.verify"))
        if codes.check(s, user, codes.VERIFY, request.form.get("code", ""), moment):
            mark_verified(s, user, moment)
            s.commit()
            next_page = session.pop("verify_next", "")
            session.pop("verify_id", None)
            log_in(user)
            flash(f"Welcome to J: Drive, {user.first_name}! Your email is confirmed.")
            return redirect(safe_next(next_page))
        error = codes.problem_after_wrong(s, user, codes.VERIFY, moment)
        s.commit()
    return render_template("verify.html", person=user, error=error,
                           wait=codes.wait_seconds(s, user, codes.VERIFY, moment)), 400 if error else 200


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    """Step 1 of a new password: we email a code (only to a confirmed account,
    but the page says the same thing either way, so it tells nobody who has one)."""
    if g.user is not None:
        return redirect(url_for("auth.settings"))
    error = None
    email = ""
    if request.method == "POST":
        s = db.session()
        moment = utcnow()
        email = clean_email(request.form.get("email", ""))
        error = email_problem(email)
        if not error:
            if not limits.hit(s, f"forgot-ip:{limits.client_ip()}", 10, 3600, moment):
                error = TOO_MANY
            else:
                user = find_user(s, email)
                if user is not None and user.is_verified and not user.is_demo:
                    problem = codes.send(s, user, codes.RESET, moment)
                    if problem and "couldn't send" in problem:
                        error = problem
                s.commit()
                if not error:
                    session["reset_email"] = email
                    flash(f"If {email} has an account, a code is on its way. Check your spam folder too.")
                    return redirect(url_for("auth.reset"))
        s.commit()
    return render_template("forgot.html", error=error, email=email), 400 if error else 200


@bp.route("/reset", methods=["GET", "POST"])
def reset():
    """Step 2: the code from the email and a new password."""
    email = session.get("reset_email")
    if not email:
        return redirect(url_for("auth.forgot"))
    errors = {}
    if request.method == "POST":
        s = db.session()
        moment = utcnow()
        new = request.form.get("new_password", "")
        problem = password_problem(new, request.form.get("confirm_password", ""))
        if problem:
            errors["new_password"] = problem
        else:
            user = find_user(s, email)
            if user is not None and user.is_verified and codes.check(s, user, codes.RESET,
                                                                     request.form.get("code", ""), moment):
                set_password(user, new)
                s.commit()
                session.pop("reset_email", None)
                flash("Your password has been changed. Log in with the new one.")
                return redirect(url_for("auth.login"))
            errors["code"] = (codes.problem_after_wrong(s, user, codes.RESET, moment) if user is not None
                              else "That code isn't right.")
            s.commit()
    return render_template("reset.html", email=email, errors=errors), 400 if errors else 200


@bp.post("/logout")
def logout():
    if session.get("sid"):
        s = db.session()
        devices.end(s, session["sid"], utcnow())
        s.commit()
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("auth.login"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    errors = {}
    if request.method == "POST":
        not_for_demo("The demo can't change its account. Sign up to have your own.")
        action = request.form.get("action")
        s = db.session()
        if action == "name":
            name = request.form.get("name", "").strip()
            problem = name_problem(name)
            if problem:
                errors["name"] = problem
            else:
                g.user.name = name
                s.commit()
                flash("Your name has been changed.")
                return redirect(url_for("auth.settings"))
        elif action == "password":
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["password_current"] = "That isn't your current password."
            new = request.form.get("new_password", "")
            problem = password_problem(new, request.form.get("confirm_password", ""))
            if problem:
                errors["new_password"] = problem
            if not errors:
                set_password(g.user, new)
                devices.end_others(s, g.user, session.get("sid"), utcnow())
                s.commit()
                # This device stays logged in; every other one is logged out.
                session["session_version"] = g.user.session_version
                flash("Your password has been changed. Your other devices are logged out.")
                return redirect(url_for("auth.settings"))
        elif action == "logout_others":
            count = devices.end_others(s, g.user, session.get("sid"), utcnow())
            s.commit()
            flash(f"Logged out {count} other {'device' if count == 1 else 'devices'}." if count
                  else "You weren't logged in anywhere else.")
            return redirect(url_for("auth.settings"))
        elif action == "end_session":
            row_id = request.form.get("session_id", type=int)
            if row_id and row_id != session.get("sid") and any(row.id == row_id for row in devices.active(s, g.user)):
                devices.end(s, row_id, utcnow())
                s.commit()
                flash("Logged out that device.")
            return redirect(url_for("auth.settings"))
        elif action == "twostep_off":
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["twostep_password"] = "That isn't your current password."
            else:
                twostep.turn_off(s, g.user)
                s.commit()
                flash("Two-step sign-in is off.")
                return redirect(url_for("auth.settings"))
        elif action == "recovery_codes":
            if not twostep.is_on(g.user):
                abort(400, "Turn on two-step sign-in first.")
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["recovery_password"] = "That isn't your current password."
            else:
                session["show_codes"] = twostep.new_recovery_codes(s, g.user)
                s.commit()
                return redirect(url_for("auth.recovery_codes"))
        else:
            abort(400, "Choose something to change.")
    s = db.session()
    return render_template("settings.html", errors=errors, mail_on=mailer.enabled(),
                           sessions=devices.active(s, g.user), this_session=session.get("sid"),
                           twostep_on=twostep.is_on(g.user),
                           codes_left=twostep.codes_left(s, g.user) if twostep.is_on(g.user) else 0), \
        400 if errors else 200


@bp.route("/settings/two-step", methods=["GET", "POST"])
@login_required
def twostep_setup():
    """Turn on two-step sign-in: scan the QR code, then type a code to prove the app has it."""
    not_for_demo("The demo can't turn on two-step sign-in. Sign up to try it for real.")
    if twostep.is_on(g.user):
        return redirect(url_for("auth.settings"))
    s = db.session()
    secret = session.get("totp_pending")
    if not secret or request.method == "GET":
        secret = session["totp_pending"] = twostep.new_secret()
    error = None
    if request.method == "POST":
        g.user.totp_secret = secret
        if twostep.check_code(s, g.user, request.form.get("code", ""), utcnow()):
            g.user.totp_enabled_at = utcnow()
            session.pop("totp_pending", None)
            session["show_codes"] = twostep.new_recovery_codes(s, g.user)
            s.commit()
            flash("Two-step sign-in is on.")
            return redirect(url_for("auth.recovery_codes"))
        s.rollback()
        error = "That code isn't right. Type the 6 digits your app shows now."
    return render_template("twostep_setup.html", secret=secret, qr=twostep.qr_svg(g.user, secret), error=error), \
        400 if error else 200


@bp.get("/settings/recovery-codes")
@login_required
def recovery_codes():
    """Shown once, straight after they are made. A reload shows nothing."""
    codes_now = session.pop("show_codes", None)
    if not codes_now:
        return redirect(url_for("auth.settings"))
    return render_template("recovery_codes.html", codes=codes_now)


@bp.get("/dev/outbox")
def dev_outbox():
    """While running locally with no mail service, see the emails that would
    have gone out (to copy a sign-up code). Never on the live site."""
    if not (current_app.debug or current_app.testing) or mailer.enabled() or current_app.config.get("ON_RENDER"):
        abort(404)
    return render_template("outbox.html", emails=list(reversed(mailer.outbox())))


# ---------- Command line ----------

@click.command("set-password")
@with_appcontext
@click.argument("email")
def set_password_command(email):
    """Change someone's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, email)
    if user is None:
        raise click.ClickException(f"There is no account for {email}.")
    password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    set_password(user, password)
    s.commit()
    click.echo("The password is changed.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.cli.add_command(set_password_command)
