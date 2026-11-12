"""Link controls: an end date, a password, downloads off, and "Can upload" links."""
import io
from datetime import timedelta

from sqlalchemy import select

from jdrive import db, links
from jdrive.dates import to_local, utcnow
from jdrive.models import Item, ShareLink

from .conftest import CSRF, browser, get_item, make_user, new_folder, post, text, upload


def setup_link(app, client, item_id, **settings):
    """Turn the link on, save settings, and return its token."""
    post(client, f"/items/{item_id}/share", {"action": "link_on"})
    if settings:
        data = {"action": "link_settings", "link_role": "viewer", "expires": "", "allow_download": "1",
                "password_action": "keep", "password": ""}
        data.update(settings)
        post(client, f"/items/{item_id}/share", data)
    with app.app_context(), app.test_request_context():
        return links.token_for(db.session().scalar(select(ShareLink).where(ShareLink.item_id == item_id)))


def the_link(app, item_id):
    with app.app_context():
        return db.session().scalar(select(ShareLink).where(ShareLink.item_id == item_id))


def test_a_link_with_an_end_date_stops_after_that_day(app, ctx):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Trip")
    tomorrow = (to_local(utcnow()) + timedelta(days=1)).date().isoformat()
    token = setup_link(app, maria, folder, expires=tomorrow)
    link = the_link(app, folder)
    assert to_local(link.expires_at).date().isoformat() == tomorrow
    visitor = app.test_client()
    assert visitor.get(f"/s/{token}").status_code == 200
    stored = ctx.get(ShareLink, link.id)
    stored.expires_at = utcnow() - timedelta(minutes=1)
    ctx.commit()
    assert visitor.get(f"/s/{token}").status_code == 404
    assert "This link has run out" in text(maria.get(f"/items/{folder}/share"))


