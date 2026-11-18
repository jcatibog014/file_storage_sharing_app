"""The UI review: clearer words, warnings before it's too late, and quicker ways around."""
import re

from jdrive.models import User

from .conftest import browser, make_user, new_folder, post, text, upload, words


def test_the_storage_bar_turns_amber_then_red(app, ctx):
    user_id = make_user(app)
    client = browser(app, user_id)
    upload(client, "a.bin", b"x" * 50)
    for limit, state in ((1000, None), (64, "is-warning"), (52, "is-full")):
        ctx.get(User, user_id).quota_bytes = limit
        ctx.commit()
        page = client.get("/drive").get_data(as_text=True)
        bar = re.search(r'<div class="space([^"]*)">', page).group(1)
        if state:
            assert state in bar, (limit, bar)
        else:
            assert "is-warning" not in bar and "is-full" not in bar


def test_roles_explain_themselves(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Team")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "viewer"})
    ana = browser(app, ana_id)
    assert 'title="Viewer: can open and download, but not change anything"' in text(ana.get("/shared"))
    assert 'title="Viewer: can open and download' in text(ana.get(f"/folders/{folder}"))


def test_trash_words_and_a_safe_gap(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "old.txt")["item"]["id"]
    post(client, f"/items/{file_id}/trash")
    page = text(client.get("/trash"))
    assert "Auto-Deletes In" in page and "Delete all for good" in page
    assert 'data-confirm-ok="Delete for good"' in page and 'data-confirm-ok="Delete all for good"' in page
    assert "Empty trash" not in page


def test_settings_words(app):
    page = text(browser(app, make_user(app)).get("/settings"))
    for wording in ("Two-Step Sign-In (2FA)", "Enable 2FA", "View activity log"):
        assert wording in page, wording


def test_the_theme_is_in_the_account_menu_too(app):
    page = browser(app, make_user(app)).get("/drive").get_data(as_text=True)
    menu = page.split('class="user-menu-items"')[1]
    assert 'name="menu-theme" value="dark"' in menu
    assert page.count("data-theme-settings") == 1  # Settings has its own on its own page


def test_breadcrumbs_start_at_home_and_locations_are_links(app):
    client = browser(app, make_user(app))
    folder = new_folder(client, "Thesis")
    file_id = upload(client, "notes.txt", parent_id=folder)["item"]["id"]
    assert 'class="crumb-home"' in text(client.get(f"/files/{file_id}"))
    client.get(f"/files/{file_id}")
    recent = text(client.get("/recent"))
    assert f'<a class="where-link" href="/folders/{folder}">Thesis</a>' in recent


def test_room_to_drop_more_files(app):
    client = browser(app, make_user(app))
    upload(client, "a.txt")
    assert "Drag and drop files here to upload" in words(client.get("/drive"))


def test_what_javascript_draws_follows_the_theme(app):
    from pathlib import Path
    static = Path(app.static_folder)
    script = (static / "app.js").read_text(encoding="utf-8")
    style = (static / "style.css").read_text(encoding="utf-8")
    assert "function setUpTips()" in script and ".tip {" in style  # our tooltips, not the browser's box
    assert "upload-panel-title" in script and "didn't upload" in script
    assert ".button-danger-solid { background: #b42318;" in style  # readable in dark mode too
    page = browser(app, make_user(app)).get("/drive").get_data(as_text=True)
    assert 'content="#110c19" media="(prefers-color-scheme: dark)"' in page
