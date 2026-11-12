"""Sign-up with an emailed code, logging in and out, new passwords, and the protections."""
from datetime import timedelta

from jdrive import codes, db, mailer
from jdrive.dates import utcnow
from jdrive.models import EmailCode, User

from .conftest import PASSWORD, browser, code_sent_to, get_user, is_logged_in, make_user, post, sign_up, text


def test_sign_up_sends_a_code_and_the_account_waits_for_it(client, app):
    response = post(client, "/signup", {"name": "Maria Santos", "email": " Maria@Example.com ",
                                        "password": PASSWORD, "confirm_password": PASSWORD})
    assert response.headers["Location"].endswith("/verify")
    user = get_user(app, "maria@example.com")
    assert user is not None and user.email_verified_at is None
    assert not is_logged_in(client)  # nothing works until the code is typed in
    page = text(client.get("/verify"))
    assert "maria@example.com" in page and "6-digit code" in page
    email = mailer.last_to("maria@example.com", app)
    assert "J: Drive" in email["Subject"]
    # Only a hash of the code is kept.
    code = code_sent_to(app, "maria@example.com")
    with app.app_context():
        stored = db.session().query(EmailCode).one()
        assert code not in stored.code_hash


def test_the_right_code_confirms_the_email_and_logs_in(client, app):
    sign_up(client, verify=False)
    response = post(client, "/verify", {"code": code_sent_to(app, "maria@example.com")})
    assert response.headers["Location"].endswith("/drive")
    assert get_user(app, "maria@example.com").email_verified_at is not None
    assert is_logged_in(client)
    assert "My Drive" in text(client.get("/drive"))


def test_a_wrong_code_counts_down_and_then_runs_out(client, app):
    sign_up(client, verify=False)
    right = code_sent_to(app, "maria@example.com")
    wrong = "000000" if right != "000000" else "111111"
    for left in (4, 3, 2, 1):
        page = text(post(client, "/verify", {"code": wrong}))
        assert f"{left} tries left" in page
    post(client, "/verify", {"code": wrong})
    # After 5 wrong tries even the right code no longer works.
    page = text(post(client, "/verify", {"code": right}))
    assert "run out" in page
    assert get_user(app, "maria@example.com").email_verified_at is None


def test_a_code_runs_out_after_15_minutes(app, ctx):
    user_id = make_user(app, verified=False)
    user = ctx.get(User, user_id)
    now = utcnow()
    assert codes.send(ctx, user, codes.VERIFY, now) is None
    code = code_sent_to(app, user.email)
    assert not codes.check(ctx, user, codes.VERIFY, code, now + timedelta(minutes=16))
    assert codes.check(ctx, user, codes.VERIFY, code, now + timedelta(minutes=14))
    assert not codes.check(ctx, user, codes.VERIFY, code, now + timedelta(minutes=14))  # used up


def test_a_new_code_waits_a_minute_and_replaces_the_old_one(client, app):
    sign_up(client, verify=False)
    first = code_sent_to(app, "maria@example.com")
    response = post(client, "/verify", {"action": "resend"}, follow_redirects=True)
    assert "wait a minute" in text(response)
    with app.app_context():
        s = db.session()
        for code in s.query(EmailCode):
            code.created_at -= timedelta(minutes=2)
        s.commit()
    post(client, "/verify", {"action": "resend"})
    second = code_sent_to(app, "maria@example.com")
    if first != second:
        assert "isn't right" in text(post(client, "/verify", {"code": first}))
    assert post(client, "/verify", {"code": second}).status_code == 302


def test_at_most_five_codes_an_hour(app, ctx):
    user = ctx.get(User, make_user(app, verified=False))
    now = utcnow()
    for minute in range(5):
        assert codes.send(ctx, user, codes.VERIFY, now + timedelta(minutes=minute * 2)) is None
    assert "an hour" in codes.send(ctx, user, codes.VERIFY, now + timedelta(minutes=12))


def test_sign_up_checks_what_was_typed(client):
    page = text(post(client, "/signup", {"name": "", "email": "nope", "password": "short",
                                    "confirm_password": "other"}))
    assert "Enter your name." in page
    assert "doesn't look like an email" in page
    assert "at least 8" in page
    page = text(post(client, "/signup", {"name": "X", "email": "x@demo.invalid", "password": PASSWORD,
                                    "confirm_password": PASSWORD}))
    assert "real email" in page


def test_a_confirmed_email_cant_sign_up_again(client, app):
    make_user(app, email="maria@example.com")
    page = text(post(client, "/signup", {"name": "Someone Else", "email": "maria@example.com", "password": PASSWORD,
                                    "confirm_password": PASSWORD}))
    assert "already has an account" in page


def test_an_unconfirmed_sign_up_is_replaced_by_whoever_can_read_the_inbox(client, app):
    # Someone types Maria's address but can't read her email...
    sign_up(browser(app), name="Not Maria", password="the squatter", verify=False)
    # ...so Maria can still sign up with it, and her password is the one that counts.
    sign_up(client, name="Maria Santos", password=PASSWORD)
    user = get_user(app, "maria@example.com")
    assert user.name == "Maria Santos" and user.is_verified
    assert post(browser(app), "/login", {"email": "maria@example.com", "password": "the squatter"}).status_code == 401


