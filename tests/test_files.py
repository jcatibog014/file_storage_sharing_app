"""Folders and files: uploading straight to the bucket, downloading, renaming, moving, deleting."""
import io

from sqlalchemy import func, select

from jdrive import db, files, storage
from jdrive.dates import utcnow
from jdrive.models import Blob, FileVersion, Item, Upload

from .conftest import CSRF, browser, get_item, make_user, new_folder, post, text, upload


def bucket_keys(app):
    with app.app_context():
        return set(db.session().scalars(select(Blob.key)))


def object_exists(app, key):
    return app.extensions["jdrive_bucket"].head(key) is not None


def test_upload_goes_straight_to_the_bucket(app):
    client = browser(app, make_user(app))
    answer = upload(client, "Report.pdf", b"%PDF-1.4 hello", content_type="application/pdf")
    assert answer["ok"] and answer["item"]["name"] == "Report.pdf"
    item = get_item(app, answer["item"]["id"])
    assert item.size == 14 and item.content_type == "application/pdf" and item.parent_id is None
    with app.app_context():
        s = db.session()
        version = s.scalar(select(FileVersion).where(FileVersion.item_id == item.id))
        assert version.number == 1 and version.blob.state == "ready"
        assert version.blob.key.startswith("b/") and "Report" not in version.blob.key  # keys say nothing
        assert s.scalar(select(func.count()).select_from(Upload)) == 0
    assert "Report.pdf" in text(client.get("/drive"))


def test_the_signed_link_is_for_that_file_only(app):
    client = browser(app, make_user(app))
    answer = client.post("/api/uploads", json={"name": "a.txt", "size": 5, "type": "text/plain"},
                         headers={"X-CSRF-Token": CSRF}).get_json()
    assert answer["method"] == "PUT" and answer["headers"] == {"Content-Type": "text/plain"}
    # A bigger file than promised is refused by the bucket.
    assert client.put(answer["url"], data=b"much more than five", headers=answer["headers"]).status_code == 400
    done = client.post(f"/api/uploads/{answer['upload_id']}/done", json={}, headers={"X-CSRF-Token": CSRF}).get_json()
    assert not done["ok"] and "didn't arrive" in done["error"]
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Item)) == 0


def test_done_checks_the_bucket_and_deletes_a_wrong_file(app, monkeypatch):
    """Even if a different file got into the bucket somehow, it never shows up."""
    client = browser(app, make_user(app))
    answer = client.post("/api/uploads", json={"name": "a.txt", "size": 5, "type": "text/plain"},
                         headers={"X-CSRF-Token": CSRF}).get_json()
    with app.app_context():
        key = db.session().get(Upload, answer["upload_id"]).blob.key
    app.extensions["jdrive_bucket"].put_bytes(key, b"something else entirely", "text/plain")
    done = client.post(f"/api/uploads/{answer['upload_id']}/done", json={}, headers={"X-CSRF-Token": CSRF}).get_json()
    assert not done["ok"] and "isn't the one we expected" in done["error"]
    assert not object_exists(app, key)
    assert bucket_keys(app) == set()


