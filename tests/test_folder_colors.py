"""Folder colours: chosen when a folder is made, changed later from the ⋯ menu."""
from sqlalchemy import select

from jdrive import db, files
from jdrive.models import Item

from .conftest import browser, get_item, make_user, new_folder, post, text, upload
from .test_demo import start_demo
from .test_sharing import share


def make_folder(client, name="Photos", color=None, parent_id=None):
    data = {"name": name, "parent_id": parent_id or ""}
    if color is not None:
        data["color"] = color
    return post(client, "/folders/new", data)


def newest_folder(app):
    with app.app_context():
        return db.session().scalar(select(Item).where(Item.kind == "folder").order_by(Item.id.desc()).limit(1))


def test_the_new_folder_panel_offers_the_colors(app):
    maria = browser(app, make_user(app))
    page = text(maria.get("/drive"))
    assert page.count('type="radio" name="color"') == len(files.FOLDER_COLORS) == 9
    assert 'value="violet" checked' in page  # the usual colour is chosen first
    assert '<legend class="field-label">Color</legend>' in page


def test_a_new_folder_gets_the_chosen_color(app):
    maria = browser(app, make_user(app))
    make_folder(maria, "Photos", "blue")
    assert newest_folder(app).color == "blue"
    page = text(maria.get("/drive"))
    assert 'class="type-icon type-folder folder-blue"' in page
    assert 'class="file-card-art type-folder folder-blue"' in text(maria.get("/drive?view=grid"))


def test_violet_or_no_choice_is_the_usual_folder(app):
    maria = browser(app, make_user(app))
    make_folder(maria, "Plain")  # without JavaScript or an old page: no colour sent
    assert newest_folder(app).color is None
    make_folder(maria, "Violet", "violet")
    assert newest_folder(app).color is None
    page = text(maria.get("/drive"))
    assert page.count('class="type-icon type-folder"') == 2  # both plain: no folder-* class


def test_a_made_up_color_is_refused(app):
    maria = browser(app, make_user(app))
    # Only names from the list: a colour goes into a class name, never into a style.
    response = post(maria, "/folders/new", {"name": "Odd", "parent_id": "", "color": "url(evil)"},
                    follow_redirects=True)
    assert "Choose one of the folder colors." in text(response)
    assert newest_folder(app) is None


def test_the_menu_changes_a_folders_color(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Photos")
    page = text(maria.get("/drive"))
    assert f'action="/items/{folder}/color"' in page
    assert "Folder color" in page
    response = post(maria, f"/items/{folder}/color", {"color": "green", "next": "/drive"})
    assert response.headers["Location"].endswith("/drive")
    assert get_item(app, folder).color == "green"
    assert "Photos is green now." in text(maria.get("/drive"))
    assert 'value="green" class="swatch folder-green is-on"' in text(maria.get("/drive"))
    post(maria, f"/items/{folder}/color", {"color": "violet"})
    assert get_item(app, folder).color is None


def test_files_have_no_color(app):
    maria = browser(app, make_user(app))
    upload(maria, "notes.txt")
    with app.app_context():
        file_id = db.session().scalar(select(Item.id).where(Item.kind == "file"))
    assert post(maria, f"/items/{file_id}/color", {"color": "red"}).status_code == 404
    assert f'action="/items/{file_id}/color"' not in text(maria.get("/drive"))


def test_an_editor_can_change_the_color_but_a_viewer_cannot(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Cruz")
    carlo_id = make_user(app, "Carlo Reyes")
    top = new_folder(maria, "Team")
    inside = new_folder(maria, "Drafts", parent_id=top)
    share(maria, top, "ana@example.com", "editor")
    share(maria, top, "carlo@example.com", "viewer")
    ana, carlo = browser(app, ana_id), browser(app, carlo_id)

    assert post(ana, f"/items/{inside}/color", {"color": "pink"}).status_code == 302
    assert get_item(app, inside).color == "pink"
    assert post(carlo, f"/items/{inside}/color", {"color": "grey"}).status_code == 403
    assert get_item(app, inside).color == "pink"
    # The viewer sees the colour, but no swatches to change it.
    page = text(carlo.get(f"/folders/{top}"))
    assert "folder-pink" in page and "Folder color" not in page
    # Someone with no access can't even learn the folder is there.
    stranger = browser(app, make_user(app, "Lito Ramos"))
    assert post(stranger, f"/items/{inside}/color", {"color": "red"}).status_code == 404


def test_the_move_page_shows_folder_colors(app):
    maria = browser(app, make_user(app))
    make_folder(maria, "Work", "orange")
    upload(maria, "notes.txt")
    with app.app_context():
        file_id = db.session().scalar(select(Item.id).where(Item.kind == "file"))
    assert "type-folder folder-orange" in text(maria.get(f"/items/{file_id}/move"))


def test_the_demo_has_colored_folders(app):
    demo = start_demo(app)
    page = text(demo.get("/drive"))
    for color in ("orange", "green", "blue"):
        assert f"folder-{color}" in page
    assert "folder-red" in text(demo.get("/shared?view=grid"))  # Ana's Family Recipes
