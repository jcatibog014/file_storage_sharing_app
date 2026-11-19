""""Can edit" links: a visitor opens the folder, and adds, renames and moves things to the Trash."""
import io

from sqlalchemy import select

from jdrive import db, links
from jdrive.models import Item, Notification

from .conftest import CSRF, browser, get_item, make_user, new_folder, post, text, upload
from .test_link_controls import setup_link, the_link


def edit_upload(client, token, parent_id=None, name="hello.txt", data=b"hello"):
    started = client.post(f"/s/{token}/api/uploads",
                          json={"name": name, "size": len(data), "type": "text/plain", "parent_id": parent_id or ""},
                          headers={"X-CSRF-Token": CSRF})
    answer = started.get_json()
    if not answer or not answer.get("ok"):
        return answer or {"status": started.status_code}
    client.put(answer["url"], data=data, headers=answer["headers"])
    return client.post(f"/s/{token}/api/uploads/{answer['upload_id']}/done", json={},
                       headers={"X-CSRF-Token": CSRF}).get_json()


def item_named(app, name):
    with app.app_context():
        return db.session().scalar(select(Item).where(Item.name == name))


def a_shared_folder(app):
    """Maria's Team folder (with Drafts and plan.txt inside), shared with a Can edit link."""
    maria = browser(app, make_user(app))
    team = new_folder(maria, "Team")
    drafts = new_folder(maria, "Drafts", parent_id=team)
    upload(maria, "plan.txt", parent_id=team)
    token = setup_link(app, maria, team, link_role="editor")
    return maria, team, drafts, token


def test_the_share_page_offers_three_kinds_of_link(app):
    maria, team, _drafts, _token = a_shared_folder(app)
    page = text(maria.get(f"/items/{team}/share"))
    for label in ("Can view", "Can upload", "Can edit"):
        assert f"<strong>{label}</strong>" in page
    assert links.LINK_ROLE_HELP["editor"] in page
    assert "Anyone with the link can edit" in page
    assert the_link(app, team).role == "editor"


def test_edit_links_are_for_folders(app):
    maria = browser(app, make_user(app))
    file_id = upload(maria, "a.txt")["item"]["id"]
    setup_link(app, maria, file_id)
    response = post(maria, f"/items/{file_id}/share", {"action": "link_settings", "link_role": "editor",
                                                       "password_action": "keep"}, follow_redirects=True)
    assert "Edit links are for folders" in text(response)


def test_a_visitor_sees_the_folder_and_the_edit_buttons(app):
    _maria, _team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    page = text(visitor.get(f"/s/{token}"))
    assert "plan.txt" in page and "Drafts" in page
    assert "You can add, rename and remove things here." in page
    assert f'action="/s/{token}/folders/new"' in page and 'data-upload' in page and "Move to trash" in page
    assert f'href="/s/{token}/rename/{drafts}"' in page
    # Inside a folder too, and uploads there go into that folder.
    inner = text(visitor.get(f"/s/{token}/i/{drafts}"))
    assert f'data-parent="{drafts}"' in inner and "Drop files here, or press Upload." in inner


def test_a_view_link_has_no_edit_buttons(app):
    maria = browser(app, make_user(app))
    team = new_folder(maria, "Team")
    upload(maria, "plan.txt", parent_id=team)
    token = setup_link(app, maria, team)
    page = text(browser(app).get(f"/s/{token}"))
    assert "plan.txt" in page and "Move to trash" not in page and "data-upload" not in page
    plan = item_named(app, "plan.txt")
    visitor = browser(app)
    assert post(visitor, f"/s/{token}/trash/{plan.id}").status_code == 404
    assert post(visitor, f"/s/{token}/folders/new", {"name": "x"}).status_code == 404
    assert edit_upload(visitor, token).get("status") == 404


def test_a_visitor_uploads_into_any_folder_inside(app):
    _maria, team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    assert edit_upload(visitor, token, name="top.txt")["ok"]
    assert edit_upload(visitor, token, parent_id=drafts, name="inner.txt")["ok"]
    assert item_named(app, "top.txt").parent_id == team
    inner = item_named(app, "inner.txt")
    assert inner.parent_id == drafts and inner.created_by_id is None
    assert inner.owner_id == get_item(app, team).owner_id  # the owner's space
    with app.app_context():
        words = list(db.session().scalars(select(Notification.text)))
    assert "Someone sent inner.txt into Drafts." in words
    # Not into a folder outside the link.
    elsewhere = new_folder(_maria, "Private")
    assert edit_upload(visitor, token, parent_id=elsewhere).get("status") == 404


