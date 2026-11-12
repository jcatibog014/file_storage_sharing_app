"""Activity, notifications, logged-in devices and two-step sign-in."""
import re
from datetime import timedelta

import pyotp
from sqlalchemy import func, select

from jdrive import db, devices, links, mailer, twostep
from jdrive.dates import utcnow
from jdrive.models import Activity, LoginSession, ShareLink, User

from .conftest import PASSWORD, browser, get_user, is_logged_in, make_user, new_folder, post, text, upload, words


def verbs(app):
    with app.app_context():
        return [row.verb for row in db.session().scalars(select(Activity).order_by(Activity.id))]


# ---------- Activity ----------

def test_actions_are_logged_and_shown(app):
    maria = browser(app, make_user(app))
    make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Team")
    file_id = upload(maria, "plan.txt", parent_id=folder)["item"]["id"]
    post(maria, f"/items/{file_id}/rename", {"name": "Plan v2.txt"})
    maria.get(f"/files/{file_id}/download")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "editor"})
    post(maria, f"/items/{file_id}/trash")
    assert verbs(app) == ["folder_made", "uploaded", "renamed", "downloaded", "shared", "trashed"]
    page = words(maria.get("/activity"))
    assert "You renamed" in page and "from plan.txt" in page and "with ana@example.com as Editor" in page
    assert "You moved to the Trash" in page
    assert "You uploaded" in words(maria.get("/activity?only=uploaded"))
    assert "You renamed" not in words(maria.get("/activity?only=uploaded"))


def test_an_editors_actions_show_for_the_owner(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Team")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "editor"})
    upload(browser(app, ana_id), "from-ana.txt", parent_id=folder)
    assert "Ana Reyes uploaded" in words(maria.get("/activity"))
    assert "Ana Reyes uploaded" in words(maria.get(f"/folders/{folder}"))  # the folder's own history


def test_link_visitors_are_logged_without_a_name(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Album")
    post(maria, f"/items/{folder}/share", {"action": "link_on"})
    with app.app_context(), app.test_request_context():
        token = links.token_for(db.session().scalar(select(ShareLink)))
    app.test_client().get(f"/s/{token}")
    maria.get(f"/s/{token}")  # the owner's own visit isn't counted as a visitor
    assert verbs(app).count("link_opened") == 1
    assert "Someone with the link opened the link to" in words(maria.get("/activity"))


def test_history_stays_after_deleting_for_good(app):
    maria = browser(app, make_user(app))
    file_id = upload(maria, "gone.txt")["item"]["id"]
    post(maria, f"/items/{file_id}/trash")
    post(maria, f"/items/{file_id}/delete")
    assert "You deleted for good gone.txt" in words(maria.get("/activity"))
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Activity).where(Activity.item_id.is_(None))) == 3


def test_strangers_dont_see_your_activity(app):
    maria = browser(app, make_user(app))
    upload(maria, "secret-plan.txt")
    jose = browser(app, make_user(app, "Jose Rizal"))
    assert "secret-plan.txt" not in text(jose.get("/activity"))


# ---------- Notifications ----------

