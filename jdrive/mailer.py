"""Sending email. J: Drive needs it: sign-up codes, password resets and share
notices go to people's real inboxes.

Three ways to send, the first that is set up wins:

1. Brevo's web API (BREVO_API_KEY + MAIL_FROM). This is the one for the live
   site, because Render's free plan blocks the usual email ports (SMTP 25, 465
   and 587). Brevo is free for 300 emails a day, and MAIL_FROM can be your own
   Gmail address once Brevo has checked it (docs/DEPLOY.md).
2. Gmail with an app password (MAIL_USERNAME + MAIL_PASSWORD). Works when the
   app runs on your own computer.
3. Nothing set up (and always in tests): the email is kept in a list, written
   to the log, and shown at /dev/outbox while running locally with --debug,
   so you can still copy a code.

Demo people have addresses ending in .invalid (a name that can never be a
real address), and nothing is ever sent to them.
"""
import json
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from flask import current_app

GMAIL_HOST = "smtp.gmail.com"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
FROM_NAME = "J: Drive"


def way(app=None):
    """"brevo", "gmail" or None (nothing set up)."""
    config = (app or current_app).config
    if config.get("BREVO_API_KEY") and config.get("MAIL_FROM"):
        return "brevo"
    if config.get("MAIL_USERNAME") and config.get("MAIL_PASSWORD"):
        return "gmail"
    return None


def enabled(app=None):
    return way(app) is not None


def outbox(app=None):
    """Emails that weren't really sent (tests and /dev/outbox read this)."""
    return (app or current_app).extensions.setdefault("jdrive_outbox", [])


def is_undeliverable(address):
    return (address or "").lower().endswith(".invalid")


def sender_address(app):
    return app.config.get("MAIL_FROM") or app.config.get("MAIL_USERNAME") or "j-drive@localhost"


def send(to, subject, body):
    """Send one plain-text email. True if it went (or was kept in the outbox),
    False if the mail service said no. Never raises: a mail problem must not
    break the page; the caller decides what to tell the person."""
    app = current_app
    if is_undeliverable(to):
        return True
    message = EmailMessage()
    message["From"] = f"{FROM_NAME} <{sender_address(app)}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    how = None if app.testing else way(app)
    try:
        if how == "brevo":
            _send_brevo(app, to, subject, body)
        elif how == "gmail":
            with smtplib.SMTP_SSL(GMAIL_HOST, 465, timeout=20) as smtp:
                smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
                smtp.send_message(message)
        else:
            outbox(app).append(message)
            app.logger.info("Email not sent (no mail service is set up). To %s: %s\n%s", to, subject, body)
        return True
    except (OSError, smtplib.SMTPException, urllib.error.URLError):
        app.logger.exception("Sending an email to %s failed.", to)
        return False


def _send_brevo(app, to, subject, body):
    data = json.dumps({
        "sender": {"name": FROM_NAME, "email": app.config["MAIL_FROM"]},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }).encode()
    request = urllib.request.Request(BREVO_URL, data=data, method="POST", headers={
        "api-key": app.config["BREVO_API_KEY"],
        "content-type": "application/json",
        "accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (a fixed https address)
        if response.status >= 300:
            raise OSError(f"Brevo answered {response.status}")


def last_to(address, app=None):
    """The newest outbox email to this address (tests use it)."""
    for message in reversed(outbox(app)):
        if message["To"] == address:
            return message
    return None
