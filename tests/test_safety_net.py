"""The Trash, file versions, storage space, and the timer."""
from datetime import timedelta

from sqlalchemy import func, select

from jdrive import db, files, quota, tick
from jdrive.dates import utcnow
from jdrive.models import Blob, FileVersion, Item, User

from .conftest import CSRF, browser, get_item, make_user, new_folder, post, sign_up, text, upload


def blob_count(app):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(Blob))


# ---------- The Trash ----------

def test_trash_hides_everything_inside_and_restore_brings_it_back(app):
    client = browser(app, make_user(app))
    folder = new_folder(client, "Old Stuff")
    file_id = upload(client, "inside.txt", parent_id=folder)["item"]["id"]
    response = post(client, f"/items/{folder}/trash", follow_redirects=True)
    assert "Moved Old Stuff to the Trash" in text(response)
    assert "Old Stuff" not in text(client.get("/drive"))
    for url in (f"/folders/{folder}", f"/files/{file_id}", f"/files/{file_id}/download"):
        assert client.get(url).status_code == 404, url
    page = text(client.get("/trash"))
    assert "Old Stuff" in page and "30 days" in page and "inside.txt" not in page  # only the top item is listed
    post(client, f"/items/{folder}/restore")
    assert client.get(f"/files/{file_id}").status_code == 200
    assert get_item(app, folder).trashed_at is None


def test_only_the_trashed_item_has_trash_buttons(app):
    client = browser(app, make_user(app))
    folder = new_folder(client, "Box")
    file_id = upload(client, "inside.txt", parent_id=folder)["item"]["id"]
    post(client, f"/items/{folder}/trash")
    assert post(client, f"/items/{file_id}/restore").status_code == 404
    assert post(client, f"/items/{file_id}/delete").status_code == 404
    # And delete-for-good only works on something in the Trash.
    other = upload(client, "keep.txt")["item"]["id"]
    assert post(client, f"/items/{other}/delete").status_code == 404


def test_restoring_when_the_folder_is_gone_goes_to_the_top(app):
    client = browser(app, make_user(app))
    folder = new_folder(client, "Box")
    file_id = upload(client, "a.txt", parent_id=folder)["item"]["id"]
    post(client, f"/items/{file_id}/trash")
    post(client, f"/items/{folder}/trash")
    response = post(client, f"/items/{file_id}/restore", follow_redirects=True)
    assert "because its folder is gone" in text(response)
    assert get_item(app, file_id).parent_id is None


def test_restoring_a_name_that_is_taken_again(app):
    client = browser(app, make_user(app))
    first = upload(client, "a.txt")["item"]["id"]
    post(client, f"/items/{first}/trash")
    upload(client, "a.txt")  # the name is free while the first one is in the Trash
    post(client, f"/items/{first}/restore")
    assert get_item(app, first).name == "a (1).txt"


def test_an_editor_can_trash_and_undo_but_not_delete_for_good(app):
    maria = browser(app, make_user(app))
    ben_id = make_user(app, "Ben Torres")
    folder = new_folder(maria, "Team")
    file_id = upload(maria, "plan.txt", parent_id=folder)["item"]["id"]
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ben@example.com", "role": "editor"})
    ben = browser(app, ben_id)
    assert post(ben, f"/items/{file_id}/trash").status_code == 302
    assert "plan.txt" in text(ben.get("/trash"))       # Ben sees what he put there
    assert "plan.txt" in text(maria.get("/trash"))     # and so does the owner
    assert post(ben, f"/items/{file_id}/delete").status_code == 403
    assert post(ben, f"/items/{file_id}/restore").status_code == 302