def test_end_date_checks(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Trip")
    setup_link(app, maria, folder)
    for day, message in (("2020-01-01", "after today"), ("not a date", "isn't right"),
                         ((utcnow() + timedelta(days=400)).date().isoformat(), "at most a year")):
        response = post(maria, f"/items/{folder}/share", {"action": "link_settings", "link_role": "viewer",
                                                         "expires": day, "password_action": "keep"},
                        follow_redirects=True)
        assert message in text(response), day


def test_a_password_protected_link(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Secret Plans")
    file_id = upload(maria, "plan.txt", parent_id=folder)["item"]["id"]
    token = setup_link(app, maria, folder, password_action="set", password="pandesal")
    assert "pandesal" not in (the_link(app, folder).password_hash or "")
    visitor = app.test_client()
    with visitor.session_transaction() as session:
        session["csrf"] = CSRF
    page = visitor.get(f"/s/{token}")
    assert page.status_code == 401 and "Password Needed" in text(page)
    assert "Secret Plans" not in text(page)  # nothing shows before the password
    assert visitor.get(f"/s/{token}/i/{file_id}").status_code == 401
    assert visitor.get(f"/s/{token}/d/{file_id}").status_code == 404
    assert "isn't right" in text(post(visitor, f"/s/{token}/unlock", {"password": "wrong"}))
    assert post(visitor, f"/s/{token}/unlock", {"password": "pandesal"}).status_code == 302
    assert "Secret Plans" in text(visitor.get(f"/s/{token}"))
    assert visitor.get(f"/s/{token}/d/{file_id}").status_code == 302
    # A new password locks this browser out again.
    post(maria, f"/items/{folder}/share", {"action": "link_settings", "link_role": "viewer", "allow_download": "1",
                                           "password_action": "set", "password": "ensaymada"})
    assert visitor.get(f"/s/{token}").status_code == 401


def test_password_guessing_is_limited(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    token = setup_link(app, maria, folder, password_action="set", password="right one")
    visitor = browser(app)
    for _ in range(links.PASSWORD_TRIES):
        post(visitor, f"/s/{token}/unlock", {"password": "guess"})
    response = post(visitor, f"/s/{token}/unlock", {"password": "right one"})
    assert "Too many wrong tries" in text(response)


def test_removing_the_password(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    token = setup_link(app, maria, folder, password_action="set", password="secret")
    post(maria, f"/items/{folder}/share", {"action": "link_settings", "link_role": "viewer", "allow_download": "1",
                                           "password_action": "remove"})
    assert app.test_client().get(f"/s/{token}").status_code == 200
    page = text(post(maria, f"/items/{folder}/share", {"action": "link_settings", "link_role": "viewer",
                                                       "password_action": "set", "password": "ab"},
                     follow_redirects=True))
    assert "at least 4" in page


def test_downloads_can_be_turned_off(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Look Only")
    file_id = upload(maria, "photo.jpg", parent_id=folder, content_type="image/jpeg")["item"]["id"]
    token = setup_link(app, maria, folder, allow_download="")
    visitor = app.test_client()
    page = text(visitor.get(f"/s/{token}"))
    assert "photo.jpg" in page and "Downloads are off" in page and f"/s/{token}/d/" not in page
    assert visitor.get(f"/s/{token}/d/{file_id}").status_code == 403


# ---------- "Can upload" links ----------

def link_upload(client, token, name="hello.txt", data=b"hello", content_type="text/plain"):
    started = client.post(f"/s/{token}/api/uploads", json={"name": name, "size": len(data), "type": content_type},
                          headers={"X-CSRF-Token": CSRF})
    answer = started.get_json()
    if not answer or not answer.get("ok"):
        return answer or {"status": started.status_code}
    client.put(answer["url"], data=data, headers=answer["headers"])
    return client.post(f"/s/{token}/api/uploads/{answer['upload_id']}/done", json={},
                       headers={"X-CSRF-Token": CSRF}).get_json()


def test_an_upload_link_takes_files_without_showing_the_folder(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Homework Box")
    upload(maria, "teacher-notes.txt", parent_id=folder)
    token = setup_link(app, maria, folder, link_role="uploader")
    visitor = browser(app)
    page = text(visitor.get(f"/s/{token}"))
    assert "Send Files to Maria" in page and "teacher-notes.txt" not in page
    assert link_upload(visitor, token, "essay.docx", b"my essay")["ok"]
    assert "essay.docx" in text(visitor.get(f"/s/{token}"))  # "You sent"
    with app.app_context():
        sent = db.session().scalar(select(Item).where(Item.name == "essay.docx"))
        assert sent.parent_id == folder and sent.owner_id == get_item(app, folder).owner_id
        assert sent.created_by_id is None
    # Nothing inside can be opened or downloaded through it.
    assert visitor.get(f"/s/{token}/i/{sent.id}").status_code == 404
    assert visitor.get(f"/s/{token}/d/{sent.id}").status_code == 404


def test_only_the_same_browser_finishes_its_upload(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Box")
    token = setup_link(app, maria, folder, link_role="uploader")
    one, two = browser(app), browser(app)
    answer = one.post(f"/s/{token}/api/uploads", json={"name": "a.txt", "size": 1, "type": "text/plain"},
                      headers={"X-CSRF-Token": CSRF}).get_json()
    one.put(answer["url"], data=b"x", headers=answer["headers"])
    response = two.post(f"/s/{token}/api/uploads/{answer['upload_id']}/done", json={}, headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 404


def test_upload_links_are_for_folders_and_a_viewer_link_takes_no_uploads(app):
    maria = browser(app, make_user(app))
    file_id = upload(maria, "a.txt")["item"]["id"]
    setup_link(app, maria, file_id)
    response = post(maria, f"/items/{file_id}/share", {"action": "link_settings", "link_role": "uploader",
                                                       "password_action": "keep"}, follow_redirects=True)
    assert "Upload links are for folders" in text(response)
    folder = new_folder(maria, "Box")
    token = setup_link(app, maria, folder)
    visitor = browser(app)
    assert link_upload(visitor, token).get("status") == 404


def test_the_no_javascript_way_for_an_upload_link(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Box")
    token = setup_link(app, maria, folder, link_role="uploader")
    visitor = browser(app)
    response = post(visitor, f"/s/{token}/upload", {"files": [(io.BytesIO(b"hi"), "hi.txt")]},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Sent hi.txt." in text(response)


def test_upload_links_use_the_owners_space_and_a_password_too(app, ctx):
    from jdrive.models import User
    owner_id = make_user(app)
    ctx.get(User, owner_id).quota_bytes = 3
    ctx.commit()
    maria = browser(app, owner_id)
    folder = new_folder(maria, "Box")
    token = setup_link(app, maria, folder, link_role="uploader", password_action="set", password="secret")
    visitor = browser(app)
    assert link_upload(visitor, token).get("status") == 404  # locked
    post(visitor, f"/s/{token}/unlock", {"password": "secret"})
    answer = link_upload(visitor, token, data=b"too big")
    assert not answer["ok"] and "isn't enough space" in answer["error"]