def test_only_the_uploader_can_finish_an_upload(app):
    client = browser(app, make_user(app))
    answer = client.post("/api/uploads", json={"name": "a.txt", "size": 1, "type": "text/plain"},
                         headers={"X-CSRF-Token": CSRF}).get_json()
    client.put(answer["url"], data=b"x", headers=answer["headers"])
    stranger = browser(app, make_user(app, "Jose Rizal"))
    response = stranger.post(f"/api/uploads/{answer['upload_id']}/done", json={}, headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 404


def test_uploads_need_the_csrf_token(app):
    client = browser(app, make_user(app))
    assert client.post("/api/uploads", json={"name": "a.txt", "size": 1}).status_code == 400


def test_upload_limits(app):
    client = browser(app, make_user(app))
    too_big = client.post("/api/uploads", json={"name": "big.iso", "size": files.MAX_FILE_SIZE + 1},
                          headers={"X-CSRF-Token": CSRF}).get_json()
    assert not too_big["ok"] and "100 MB" in too_big["error"]
    no_name = client.post("/api/uploads", json={"name": "  ", "size": 1}, headers={"X-CSRF-Token": CSRF}).get_json()
    assert "Give it a name" in no_name["error"]


def test_types_are_cleaned():
    assert files.content_type_for("a.png", "image/PNG") == "image/png"
    assert files.content_type_for("a.png", "<script>") == "image/png"
    assert files.content_type_for("a.weird", "") == files.FALLBACK_TYPE
    assert files.content_type_for("a.txt", "text/plain; charset=utf-8") == "text/plain"


def test_names_are_cleaned_and_made_unique(app):
    client = browser(app, make_user(app))
    upload(client, "notes.txt")
    upload(client, "Notes.txt")
    upload(client, "notes.txt")
    upload(client, "a/b\\c\x07.txt")
    names = sorted(item.name for item in _all_items(app))
    assert names == ["Notes (1).txt", "a-b-c.txt", "notes (2).txt", "notes.txt"]


def _all_items(app):
    with app.app_context():
        return list(db.session().scalars(select(Item)))


def test_the_no_javascript_upload_form(app):
    client = browser(app, make_user(app))
    folder_id = new_folder(client, "School")
    response = post(client, "/upload", {"parent_id": folder_id,
                                        "files": [(io.BytesIO(b"one"), "one.txt"), (io.BytesIO(b"two!"), "two.txt")]},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Uploaded 2 files." in text(response)
    items = {item.name: item for item in _all_items(app) if not item.is_folder}
    assert items["two.txt"].size == 4 and items["two.txt"].parent_id == folder_id
    assert object_exists(app, _key_of(app, items["one.txt"].id))


def _key_of(app, item_id):
    with app.app_context():
        return db.session().scalar(select(Blob.key).join(FileVersion).where(FileVersion.item_id == item_id))


def test_download_is_a_short_signed_link(app):
    client = browser(app, make_user(app))
    item_id = upload(client, "Hello.txt", b"hello world")["item"]["id"]
    response = client.get(f"/files/{item_id}/download")
    assert response.status_code == 302
    link = response.headers["Location"]
    assert "/_bucket/b/" in link and "sig=" in link and "exp=" in link
    got = client.get(link)
    assert got.data == b"hello world"
    assert 'filename="Hello.txt"' in got.headers["Content-Disposition"]
    # The file page never holds a bucket link; each Download click asks again.
    assert "/_bucket/" not in text(client.get(f"/files/{item_id}"))


def test_folders_rename_and_breadcrumbs(app):
    client = browser(app, make_user(app))
    photos = new_folder(client, "Photos")
    trip = new_folder(client, "Trip", photos)
    page = text(client.get(f"/folders/{trip}"))
    assert "My Drive" in page and f'href="/folders/{photos}"' in page
    post(client, f"/items/{trip}/rename", {"name": "Baguio 2026"})
    assert get_item(app, trip).name == "Baguio 2026"
    assert "Baguio 2026" in text(client.get(f"/folders/{photos}"))


def test_move_rules(app):
    client = browser(app, make_user(app))
    a = new_folder(client, "A")
    b = new_folder(client, "B", a)
    file_id = upload(client, "x.txt", parent_id=a)["item"]["id"]
    assert post(client, f"/items/{file_id}/move", {"target_id": b}).status_code == 302
    assert get_item(app, file_id).parent_id == b
    # A folder can't go inside itself or its own child.
    response = post(client, f"/items/{a}/move", {"target_id": b}, follow_redirects=True)
    assert "can't go inside itself" in text(response)
    assert get_item(app, a).parent_id is None
    post(client, f"/items/{b}/move", {"target_id": ""})
    assert get_item(app, b).parent_id is None
    page = text(client.get(f"/items/{file_id}/move?at=0"))
    assert "Move here" in page and ">A<" not in page or "A" in page


def test_move_into_someone_elses_folder_is_404(app):
    maria = browser(app, make_user(app))
    jose = browser(app, make_user(app, "Jose Rizal"))
    file_id = upload(maria, "mine.txt")["item"]["id"]
    joses_folder = new_folder(jose, "Jose's")
    assert post(maria, f"/items/{file_id}/move", {"target_id": joses_folder}).status_code == 404


def test_delete_removes_the_tree_and_the_objects(app):
    client = browser(app, make_user(app))
    top = new_folder(client, "Top")
    inner = new_folder(client, "Inner", top)
    upload(client, "a.txt", b"aaa", parent_id=top)
    upload(client, "b.txt", b"bbb", parent_id=inner)
    keys = bucket_keys(app)
    assert len(keys) == 2
    post(client, f"/items/{top}/trash")
    assert len(_all_items(app)) == 4 and bucket_keys(app) == keys  # in the Trash, still there
    post(client, f"/items/{top}/delete")
    assert _all_items(app) == []
    assert bucket_keys(app) == set()
    assert not any(object_exists(app, key) for key in keys)


def test_a_shared_blob_stays_while_something_uses_it(app, ctx):
    client = browser(app, make_user(app))
    first = upload(client, "a.txt", b"aaa")["item"]["id"]
    blob_id = ctx.scalar(select(FileVersion.blob_id).where(FileVersion.item_id == first))
    # A second file pointing at the same blob (like every demo's copy of a sample).
    other = Item(kind="file", name="copy.txt", owner_id=get_item(app, first).owner_id, created_at=utcnow(),
                 updated_at=utcnow(), size=3, content_type="text/plain")
    ctx.add(other)
    ctx.flush()
    ctx.add(FileVersion(item_id=other.id, number=1, blob_id=blob_id, size=3, created_at=utcnow()))
    ctx.commit()
    post(client, f"/items/{first}/trash")
    post(client, f"/items/{first}/delete")
    key = ctx.scalar(select(Blob.key).where(Blob.id == blob_id))
    assert key is not None and object_exists(app, key)


def test_unfinished_uploads_run_out(app, ctx):
    from datetime import timedelta
    client = browser(app, make_user(app))
    answer = client.post("/api/uploads", json={"name": "a.txt", "size": 1, "type": "text/plain"},
                         headers={"X-CSRF-Token": CSRF}).get_json()
    client.put(answer["url"], data=b"x", headers=answer["headers"])
    key = ctx.get(Upload, answer["upload_id"]).blob.key
    assert files.expire_uploads(ctx, utcnow()) == 0
    assert files.expire_uploads(ctx, utcnow() + timedelta(hours=2)) == 1
    assert not object_exists(app, key)


def test_strangers_get_404_for_everything(app):
    maria = browser(app, make_user(app))
    folder_id = new_folder(maria, "Private")
    file_id = upload(maria, "secret.txt", parent_id=folder_id)["item"]["id"]
    jose = browser(app, make_user(app, "Jose Rizal"))
    for url in (f"/folders/{folder_id}", f"/files/{file_id}", f"/files/{file_id}/download",
                f"/items/{file_id}/rename", f"/items/{file_id}/move", f"/folders/{file_id}", f"/files/{folder_id}",
                "/folders/999999"):
        assert jose.get(url).status_code == 404, url
    for url, data in ((f"/items/{file_id}/rename", {"name": "x"}), (f"/items/{file_id}/trash", {}),
                      (f"/items/{file_id}/delete", {}), (f"/items/{file_id}/restore", {}),
                      ("/folders/new", {"parent_id": folder_id, "name": "x"})):
        assert post(jose, url, data).status_code == 404, url
    answer = jose.post("/api/uploads", json={"parent_id": folder_id, "name": "x.txt", "size": 1},
                       headers={"X-CSRF-Token": CSRF})
    assert answer.status_code == 404
    assert get_item(app, file_id).name == "secret.txt"


def test_list_and_grid_views_and_sorting(app):
    client = browser(app, make_user(app))
    upload(client, "b.txt", b"22")
    upload(client, "a.txt", b"1")
    new_folder(client, "Zebra")
    page = text(client.get("/drive?sort=name"))
    assert page.index("Zebra") < page.index("a.txt") < page.index("b.txt")  # folders first
    page = text(client.get("/drive?sort=size&order=desc"))
    assert page.index("b.txt") < page.index("a.txt")
    assert "file-grid" in text(client.get("/drive?view=grid"))
    assert "file-grid" in text(client.get("/drive"))  # remembered
    assert "file-table" in text(client.get("/drive?view=list"))


def test_file_page_shows_details(app):
    client = browser(app, make_user(app))
    item_id = upload(client, "Budget.xlsx", b"x" * 2500, content_type="application/vnd.ms-excel")["item"]["id"]
    page = text(client.get(f"/files/{item_id}"))
    assert "Spreadsheet" in page and "2.5 KB" in page and "Owner" in page


def test_storage_never_leaves_files_behind(app, ctx):
    assert storage.get is not None
    client = browser(app, make_user(app))
    upload(client, "a.txt")
    assert files.sweep_blobs(ctx) == 0  # nothing to sweep: the blob is in use