def test_the_no_javascript_upload_goes_into_the_folder_on_screen(app):
    _maria, _team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    response = post(visitor, f"/s/{token}/upload", {"parent_id": drafts, "files": [(io.BytesIO(b"hi"), "hi.txt")]},
                    content_type="multipart/form-data")
    assert response.headers["Location"].endswith(f"/s/{token}/i/{drafts}")
    assert item_named(app, "hi.txt").parent_id == drafts
    assert "Uploaded hi.txt." in text(visitor.get(f"/s/{token}/i/{drafts}"))


def test_a_visitor_makes_a_folder_with_a_color(app):
    _maria, team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    response = post(visitor, f"/s/{token}/folders/new", {"name": "Ideas", "parent_id": drafts, "color": "green"})
    assert response.headers["Location"].endswith(f"/s/{token}/i/{drafts}")
    ideas = item_named(app, "Ideas")
    assert ideas.parent_id == drafts and ideas.color == "green" and ideas.created_by_id is None
    assert "folder-green" in text(visitor.get(f"/s/{token}/i/{drafts}"))


def test_a_visitor_renames_but_not_the_shared_folder_itself(app):
    _maria, team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    assert 'value="Drafts"' in text(visitor.get(f"/s/{token}/rename/{drafts}"))
    response = post(visitor, f"/s/{token}/rename/{drafts}", {"name": "Old drafts"})
    assert response.headers["Location"].endswith(f"/s/{token}")
    assert get_item(app, drafts).name == "Old drafts"
    assert visitor.get(f"/s/{token}/rename/{team}").status_code == 403
    assert post(visitor, f"/s/{token}/rename/{team}", {"name": "Mine now"}).status_code == 403
    assert get_item(app, team).name == "Team"
    empty = post(visitor, f"/s/{token}/rename/{drafts}", {"name": "  "})
    assert empty.status_code == 400 and "Give it a name." in text(empty)


def test_a_visitor_moves_things_to_the_owners_trash(app):
    maria, team, drafts, token = a_shared_folder(app)
    visitor = browser(app)
    plan = item_named(app, "plan.txt")
    response = post(visitor, f"/s/{token}/trash/{plan.id}", follow_redirects=True)
    assert "Moved plan.txt to the Trash. The owner can bring it back." in text(response)
    trashed = get_item(app, plan.id)
    assert trashed.trashed_at is not None and trashed.trashed_by_id is None
    assert "plan.txt" not in text(visitor.get(f"/s/{token}"))
    assert visitor.get(f"/s/{token}/i/{plan.id}").status_code == 404
    # The owner finds it in their Trash, and is told.
    assert "plan.txt" in text(maria.get("/trash"))
    assert "Someone with the link moved plan.txt to the Trash." in text(maria.get("/notifications"))
    # Never the shared folder itself, and nothing outside the link.
    assert post(visitor, f"/s/{token}/trash/{team}").status_code == 403
    private = upload(maria, "private.txt")["item"]["id"]
    assert post(visitor, f"/s/{token}/trash/{private}").status_code == 404
    assert get_item(app, private).trashed_at is None


def test_edits_need_the_password_too(app):
    maria = browser(app, make_user(app))
    team = new_folder(maria, "Team")
    drafts = new_folder(maria, "Drafts", parent_id=team)
    token = setup_link(app, maria, team, link_role="editor", password_action="set", password="secret")
    visitor = browser(app)
    assert post(visitor, f"/s/{token}/trash/{drafts}").status_code == 404
    assert post(visitor, f"/s/{token}/folders/new", {"name": "x"}).status_code == 404
    post(visitor, f"/s/{token}/unlock", {"password": "secret"})
    assert post(visitor, f"/s/{token}/folders/new", {"name": "x"}).status_code == 302
    assert item_named(app, "x").parent_id == team


def test_edits_through_a_link_are_limited(app, monkeypatch):
    from jdrive.views import public
    monkeypatch.setattr(public, "EDITS_PER_HOUR", 2)
    _maria, _team, _drafts, token = a_shared_folder(app)
    visitor = browser(app)
    for name in ("one", "two"):
        post(visitor, f"/s/{token}/folders/new", {"name": name})
    response = post(visitor, f"/s/{token}/folders/new", {"name": "three"}, follow_redirects=True)
    assert "That's a lot of changes for one hour." in text(response)
    assert item_named(app, "three") is None


def test_the_demo_has_an_edit_link(app):
    from .test_demo import start_demo
    demo = start_demo(app)
    with app.app_context():
        work = db.session().scalar(select(Item).where(Item.name == "Work").order_by(Item.id.desc()))
    page = text(demo.get(f"/items/{work.id}/share"))
    assert "Anyone with the link can edit" in page