def test_log_in_and_out(client, app):
    make_user(app, email="maria@example.com")
    response = post(client, "/login", {"email": "MARIA@example.com", "password": PASSWORD})
    assert response.headers["Location"].endswith("/drive")
    assert is_logged_in(client)
    post(client, "/logout")
    assert not is_logged_in(client)


def test_logging_in_before_confirming_sends_a_code(client, app):
    make_user(app, email="maria@example.com", verified=False)
    response = post(client, "/login", {"email": "maria@example.com", "password": PASSWORD})
    assert response.headers["Location"].endswith("/verify")
    assert not is_logged_in(client)
    assert post(client, "/verify", {"code": code_sent_to(app, "maria@example.com")}).status_code == 302
    assert is_logged_in(client)


def test_wrong_logins_say_the_same_thing_and_lock_after_five(client, app):
    make_user(app, email="maria@example.com")
    unknown = text(post(client, "/login", {"email": "nobody@example.com", "password": "x"}))
    wrong = text(post(client, "/login", {"email": "maria@example.com", "password": "x"}))
    assert "Wrong email or password." in unknown and "Wrong email or password." in wrong
    for _ in range(4):
        post(client, "/login", {"email": "maria@example.com", "password": "x"})
    page = text(post(client, "/login", {"email": "maria@example.com", "password": PASSWORD}))
    assert "15 minutes" in page
    assert not is_logged_in(client)


def test_one_network_can_only_try_so_many_logins(client, app):
    for number in range(30):
        post(client, "/login", {"email": f"user{number}@example.com", "password": "x"})
    make_user(app, email="maria@example.com")
    page = text(post(client, "/login", {"email": "maria@example.com", "password": PASSWORD}))
    assert "Too many tries from your network" in page


def test_forgot_password_emails_a_code_to_real_accounts_only(client, app):
    make_user(app, email="maria@example.com")
    for email in ("maria@example.com", "nobody@example.com"):
        response = post(browser(app), "/forgot", {"email": email}, follow_redirects=True)
        assert "If " + email + " has an account" in text(response)
    assert mailer.last_to("maria@example.com", app) is not None
    assert mailer.last_to("nobody@example.com", app) is None


def test_reset_password_with_the_code(client, app):
    make_user(app, email="maria@example.com")
    other_device = browser(app, get_user(app, "maria@example.com").id)
    post(client, "/forgot", {"email": "maria@example.com"})
    page = text(post(client, "/reset", {"code": "123", "new_password": "new password",
                                   "confirm_password": "new password"}))
    assert "isn't right" in page
    response = post(client, "/reset", {"code": code_sent_to(app, "maria@example.com"),
                                       "new_password": "new password", "confirm_password": "new password"})
    assert response.headers["Location"].endswith("/login")
    assert not is_logged_in(other_device)  # every old session ends
    assert post(client, "/login", {"email": "maria@example.com", "password": "new password"}).status_code == 302


def test_settings_change_name_and_password(app):
    user_id = make_user(app)
    client = browser(app, user_id)
    other_device = browser(app, user_id)
    post(client, "/settings", {"action": "name", "name": "Maria S."})
    assert get_user(app, "maria@example.com").name == "Maria S."
    page = text(post(client, "/settings", {"action": "password", "current_password": "wrong", "new_password": "new password",
                                      "confirm_password": "new password"}))
    assert "isn't your current password" in page
    post(client, "/settings", {"action": "password", "current_password": PASSWORD, "new_password": "new password",
                               "confirm_password": "new password"})
    assert is_logged_in(client)
    assert not is_logged_in(other_device)


def test_pages_need_a_login(client):
    response = client.get("/drive")
    assert response.status_code == 302 and "/login?next=/drive" in response.headers["Location"]
    assert client.get("/").headers["Location"].endswith("/login")


def test_forms_need_the_csrf_token(app):
    make_user(app, email="maria@example.com")
    client = app.test_client()
    response = client.post("/login", data={"email": "maria@example.com", "password": PASSWORD})
    assert response.status_code == 400


def test_next_only_goes_to_this_site(client, app):
    make_user(app, email="maria@example.com")
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example"):
        response = post(browser(app), "/login", {"email": "maria@example.com", "password": PASSWORD, "next": bad})
        assert response.headers["Location"].endswith("/drive")


def test_security_headers(client, app):
    response = client.get("/login")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    logged_in = browser(app, make_user(app)).get("/drive")
    assert logged_in.headers["Cache-Control"] == "no-store"


def test_the_outbox_page_is_only_for_local_use(client, app):
    sign_up(client, verify=False)
    assert "Your J: Drive code" in text(client.get("/dev/outbox"))
    app.config["ON_RENDER"] = True
    assert client.get("/dev/outbox").status_code == 404


def test_nothing_is_sent_to_demo_addresses(app):
    with app.test_request_context():
        assert mailer.send("someone@demo.invalid", "Hi", "Hello")
        assert mailer.outbox() == []
