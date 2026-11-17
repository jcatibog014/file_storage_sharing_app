"""The phone review: a smaller header, one view button, and clearer lists on small screens."""
from .conftest import CSRF, browser, make_user, new_folder, post, text, upload


def test_the_menu_button_is_just_the_icon(app):
    page = browser(app, make_user(app)).get("/drive").get_data(as_text=True)
    button = page.split('class="menu-button"')[1].split("</button>")[0]
    assert 'aria-label="Menu"' in button and "Menu\n" not in button.split(">", 1)[1]


def test_one_button_switches_list_and_grid(app):
    client = browser(app, make_user(app))
    upload(client, "a.txt")
    page = text(client.get("/drive?view=list"))
    assert page.count('class="icon-button view-toggle"') == 1
    assert "view=grid" in page.split('class="icon-button view-toggle"')[1].split(">")[0]
    page = text(client.get("/drive?view=grid"))
    assert "view=list" in page.split('class="icon-button view-toggle"')[1].split(">")[0]


def test_the_drop_box_says_tap_on_a_phone(app):
    client = browser(app, make_user(app))
    upload(client, "a.txt")
    page = text(client.get("/drive"))
    assert '<label class="drop-more js-only" for="upload-input">' in page
    assert "Tap to upload files" in page and "Drag and drop files here to upload" in page


def test_shared_with_me_has_a_grid_a_who_line_and_a_footer(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Team")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "editor"})
    ana = browser(app, ana_id)
    page = text(ana.get("/shared?view=list"))
    assert "Shared by Maria Santos &middot; Editor" in ana.get("/shared").get_data(as_text=True)
    assert "1 item shared with you" in page
    grid = text(ana.get("/shared?view=grid"))
    assert 'class="file-grid"' in grid and "Maria" in grid and "Editor" in grid


def test_the_demo_tag_explains_itself(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    post(client, "/demo")
    page = text(client.get("/drive"))
    assert "You are in a demo workspace. Everything here is deleted after 24 hours." in page
