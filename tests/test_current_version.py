"""Make current: the chosen version gets the Current tag, and no version is added."""
import io
import zipfile

from sqlalchemy import select

from jdrive import db, quota
from jdrive.models import FileVersion

from .conftest import browser, get_item, make_user, new_folder, post, text, upload


def make_file_with_versions(client, count, name="report.txt", parent_id=None):
    file_id = upload(client, name, b"v1", parent_id=parent_id)["item"]["id"]
    for number in range(2, count + 1):
        upload(client, name, f"v{number}".encode() * number, target_id=file_id)
    return file_id


def version_ids(app, file_id):
    """{number: id}"""
    with app.app_context():
        rows = db.session().execute(select(FileVersion.number, FileVersion.id)
                                    .where(FileVersion.item_id == file_id)).all()
    return dict(rows)


def current_tag_on(page):
    """The version number shown with the Current tag."""
    before = page.split('<span class="badge badge-accent">Current</span>')[0]
    return int(before.rsplit("<strong>Version ", 1)[1].split("<", 1)[0])


def test_making_version_3_current_tags_version_3_and_adds_nothing(app):
    maria = browser(app, make_user(app))
    file_id = make_file_with_versions(maria, 6)
    ids = version_ids(app, file_id)
    response = post(maria, f"/files/{file_id}/versions/{ids[3]}/restore", follow_redirects=True)
    page = text(response)
    assert "Version 3 is the current version now." in page
    assert current_tag_on(page) == 3
    assert page.count("badge badge-accent\">Current") == 1
    assert sorted(version_ids(app, file_id)) == [1, 2, 3, 4, 5, 6]  # no Version 7
    assert page.count("<strong>Version ") == 6
    # The file is version 3 now: its size, and what a download or preview gives.
    assert get_item(app, file_id).size == len(b"v3" * 3)
    download = maria.get(maria.get(f"/files/{file_id}/download").headers["Location"])
    assert download.data == b"v3" * 3


def test_the_current_version_has_no_menu_and_the_newest_gets_one(app):
    maria = browser(app, make_user(app))
    file_id = make_file_with_versions(maria, 3)
    ids = version_ids(app, file_id)
    post(maria, f"/files/{file_id}/versions/{ids[1]}/restore")
    page = text(maria.get(f"/files/{file_id}"))
    assert f'/versions/{ids[1]}/restore' not in page        # it is current already
    assert f'/versions/{ids[3]}/restore' in page            # the newest can be made current again
    post(maria, f"/files/{file_id}/versions/{ids[3]}/restore")
    assert get_item(app, file_id).current_version_id is None  # back to "the newest"
    assert current_tag_on(text(maria.get(f"/files/{file_id}"))) == 3


def test_a_new_upload_is_current_again(app):
    maria = browser(app, make_user(app))
    file_id = make_file_with_versions(maria, 3)
    ids = version_ids(app, file_id)
    post(maria, f"/files/{file_id}/versions/{ids[2]}/restore")
    upload(maria, "report.txt", b"brand new", target_id=file_id)
    assert sorted(version_ids(app, file_id)) == [1, 2, 3, 4]
    item = get_item(app, file_id)
    assert item.current_version_id is None and item.size == len(b"brand new")
    assert current_tag_on(text(maria.get(f"/files/{file_id}"))) == 4


def test_making_an_old_version_current_takes_no_space(app):
    owner_id = make_user(app)
    maria = browser(app, owner_id)
    file_id = make_file_with_versions(maria, 3)
    ids = version_ids(app, file_id)
    with app.app_context():
        before = quota.used(db.session(), owner_id)
    post(maria, f"/files/{file_id}/versions/{ids[1]}/restore")
    with app.app_context():
        assert quota.used(db.session(), owner_id) == before


def test_a_zip_has_the_current_version(app):
    maria = browser(app, make_user(app))
    folder = new_folder(maria, "Reports")
    file_id = make_file_with_versions(maria, 3, parent_id=folder)
    ids = version_ids(app, file_id)
    post(maria, f"/files/{file_id}/versions/{ids[1]}/restore")
    archive = zipfile.ZipFile(io.BytesIO(maria.get(f"/folders/{folder}/zip").data))
    assert archive.read("report.txt") == b"v1"


def test_deleting_the_newest_keeps_the_chosen_one_current(app):
    maria = browser(app, make_user(app))
    file_id = make_file_with_versions(maria, 3)
    ids = version_ids(app, file_id)
    post(maria, f"/files/{file_id}/versions/{ids[2]}/restore")
    post(maria, f"/files/{file_id}/versions/{ids[3]}/delete")
    assert sorted(version_ids(app, file_id)) == [1, 2]
    assert current_tag_on(text(maria.get(f"/files/{file_id}"))) == 2