def test_a_share_rings_the_bell(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Photos")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "viewer"})
    ana = browser(app, ana_id)
    assert re.search(r'Notifications\s*<span class="count">1</span>', ana.get("/drive").get_data(as_text=True))
    page = text(ana.get("/notifications"))
    assert "Maria Santos shared Photos with you." in page and "New" in page
    assert '<span class="count">' not in ana.get("/drive").get_data(as_text=True)  # read now


def test_files_sent_through_a_link_ring_the_owners_bell(app):
    from .test_link_controls import link_upload, setup_link
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Box")
    token = setup_link(app, maria, folder, link_role="uploader")
    link_upload(browser(app), token, "homework.pdf", b"%PDF", "application/pdf")
    assert "Someone sent homework.pdf into Box." in text(maria.get("/notifications"))


# ---------- Logged-in devices ----------

def test_each_login_is_a_device_you_can_log_out(app):
    make_user(app, email="maria@example.com")
    phone, laptop = app.test_client(), app.test_client()
    for client, agent in ((phone, "Mozilla/5.0 (iPhone) Safari/604"), (laptop, "Mozilla/5.0 (Windows NT 10.0) Chrome/130")):
        post(client, "/login", {"email": "maria@example.com", "password": PASSWORD},
             headers={"User-Agent": agent})
    page = text(laptop.get("/settings"))
    assert "Safari on iPhone" in page and "Chrome on Windows" in page and "This device" in page
    with app.app_context():
        phone_row = db.session().scalar(select(LoginSession).where(LoginSession.device == "Safari on iPhone"))
    post(laptop, "/settings", {"action": "end_session", "session_id": phone_row.id})
    assert not is_logged_in(phone) and is_logged_in(laptop)


def test_log_out_other_devices_and_logging_out_ends_the_row(app):
    user_id = make_user(app)
    one, two, three = browser(app, user_id), browser(app, user_id), browser(app, user_id)
    response = post(one, "/settings", {"action": "logout_others"}, follow_redirects=True)
    assert "Logged out 2 other devices." in text(response)
    assert is_logged_in(one) and not is_logged_in(two) and not is_logged_in(three)
    with one.session_transaction() as session:
        sid = session["sid"]
    post(one, "/logout")
    with app.app_context():
        assert db.session().get(LoginSession, sid).ended_at is not None


def test_a_stolen_session_id_without_its_secret_is_useless(app):
    user_id = make_user(app)
    real = browser(app, user_id)
    with real.session_transaction() as session:
        stolen = dict(session)
    fake = app.test_client()
    with fake.session_transaction() as session:
        session.update(stolen)
        session["ss"] = "guessed"
    assert not is_logged_in(fake)


def test_a_new_device_gets_an_email(app):
    make_user(app, email="maria@example.com")
    for agent in ("Mozilla/5.0 (Windows NT 10.0) Chrome/130", "Mozilla/5.0 (Windows NT 10.0) Chrome/130"):
        post(app.test_client(), "/login", {"email": "maria@example.com", "password": PASSWORD},
             headers={"User-Agent": agent})
    assert mailer.last_to("maria@example.com", app) is None  # first device, then the same one again
    post(app.test_client(), "/login", {"email": "maria@example.com", "password": PASSWORD},
         headers={"User-Agent": "Mozilla/5.0 (Android 14) Chrome/130"})
    email = mailer.last_to("maria@example.com", app)
    assert email["Subject"] == "New login to your J: Drive" and "Chrome on Android" in email.get_content()


def test_old_ended_logins_are_forgotten(app, ctx):
    user = ctx.get(User, make_user(app))
    session_id, _ = devices.start(ctx, user, "Old phone", "1.2.3.4", utcnow() - timedelta(days=200))
    devices.end(ctx, session_id, utcnow() - timedelta(days=100))
    ctx.commit()
    assert devices.purge(ctx, utcnow()) == 1


# ---------- Two-step sign-in ----------

def turn_on_twostep(app, client):
    page = client.get("/settings/two-step").get_data(as_text=True)
    secret = re.search(r'<code class="secret-key">([A-Z2-7]+)</code>', page).group(1)
    response = post(client, "/settings/two-step", {"code": pyotp.TOTP(secret).now()})
    assert response.headers["Location"].endswith("/settings/recovery-codes")
    codes = re.findall(r"<li><code>([A-Z0-9-]+)</code></li>", client.get("/settings/recovery-codes").get_data(as_text=True))
    return secret, codes


def test_turning_it_on_needs_a_code_from_the_app(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    page = client.get("/settings/two-step").get_data(as_text=True)
    assert "<svg" in page  # the QR code is drawn here, no outside service
    assert "isn't right" in text(post(client, "/settings/two-step", {"code": "000000"}))
    assert not get_user(app, "maria@example.com").totp_enabled_at
    secret, codes = turn_on_twostep(app, client)
    assert len(codes) == 8
    assert client.get("/settings/recovery-codes").status_code == 302  # shown once
    assert "On. Logging in also needs a code" in text(client.get("/settings"))


def log_in_with_password(client):
    return post(client, "/login", {"email": "maria@example.com", "password": PASSWORD})


def test_logging_in_then_needs_the_code(app, ctx):
    client = browser(app, make_user(app, email="maria@example.com"))
    secret, codes = turn_on_twostep(app, client)
    visitor = app.test_client()
    assert log_in_with_password(visitor).headers["Location"].endswith("/login/code")
    assert not is_logged_in(visitor)
    assert "isn't right" in text(post(visitor, "/login/code", {"code": "123456"}))
    # Setting up used this window's code, so use the next window's (a clock a little ahead still counts).
    import time
    user = ctx.get(User, get_user(app, "maria@example.com").id)
    code = pyotp.TOTP(secret).at(int(time.time()) + 30)
    assert post(visitor, "/login/code", {"code": code}).status_code == 302
    assert is_logged_in(visitor)
    # The same code can't be used again.
    again = app.test_client()
    log_in_with_password(again)
    assert "isn't right" in text(post(again, "/login/code", {"code": code}))
    assert user is not None


def test_a_recovery_code_works_once(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    _secret, codes = turn_on_twostep(app, client)
    first = app.test_client()
    log_in_with_password(first)
    assert post(first, "/login/code", {"code": codes[0].lower().replace("-", " ")}).status_code == 302
    second = app.test_client()
    log_in_with_password(second)
    assert "isn't right" in text(post(second, "/login/code", {"code": codes[0]}))
    assert "7 recovery codes left" in text(client.get("/settings"))


def test_code_guessing_is_limited(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    secret, _codes = turn_on_twostep(app, client)
    visitor = app.test_client()
    log_in_with_password(visitor)
    for _ in range(twostep.TRIES):
        post(visitor, "/login/code", {"code": "000000"})
    assert "Too many wrong codes" in text(post(visitor, "/login/code", {"code": pyotp.TOTP(secret).now()}))


def test_the_code_page_needs_the_password_first(app):
    assert app.test_client().get("/login/code").headers["Location"].endswith("/login")


def test_turning_it_off_needs_the_password(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    turn_on_twostep(app, client)
    assert "isn't your current password" in text(post(client, "/settings", {"action": "twostep_off", "current_password": "x"}))
    post(client, "/settings", {"action": "twostep_off", "current_password": PASSWORD})
    assert log_in_with_password(app.test_client()).headers["Location"].endswith("/drive")


def test_new_recovery_codes_replace_the_old_ones(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    _secret, old = turn_on_twostep(app, client)
    post(client, "/settings", {"action": "recovery_codes", "current_password": PASSWORD})
    new = re.findall(r"<li><code>([A-Z0-9-]+)</code></li>", client.get("/settings/recovery-codes").get_data(as_text=True))
    assert len(new) == 8 and not set(new) & set(old)
    visitor = app.test_client()
    log_in_with_password(visitor)
    assert "isn't right" in text(post(visitor, "/login/code", {"code": old[1]}))
