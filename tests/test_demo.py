"""Try Demo: every page it can open shows an example, it stays private, and it goes away."""
from datetime import timedelta

from sqlalchemy import func, select

from jdrive import db, links, mailer, seed, tick
from jdrive.dates import utcnow
from jdrive.db import Base
from jdrive.models import Blob, Item, ShareLink, User

from .conftest import CSRF, post, text, upload, words


def start_demo(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    response = post(client, "/demo")
    assert response.headers["Location"].endswith("/drive")
    return client


def item_id(app, name):
    with app.app_context():
        return db.session().scalar(select(Item.id).where(Item.name == name).order_by(Item.id.desc()))


def test_the_login_page_offers_the_demo(client):
    assert "Try demo" in text(client.get("/login"))


def test_the_demo_starts_with_a_welcome(app):
    client = start_demo(app)
    page = text(client.get("/drive"))
    assert "You are Janelle Demo" in page and "Demo" in page
    for name in ("Photos", "School", "Work", "Resume.pdf", "Voice memo.wav", "Welcome to J: Drive.txt"):
        assert name in page, name


def test_every_page_has_an_example(app):
    client = start_demo(app)
    pages = {
        "/drive": "Photos",
        f"/folders/{item_id(app, 'Baguio Trip')}?view=grid": "Burnham Park.jpg",
        f"/folders/{item_id(app, 'Thesis')}": "Thesis draft.pdf",
        f"/files/{item_id(app, 'Thesis draft.pdf')}": "Version 3",
        f"/files/{item_id(app, 'Burnham Park.jpg')}": "/preview",
        f"/files/{item_id(app, 'Voice memo.wav')}": "<audio",
        f"/files/{item_id(app, 'Reading list.md')}": "Clean Code",
        f"/items/{item_id(app, 'Baguio Trip')}/share": "Ana Reyes",
        f"/items/{item_id(app, 'Work')}/share": "hasn't signed up yet",
        f"/items/{item_id(app, 'Homework Box')}/share": "can add files",
        "/shared": "Family Recipes",
        "/recent": "Welcome to J: Drive.txt",
        "/starred": "Resume.pdf",
        "/trash": "Old notes.txt",
        "/notifications": "shared Family Recipes with you",
        "/activity": "Carlo Diaz",
        "/settings": "Safari on iPhone",
        "/search?q=a": "result",
    }
    for url, example in pages.items():
        response = client.get(url)
        assert response.status_code == 200, url
        assert example in response.get_data(as_text=True) or example in words(response), (url, example)
        assert "empty-state" not in response.get_data(as_text=True), url


def test_the_demo_links_work_for_visitors(app):
    start_demo(app)
    with app.app_context(), app.test_request_context():
        s = db.session()
        trip = links.token_for(s.scalar(select(ShareLink).where(ShareLink.role == "viewer")))
        box = links.token_for(s.scalar(select(ShareLink).where(ShareLink.role == "uploader")))
    visitor = app.test_client()
    assert "Pasalubong.jpg" in text(visitor.get(f"/s/{trip}"))
    assert "Send Files to Janelle" in text(visitor.get(f"/s/{box}"))


def test_the_demo_can_upload_but_not_email_real_people(app):
    client = start_demo(app)
    assert upload(client, "mine.txt", b"hello")["ok"]
    folder = item_id(app, "Photos")
    response = post(client, f"/items/{folder}/share", {"action": "add", "emails": "real@example.com", "role": "viewer"})
    assert "can't email real people" in text(response)
    assert mailer.outbox(app) == []


def test_the_demo_cant_change_its_account(app):
    client = start_demo(app)
    assert post(client, "/settings", {"action": "name", "name": "Hacker"}).status_code == 403
    assert client.get("/settings/two-step").status_code == 403
    assert post(app.test_client(), "/login", {"email": "janelle.x@demo.invalid", "password": "x"}).status_code == 401


def test_demos_share_the_sample_files(app):
    start_demo(app)
    with app.app_context():
        first = db.session().scalar(select(func.count()).select_from(Blob))
    start_demo(app)
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Blob)) == first
        assert db.session().scalar(select(func.count()).select_from(Blob).where(Blob.is_sample.is_(False))) == 0


def test_demos_are_private_to_each_visitor(app):
    one, two = start_demo(app), start_demo(app)
    mine = item_id(app, "Photos")  # the second demo's
    assert two.get(f"/folders/{mine}").status_code == 200
    assert one.get(f"/folders/{mine}").status_code == 404


def test_a_demo_is_deleted_after_24_hours(app, ctx):
    client = start_demo(app)
    upload(client, "mine.txt", b"my own upload")
    assert tick.run(ctx, utcnow() + timedelta(hours=25))["demos_deleted"] == 4  # Janelle + 3 sample people
    assert ctx.scalar(select(func.count()).select_from(User)) == 0
    assert ctx.scalar(select(func.count()).select_from(Item)) == 0
    # The samples stay for the next demo; the visitor's own upload is swept.
    assert ctx.scalar(select(func.count()).select_from(Blob).where(Blob.is_sample.is_(False))) == 0
    assert ctx.scalar(select(func.count()).select_from(Blob)) > 0
    assert client.get("/drive").status_code == 302


def test_sign_up_for_real_leaves_the_demo(app):
    client = start_demo(app)
    assert "Sign Up" in text(client.get("/signup"))
    assert client.get("/drive").status_code == 302


def test_too_many_demos(app, monkeypatch):
    monkeypatch.setattr(seed, "MAX_DEMOS", 1)
    start_demo(app)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    response = post(client, "/demo", follow_redirects=True)
    assert "very busy" in text(response)


def test_links_to_many_rows_have_an_index():
    """Deleting a demo deletes thousands of rows by cascade: every foreign key needs an
    index that starts with its column, or each delete scans a whole table."""
    missing = []
    for table in Base.metadata.sorted_tables:
        starts = {tuple(index.columns.keys())[0] for index in table.indexes}
        starts |= {constraint.columns.keys()[0] for constraint in table.constraints
                   if constraint.__class__.__name__ in ("PrimaryKeyConstraint", "UniqueConstraint")}
        for fk in table.foreign_keys:
            if fk.parent.name not in starts:
                missing.append(f"{table.name}.{fk.parent.name}")
    assert missing == []

