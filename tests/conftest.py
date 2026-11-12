import os
import re

import pytest
from sqlalchemy import select

from jdrive import auth, create_app, db, mailer
from jdrive.dates import utcnow
from jdrive.models import User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live database).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    """The real password hash is slow on purpose (to slow down guessing). Tests make
    many users, so they use a quick setting of the same kind of hash."""
    from functools import partial

    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(auth, "generate_password_hash", partial(generate_password_hash, method="pbkdf2:sha256:1000"))


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
        "BUCKET_DIR": str(tmp_path / "bucket"),
        "SERVER_NAME": "localhost",
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
    return app


@pytest.fixture
def ctx(app):
    """An app context, for tests that use db.session() directly."""
    with app.app_context():
        yield db.session()


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


def make_user(app, name="Maria Santos", email=None, verified=True, **fields):
    """Put a user straight into the database. Returns the id."""
    with app.app_context():
        s = db.session()
        email = email or f"{name.split()[0].lower()}@example.com"
        user = auth.create_user(s, name, email, PASSWORD, verified_at=utcnow() if verified else None, **fields)
        s.commit()
        return user.id


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def browser(app, user_id=None):
    """A fresh client, logged in as this user if given."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return log_in_as(client, user_id) if user_id else client


def is_logged_in(client):
    """The real check: load_user() runs on every request and drops a session that
    is no longer valid, so ask after a request what is left in the cookie."""
    client.get("/settings")
    with client.session_transaction() as session:
        return "user_id" in session


def get_user(app, email):
    with app.app_context():
        return db.session().scalar(select(User).where(User.email == email))


def code_sent_to(app, email):
    """The 6-digit code in the newest email to this address."""
    message = mailer.last_to(email, app)
    assert message is not None, f"no email to {email}"
    return re.search(r"\b(\d{6})\b", message.get_content()).group(1)


def sign_up(client, name="Maria Santos", email="maria@example.com", password=PASSWORD, verify=True):
    """Sign up through the real pages, and type the emailed code too if verify."""
    response = post(client, "/signup", {"name": name, "email": email, "password": password,
                                        "confirm_password": password})
    assert response.status_code == 302, response.get_data(as_text=True)
    if verify:
        response = post(client, "/verify", {"code": code_sent_to(client.application, email)})
        assert response.status_code == 302
    return response


def text(response):
    """A page's words the way a person reads them (&#39; back to ', &amp; to &)."""
    import html
    return html.unescape(response.get_data(as_text=True))


def upload(client, name="notes.txt", data=b"hello", parent_id=None, content_type="text/plain", target_id=None):
    """Upload the way the browser does: ask for a link, PUT to the bucket, say done.
    Returns the JSON of the last step."""
    started = client.post("/api/uploads", json={"parent_id": parent_id or "", "target_id": target_id or "",
                                                "name": name, "size": len(data), "type": content_type},
                          headers={"X-CSRF-Token": CSRF})
    answer = started.get_json()
    if not answer["ok"]:
        return answer
    client.put(answer["url"], data=data, headers=answer["headers"])
    return client.post(f"/api/uploads/{answer['upload_id']}/done", json={}, headers={"X-CSRF-Token": CSRF}).get_json()


def new_folder(client, name="Photos", parent_id=None):
    """Make a folder through the page. Returns its id."""
    from jdrive.models import Item
    post(client, "/folders/new", {"name": name, "parent_id": parent_id or ""})
    with client.application.app_context():
        return db.session().scalar(select(Item.id).order_by(Item.id.desc()).limit(1))


def get_item(app, item_id):
    from jdrive.models import Item
    with app.app_context():
        return db.session().get(Item, item_id)
