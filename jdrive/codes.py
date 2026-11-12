"""The 6-digit codes we email: they prove that an email address is real and
belongs to the person typing it.

- "verify": sent at sign-up. The account works only after it is typed in.
- "reset": sent from Forgot Password, to set a new password.

Only an HMAC of the code is stored (with the app's secret key), so a copy of
the database gives nobody a working code. A code runs out after 15 minutes,
and after 5 wrong tries it stops working: guessing 1 in a million 5 times is
hopeless. Asking for a new code waits 60 seconds, at most 5 an hour.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import select, update

from . import limits, mailer
from .models import EmailCode

VERIFY = "verify"
RESET = "reset"
LIFETIME = timedelta(minutes=15)
TRIES = 5
RESEND_WAIT = timedelta(seconds=60)
SENDS_PER_HOUR = 5

SUBJECTS = {
    VERIFY: "Your J: Drive code: {code}",
    RESET: "Reset your J: Drive password: {code}",
}
BODIES = {
    VERIFY: ("Hi {name},\n\nYour code to finish signing up for J: Drive is:\n\n    {code}\n\n"
             "It works for 15 minutes. If you didn't sign up, you can ignore this email: "
             "nothing happens without the code.\n\nJ: Drive"),
    RESET: ("Hi {name},\n\nYour code to set a new J: Drive password is:\n\n    {code}\n\n"
            "It works for 15 minutes. If you didn't ask for it, ignore this email: "
            "your password stays the same.\n\nJ: Drive"),
}


def _hash(user_id, purpose, code):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{user_id}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def clean(text):
    """People paste codes with spaces or dashes."""
    return "".join(ch for ch in (text or "") if ch.isdigit())[:6]


def latest(s, user, purpose):
    return s.scalar(select(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose)
                    .order_by(EmailCode.id.desc()).limit(1))


def wait_seconds(s, user, purpose, moment):
    """How long before another code may be sent (0 = now)."""
    newest = latest(s, user, purpose)
    if newest is None:
        return 0
    left = (newest.created_at + RESEND_WAIT - moment).total_seconds()
    return max(0, int(left + 0.999))


def send(s, user, purpose, moment):
    """Make a new code, email it, and return None, or a message saying why not.
    Older codes for the same purpose stop working. The caller commits."""
    if wait_seconds(s, user, purpose, moment):
        return "We just sent a code. Please wait a minute before asking for another."
    if not limits.hit(s, f"code-send:{purpose}:{user.id}", SENDS_PER_HOUR, 3600, moment):
        return "That's a lot of codes. Please wait an hour, or check your spam folder for the last one."
    s.execute(update(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose,
                                      EmailCode.used_at.is_(None)).values(used_at=moment))
    code = f"{secrets.randbelow(1_000_000):06d}"
    s.add(EmailCode(user_id=user.id, purpose=purpose, code_hash=_hash(user.id, purpose, code),
                    created_at=moment, expires_at=moment + LIFETIME, tries_left=TRIES))
    s.flush()
    sent = mailer.send(user.email, SUBJECTS[purpose].format(code=code),
                       BODIES[purpose].format(name=user.first_name or "there", code=code))
    if not sent:
        return "We couldn't send the email just now. Please try again in a minute."
    return None


def check(s, user, purpose, typed, moment):
    """True if the code is right (it is then used up). A wrong try uses up one
    of the 5. The caller commits either way, so the count is kept."""
    code = latest(s, user, purpose)
    if code is None or code.used_at is not None or code.expires_at <= moment or code.tries_left <= 0:
        return False
    if hmac.compare_digest(code.code_hash, _hash(user.id, purpose, clean(typed))):
        code.used_at = moment
        return True
    code.tries_left -= 1
    return False


def problem_after_wrong(s, user, purpose, moment):
    """What to tell someone after a wrong code."""
    code = latest(s, user, purpose)
    if code is None or code.used_at is not None or code.expires_at <= moment or code.tries_left <= 0:
        return "That code has run out. Ask for a new one."
    return f"That code isn't right. {code.tries_left} tries left."
