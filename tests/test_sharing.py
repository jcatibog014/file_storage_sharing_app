"""Sharing by email (waiting invites are only claimed by a proven address) and links."""
from sqlalchemy import select

from jdrive import db, links, mailer
from jdrive.models import Share, ShareLink

from .conftest import browser, code_sent_to, get_item, make_user, new_folder, post, sign_up, text, upload


def share(client, item_id, emails, role="viewer", **extra):
    return post(client, f"/items/{item_id}/share", {"action": "add", "emails": emails, "role": role, **extra})


def test_sharing_with_an_account_gives_access_and_an_email(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Photos")
    response = share(maria, folder, "ana@example.com", "editor", note="From the trip!")
    assert response.status_code == 302
    email = mailer.last_to("ana@example.com", app)
    assert 'Maria Santos shared "Photos" with you' == email["Subject"]
    body = email.get_content()
    assert "You are an Editor." in body and "From the trip!" in body
    assert f"/folders/{folder}" in body
    ana = browser(app, ana_id)
    assert ana.get(f"/folders/{folder}").status_code == 200
    assert "Photos" in text(ana.get("/shared"))


def test_an_email_without_an_account_waits_until_it_is_proven(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Wedding")
    share(maria, folder, "newbie@example.com", "viewer")
    with app.app_context():
        waiting = db.session().scalar(select(Share).where(Share.email == "newbie@example.com"))
        assert waiting.user_id is None
    invite = mailer.last_to("newbie@example.com", app).get_content()
    assert "sign up with this email address" in invite and "/signup?email=newbie@example.com" in invite
    assert "Invited, hasn't signed up yet" in text(maria.get(f"/items/{folder}/share"))

    # Someone else types that address at sign-up but can't read the inbox: nothing.
    squatter = browser(app)
    sign_up(squatter, name="Not Them", email="newbie@example.com", verify=False)
    assert squatter.get(f"/folders/{folder}").status_code == 302  # not logged in at all
    with app.app_context():
        assert db.session().scalar(select(Share.user_id).where(Share.email == "newbie@example.com")) is None

    # The real owner of the inbox signs up and types the code: the folder is theirs to open.
    newbie = browser(app)
    sign_up(newbie, name="New Person", email="newbie@example.com")
    assert newbie.get(f"/folders/{folder}").status_code == 200
    assert "Wedding" in text(newbie.get("/shared"))


def test_a_share_covers_everything_inside(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    top = new_folder(maria, "Top")
    deep = new_folder(maria, "Deep", new_folder(maria, "Middle", top))
    file_id = upload(maria, "deep.txt", parent_id=deep)["item"]["id"]
    share(maria, top, "ana@example.com")
    ana = browser(app, ana_id)
    assert ana.get(f"/files/{file_id}").status_code == 200
    assert ana.get(f"/files/{file_id}/download").status_code == 302


def test_share_checks_what_was_typed(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    assert "doesn't look like an email" in text(share(maria, folder, "not-an-email"))
    assert "Type at least one email" in text(share(maria, folder, " , "))
    assert "Choose Viewer" in text(share(maria, folder, "a@example.com", "owner"))


def test_changing_a_role(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "X")
    share(maria, folder, "ana@example.com", "viewer")
    with app.app_context():
        share_id = db.session().scalar(select(Share.id))
    post(maria, f"/items/{folder}/share", {"action": "role", "share_id": share_id, "role": "editor"})
    assert post(browser(app, ana_id), "/folders/new", {"parent_id": folder, "name": "ok"}).status_code == 302


def test_leave_something_shared_with_you(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "X")
    share(maria, folder, "ana@example.com")
    ana = browser(app, ana_id)
    post(ana, f"/items/{folder}/leave")
    assert ana.get(f"/folders/{folder}").status_code == 404


def test_make_someone_else_the_owner(app):
    maria_id = make_user(app)
    maria = browser(app, maria_id)
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Club")
    inner = new_folder(maria, "Minutes", folder)
    share(maria, folder, "ana@example.com", "editor")
    with app.app_context():
        share_id = db.session().scalar(select(Share.id))
    post(maria, f"/items/{folder}/share", {"action": "transfer", "share_id": share_id})
    assert get_item(app, folder).owner_id == ana_id and get_item(app, inner).owner_id == ana_id
    page = text(maria.get(f"/items/{folder}/share"))
    assert "Maria Santos (you)" in page  # still there, as Co-owner
    assert "Club" in text(browser(app, ana_id).get("/drive"))


def test_only_top_level_items_change_owner(app):
    maria = browser(app, make_user(app))
    make_user(app, "Ana Reyes")
    inner = new_folder(maria, "Inner", new_folder(maria, "Outer"))
    share(maria, inner, "ana@example.com")
    with app.app_context():
        share_id = db.session().scalar(select(Share.id))
    response = post(maria, f"/items/{inner}/share", {"action": "transfer", "share_id": share_id}, follow_redirects=True)
    assert "Move it to the top of My Drive first" in text(response)


# ---------- Links ----------

def turn_on_link(app, client, item_id):
    post(client, f"/items/{item_id}/share", {"action": "link_on"})
    with app.app_context():
        link = db.session().scalar(select(ShareLink).where(ShareLink.item_id == item_id))
        with app.test_request_context():
            return links.token_for(link)


def test_anyone_with_the_link_can_open_and_download(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Trip")
    inner = new_folder(maria, "Day 1", folder)
    file_id = upload(maria, "photo.jpg", b"jpeg!", parent_id=inner, content_type="image/jpeg")["item"]["id"]
    token = turn_on_link(app, maria, folder)
    visitor = app.test_client()
    page = visitor.get(f"/s/{token}")
    assert page.status_code == 200 and "Day 1" in text(page)
    assert page.headers["Referrer-Policy"] == "no-referrer" and page.headers["Cache-Control"] == "no-store"
    assert "noindex" in page.headers["X-Robots-Tag"]
    assert visitor.get(f"/s/{token}/i/{inner}").status_code == 200
    download = visitor.get(f"/s/{token}/d/{file_id}")
    assert download.status_code == 302 and visitor.get(download.headers["Location"]).data == b"jpeg!"
    # The normal pages still need an account and a share.
    assert visitor.get(f"/folders/{folder}").status_code == 302


def test_a_link_opens_its_own_tree_only(app):
    maria = browser(app, make_user(app))
    shared = new_folder(maria, "Shared")
    private_file = upload(maria, "private.txt")["item"]["id"]
    token = turn_on_link(app, maria, shared)
    visitor = app.test_client()
    assert visitor.get(f"/s/{token}/i/{private_file}").status_code == 404
    assert visitor.get(f"/s/{token}/d/{private_file}").status_code == 404


def test_the_token_is_never_stored_and_cant_be_guessed(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    token = turn_on_link(app, maria, folder)
    with app.app_context():
        link = db.session().scalar(select(ShareLink))
        assert token not in (link.token_hash, link.nonce)
    visitor = app.test_client()
    changed = token[:-1] + ("B" if token[-1] == "A" else "A")  # one letter different
    assert visitor.get(f"/s/{changed}").status_code == 404
    assert visitor.get("/s/" + "A" * 43).status_code == 404


def test_turning_off_and_making_a_new_link(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    old = turn_on_link(app, maria, folder)
    post(maria, f"/items/{folder}/share", {"action": "link_new"})
    with app.app_context(), app.test_request_context():
        new = links.token_for(db.session().scalar(select(ShareLink)))
    visitor = app.test_client()
    assert visitor.get(f"/s/{old}").status_code == 404
    assert visitor.get(f"/s/{new}").status_code == 200
    post(maria, f"/items/{folder}/share", {"action": "link_off"})
    assert visitor.get(f"/s/{new}").status_code == 404
    post(maria, f"/items/{folder}/share", {"action": "link_on"})
    assert visitor.get(f"/s/{new}").status_code == 200  # the same address comes back


def test_the_share_page_shows_the_link_and_counts_opens(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "X")
    token = turn_on_link(app, maria, folder)
    app.test_client().get(f"/s/{token}")
    page = text(maria.get(f"/items/{folder}/share"))
    assert f"/s/{token}" in page and "Opened 1 time" in page


def test_code_helper_still_works(app):
    sign_up(browser(app), email="x@example.com", verify=False)
    assert len(code_sent_to(app, "x@example.com")) == 6