def test_a_shared_folder_in_the_trash_is_hidden_from_people_and_links(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Shared")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "viewer"})
    post(maria, f"/items/{folder}/share", {"action": "link_on"})
    from jdrive import links
    from jdrive.models import ShareLink
    with app.app_context(), app.test_request_context():
        token = links.token_for(db.session().scalar(select(ShareLink)))
    post(maria, f"/items/{folder}/trash")
    ana = browser(app, ana_id)
    assert ana.get(f"/folders/{folder}").status_code == 404
    assert "Nothing Shared Yet" in text(ana.get("/shared"))
    assert app.test_client().get(f"/s/{token}").status_code == 404


def test_empty_trash_and_the_30_day_purge(app, ctx):
    client = browser(app, make_user(app))
    old = upload(client, "old.txt", b"old")["item"]["id"]
    new = upload(client, "new.txt", b"new")["item"]["id"]
    post(client, f"/items/{old}/trash")
    post(client, f"/items/{new}/trash")
    ctx.get(Item, old).trashed_at = utcnow() - timedelta(days=31)
    ctx.commit()
    client.get("/trash")  # opening the Trash deletes your run-out things
    assert get_item(app, old) is None and get_item(app, new) is not None
    assert blob_count(app) == 1
    post(client, "/trash/empty")
    assert get_item(app, new) is None and blob_count(app) == 0


# ---------- Versions ----------

def test_a_new_version_keeps_the_old_one(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "essay.txt", b"first draft")["item"]["id"]
    answer = upload(client, "whatever.txt", b"second draft!", target_id=file_id)
    assert answer["ok"] and answer["item"]["id"] == file_id
    item = get_item(app, file_id)
    assert item.name == "essay.txt" and item.size == 13
    page = text(client.get(f"/files/{file_id}"))
    assert "Version 2" in page and "Version 1" in page and "Current" in page
    download = client.get(client.get(f"/files/{file_id}/download").headers["Location"])
    assert download.data == b"second draft!"


def test_download_and_restore_an_old_version(app, ctx):
    client = browser(app, make_user(app))
    file_id = upload(client, "essay.txt", b"first draft")["item"]["id"]
    upload(client, "x", b"second draft!", target_id=file_id)
    first = ctx.scalar(select(FileVersion).where(FileVersion.item_id == file_id, FileVersion.number == 1))
    link = client.get(f"/files/{file_id}/versions/{first.id}/download").headers["Location"]
    got = client.get(link)
    assert got.data == b"first draft" and 'essay (version 1).txt' in got.headers["Content-Disposition"]
    post(client, f"/files/{file_id}/versions/{first.id}/restore")
    versions = ctx.scalars(select(FileVersion).where(FileVersion.item_id == file_id)
                           .order_by(FileVersion.number)).all()
    assert [v.number for v in versions] == [1, 2, 3]
    assert versions[2].blob_id == versions[0].blob_id  # nothing copied
    assert blob_count(app) == 2


def test_only_the_newest_ten_versions_are_kept(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "log.txt", b"v1")["item"]["id"]
    for number in range(2, 13):
        upload(client, "log.txt", f"v{number}".encode(), target_id=file_id)
    with app.app_context():
        numbers = db.session().scalars(select(FileVersion.number).where(FileVersion.item_id == file_id)).all()
    assert sorted(numbers) == list(range(3, 13))
    assert blob_count(app) == 10  # the two oldest objects were deleted too


def test_deleting_a_version_needs_a_coowner_and_not_the_current_one(app, ctx):
    maria = browser(app, make_user(app))
    ben_id = make_user(app, "Ben Torres")
    folder = new_folder(maria, "Team")
    file_id = upload(maria, "a.txt", b"one", parent_id=folder)["item"]["id"]
    upload(maria, "a.txt", b"two", target_id=file_id)
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ben@example.com", "role": "editor"})
    old, _second = ctx.scalars(select(FileVersion).where(FileVersion.item_id == file_id)
                               .order_by(FileVersion.number)).all()
    ben = browser(app, ben_id)
    assert post(ben, f"/files/{file_id}/versions/{old.id}/delete").status_code == 403
    assert post(ben, f"/files/{file_id}/versions/{old.id}/restore").status_code == 302  # Editors can restore
    newest = ctx.scalar(select(FileVersion).where(FileVersion.item_id == file_id).order_by(FileVersion.number.desc()))
    assert newest.number == 3
    response = post(maria, f"/files/{file_id}/versions/{newest.id}/delete", follow_redirects=True)
    assert "current version" in text(response)


def test_a_version_of_another_file_is_404(app, ctx):
    client = browser(app, make_user(app))
    a = upload(client, "a.txt")["item"]["id"]
    b = upload(client, "b.txt")["item"]["id"]
    version_of_b = ctx.scalar(select(FileVersion.id).where(FileVersion.item_id == b))
    assert client.get(f"/files/{a}/versions/{version_of_b}/download").status_code == 404


# ---------- Storage space ----------

def test_uploads_stop_at_the_space_limit(app, ctx):
    user_id = make_user(app)
    ctx.get(User, user_id).quota_bytes = 10
    ctx.commit()
    client = browser(app, user_id)
    assert upload(client, "a.txt", b"123456")["ok"]
    answer = upload(client, "b.txt", b"123456")
    assert not answer["ok"] and "isn't enough space" in answer["error"] and "4 bytes left" in answer["error"]
    assert "6 bytes of 10 bytes used" in text(client.get("/drive"))


def test_trash_and_versions_count_and_waiting_uploads_too(app, ctx):
    client = browser(app, make_user(app))
    user_id = get_item(app, upload(client, "a.txt", b"12345")["item"]["id"]).owner_id
    upload(client, "a.txt", b"123", target_id=1)
    assert quota.used(ctx, user_id) == 8
    client.post("/api/uploads", json={"name": "big.bin", "size": 100, "type": ""}, headers={"X-CSRF-Token": CSRF})
    assert quota.used(ctx, user_id) == 108


def test_an_editors_upload_uses_the_owners_space(app, ctx):
    maria_id = make_user(app)
    ctx.get(User, maria_id).quota_bytes = 5
    ctx.commit()
    maria = browser(app, maria_id)
    ben_id = make_user(app, "Ben Torres")
    folder = new_folder(maria, "Team")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ben@example.com", "role": "editor"})
    answer = upload(browser(app, ben_id), "big.txt", b"123456", parent_id=folder)
    assert not answer["ok"] and "Maria's drive" in answer["error"]


# ---------- The timer ----------

def test_the_timer_needs_its_key(app):
    client = app.test_client()
    assert client.post("/tasks/tick").status_code == 404
    app.config["TICK_KEY"] = "s3cret"
    assert client.post("/tasks/tick", headers={"X-Tick-Key": "wrong"}).status_code == 404
    answer = client.post("/tasks/tick", headers={"X-Tick-Key": "s3cret"}).get_json()
    assert set(answer) >= {"uploads_expired", "trash_deleted", "objects_swept", "unconfirmed_deleted"}


def test_the_timer_deletes_unconfirmed_sign_ups_after_a_day(app, ctx):
    sign_up(browser(app), email="never@example.com", verify=False)
    confirmed = make_user(app, "Maria Santos")
    tick.run(ctx, utcnow())
    assert ctx.scalar(select(func.count()).select_from(User)) == 2
    tick.run(ctx, utcnow() + timedelta(days=2))
    assert [user.id for user in ctx.scalars(select(User))] == [confirmed]


def test_the_timer_sweeps_objects_nothing_uses(app, ctx):
    bucket = app.extensions["jdrive_bucket"]
    from jdrive import storage
    key = storage.new_key()
    bucket.put_bytes(key, b"left over", "text/plain")
    ctx.add(Blob(key=key, size=9, content_type="text/plain", state="ready", created_at=utcnow()))
    ctx.commit()
    assert tick.run(ctx, utcnow())["objects_swept"] == 1
    assert bucket.head(key) is None
    assert files.sweep_blobs(ctx) == 0
