"""Two-step sign-in with an authenticator app (Google Authenticator, Microsoft
Authenticator, 2FAS...).

Turning it on: we make a random secret, show it as a QR code, and the app
keeps it. Both sides then work out the same 6-digit code from the secret and
the time (TOTP, a new code every 30 seconds). We turn it on only after the
person types a code from the app, so we know the app really has it. They get
8 one-time recovery codes (only hashes are kept) for a lost phone.

Logging in: the right password, then a code. Each code works once (we keep
the last time step used), and 5 wrong codes in 15 minutes stop the tries.
"""
import hashlib
import hmac
import secrets
from datetime import UTC

import pyotp
import segno
from flask import redirect, session, url_for
from sqlalchemy import delete, select

from . import limits
from .models import RecoveryCode

ISSUER = "J: Drive"
RECOVERY_COUNT = 8
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L
TRIES = 5


def is_on(user):
    return user.totp_enabled_at is not None and bool(user.totp_secret)


def new_secret():
    return pyotp.random_base32()


def qr_svg(user, secret):
    """The QR code the app scans, as an SVG (drawn here: no outside service sees the secret)."""
    address = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return segno.make(address, error="m").svg_inline(scale=5, dark="#2e1a47", light="#ffffff")


def _step_ok(user, typed, moment):
    """The code's time step if right (allowing one step either way for a slow clock), else None."""
    code = "".join(ch for ch in (typed or "") if ch.isdigit())
    if len(code) != 6 or not user.totp_secret:
        return None
    totp = pyotp.TOTP(user.totp_secret)
    now = int(moment.replace(tzinfo=UTC).timestamp())  # moments are naive UTC
    for drift in (-1, 0, 1):
        step = now // 30 + drift
        if hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


def check_code(s, user, typed, moment):
    """True for a right, unused app code (then it can't be used again)."""
    step = _step_ok(user, typed, moment)
    if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
        return False
    user.totp_last_step = step
    return True


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def clean_recovery(text):
    letters = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    return "-".join(letters[i:i + 4] for i in range(0, len(letters), 4))


def new_recovery_codes(s, user):
    """8 fresh codes (the old ones stop working). Shown once."""
    s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = []
    for _ in range(RECOVERY_COUNT):
        code = "-".join("".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(4)) for _ in range(2))
        codes.append(code)
        s.add(RecoveryCode(user_id=user.id, code_hash=_hash(code)))
    return codes


def use_recovery_code(s, user, typed, moment):
    row = s.scalar(select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None),
                                              RecoveryCode.code_hash == _hash(clean_recovery(typed))))
    if row is None:
        return False
    row.used_at = moment
    return True


def codes_left(s, user):
    return len(list(s.scalars(select(RecoveryCode.id).where(RecoveryCode.user_id == user.id,
                                                            RecoveryCode.used_at.is_(None)))))


def try_login_code(s, user, typed, moment):
    """The second step of logging in: an app code or a recovery code. Tries are limited."""
    if not limits.hit(s, f"twostep:{user.id}", TRIES, 900, moment):
        return False, "Too many wrong codes. Please wait 15 minutes."
    if check_code(s, user, typed, moment) or use_recovery_code(s, user, typed, moment):
        limits.clear(s, f"twostep:{user.id}")
        return True, None
    return False, "That code isn't right. Use the newest code from your app, or a recovery code."


def turn_off(s, user):
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))


def start(user, next_page):
    """The password was right: now ask for the code (nobody is logged in yet)."""
    session["twostep_user"] = user.id
    session["twostep_next"] = next_page or ""
    return redirect(url_for("auth.login_code"))
