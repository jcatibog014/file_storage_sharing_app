"""Search, Starred, Recent, previews, thumbnails and folder ZIPs."""
import io
import zipfile

from PIL import Image
from sqlalchemy import func, select

from jdrive import db, links
from jdrive.models import Blob, ShareLink, Thumbnail

from .conftest import browser, make_user, new_folder, post, text, upload


def png_bytes(size=(800, 600), color=(124, 58, 237)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


# ---------- Search ----------

def test_search_finds_my_files_and_shared_ones_only(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    ana = browser(app, ana_id)
    upload(maria, "Budget 2026.xlsx")
    shared = new_folder(ana, "Team")
    upload(ana, "budget-draft.txt", parent_id=shared)
    upload(ana, "Budget secret.txt")  # Ana's, not shared
    post(ana, f"/items/{shared}/share", {"action": "add", "emails": "maria@example.com", "role": "viewer"})
    page = text(maria.get("/search?q=budget"))
    assert "Budget 2026.xlsx" in page and "budget-draft.txt" in page
    assert "Budget secret.txt" not in page
    assert "Shared by Ana" in page
    assert "budget-draft.txt" not in text(maria.get("/search?q=budget&where=mine"))
    assert "Budget 2026.xlsx" not in text(maria.get("/search?q=budget&where=shared"))


def test_search_by_type_and_odd_characters(app):
    client = browser(app, make_user(app))
    upload(client, "photo.png", png_bytes(), content_type="image/png")
    upload(client, "100% done_v2.txt")
    upload(client, "100 percent.txt")
    assert "photo.png" in text(client.get("/search?type=image"))
    assert "100 percent" not in text(client.get("/search?type=image"))
    page = text(client.get("/search?q=100%25"))  # "100%" is just text, not a wildcard
    assert "100% done_v2.txt" in page and "100 percent.txt" not in page


def test_search_leaves_out_the_trash(app):
    client = browser(app, make_user(app))
    folder = new_folder(client, "Old")
    upload(client, "findme.txt", parent_id=folder)
    post(client, f"/items/{folder}/trash")
    assert "findme.txt" not in text(client.get("/search?q=findme"))


# ---------- Stars and Recent ----------

def test_stars(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "fave.txt")["item"]["id"]
    assert "No Stars Yet" in text(client.get("/starred"))
    post(client, f"/items/{file_id}/star", {"next": "/drive"})
    assert "fave.txt" in text(client.get("/starred"))
    assert "Remove star" in text(client.get("/drive"))
    post(client, f"/items/{file_id}/star")
    assert "No Stars Yet" in text(client.get("/starred"))


def test_a_star_goes_away_from_the_list_when_access_does(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    folder = new_folder(maria, "Team")
    post(maria, f"/items/{folder}/share", {"action": "add", "emails": "ana@example.com", "role": "viewer"})
    ana = browser(app, ana_id)
    post(ana, f"/items/{folder}/star")
    assert "Team" in text(ana.get("/starred"))
    with app.app_context():
        from jdrive.models import Share
        share_id = db.session().scalar(select(Share.id))
    post(maria, f"/items/{folder}/share", {"action": "remove", "share_id": share_id})
    assert "Team" not in text(ana.get("/starred")).split("</h1>")[1]


def test_recent_remembers_what_was_opened(app):
    client = browser(app, make_user(app))
    first = upload(client, "first.txt")["item"]["id"]
    folder = new_folder(client, "Later")
    client.get(f"/folders/{folder}")
    page = text(client.get("/recent"))
    assert "first.txt" in page and "Later" in page
    assert page.index("Later") < page.index("first.txt")  # newest first
    client.get(f"/files/{first}")
    page = text(client.get("/recent"))
    assert page.index("first.txt") < page.index("Later")


# ---------- Previews ----------

def test_a_picture_preview_is_a_fresh_signed_inline_link(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "cat.png", png_bytes(), content_type="image/png")["item"]["id"]
    page = text(client.get(f"/files/{file_id}"))
    assert f'src="/files/{file_id}/preview"' in page and "/_bucket/" not in page
    redirect = client.get(f"/files/{file_id}/preview")
    assert redirect.status_code == 302
    shown = client.get(redirect.headers["Location"])
    assert shown.headers["Content-Disposition"].startswith("inline")
    assert shown.headers["Content-Type"] == "image/png"


def test_html_and_svg_are_never_shown_inline(app):
    client = browser(app, make_user(app))
    for name, content_type in (("page.html", "text/html"), ("logo.svg", "image/svg+xml")):
        file_id = upload(client, name, b"<script>alert(1)</script>", content_type=content_type)["item"]["id"]
        assert client.get(f"/files/{file_id}/preview").status_code == 404
    html_id = upload(client, "x.html", b"<b>hi</b>", content_type="text/html")["item"]["id"]
    page = text(client.get(f"/files/{html_id}"))
    assert "&lt;b&gt;hi&lt;/b&gt;" in client.get(f"/files/{html_id}").get_data(as_text=True)  # shown as text
    assert "<b>hi</b>" in page


def test_text_previews_show_only_the_start(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "long.log", b"line\n" * 50000, content_type="text/plain")["item"]["id"]
    page = text(client.get(f"/files/{file_id}"))
    assert "line" in page and "Only the start is shown" in page


def test_pdf_and_video_previews(app):
    client = browser(app, make_user(app))
    pdf = upload(client, "guide.pdf", b"%PDF-1.4", content_type="application/pdf")["item"]["id"]
    video = upload(client, "clip.mp4", b"\x00\x00\x00\x18ftyp", content_type="video/mp4")["item"]["id"]
    assert f'<iframe class="preview-pdf" src="/files/{pdf}/preview"' in client.get(f"/files/{pdf}").get_data(as_text=True)
    assert "<video" in text(client.get(f"/files/{video}"))
    redirect = client.get(f"/files/{pdf}/preview")
    assert redirect.headers["X-Frame-Options"] == "SAMEORIGIN"
    shown = client.get(redirect.headers["Location"])
    assert "frame-ancestors 'self'" in shown.headers["Content-Security-Policy"]


def test_thumbnails_are_new_small_webp_files(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "big.png", png_bytes((2000, 1000)), content_type="image/png")["item"]["id"]
    assert f'/files/{file_id}/thumb' in text(client.get("/drive?view=grid"))
    shown = client.get(client.get(f"/files/{file_id}/thumb").headers["Location"])
    assert shown.headers["Content-Type"] == "image/webp"
    with Image.open(io.BytesIO(shown.data)) as picture:
        assert picture.format == "WEBP" and max(picture.size) == 480
    client.get(f"/files/{file_id}/thumb")  # made once
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Thumbnail)) == 1


def test_a_broken_picture_gets_no_thumbnail_and_isnt_tried_again(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "broken.png", b"not really a png", content_type="image/png")["item"]["id"]
    assert client.get(f"/files/{file_id}/thumb").status_code == 404
    assert client.get(f"/files/{file_id}/thumb").status_code == 404
    with app.app_context():
        assert db.session().scalar(select(Thumbnail.blob_id)) is None


def test_thumbnails_are_swept_with_their_file(app):
    client = browser(app, make_user(app))
    file_id = upload(client, "a.png", png_bytes(), content_type="image/png")["item"]["id"]
    client.get(f"/files/{file_id}/thumb")
    post(client, f"/items/{file_id}/trash")
    post(client, f"/items/{file_id}/delete")
    with app.app_context():
        from jdrive import files
        files.sweep_blobs(db.session())
        assert db.session().scalar(select(func.count()).select_from(Blob)) == 0


def test_strangers_get_no_previews(app):
    maria = browser(app, make_user(app))
    file_id = upload(maria, "cat.png", png_bytes(), content_type="image/png")["item"]["id"]
    jose = browser(app, make_user(app, "Jose Rizal"))
    for url in (f"/files/{file_id}/preview", f"/files/{file_id}/thumb"):
        assert jose.get(url).status_code == 404


# ---------- ZIP ----------

def test_download_a_folder_as_a_zip(app):
    client = browser(app, make_user(app))
    top = new_folder(client, "Trip")
    day = new_folder(client, "Day 1", top)
    upload(client, "notes.txt", b"hello", parent_id=top)
    upload(client, "beach.jpg", b"jpeg bytes", parent_id=day, content_type="image/jpeg")
    gone = upload(client, "deleted.txt", b"x", parent_id=day)["item"]["id"]
    post(client, f"/items/{gone}/trash")
    response = client.get(f"/folders/{top}/zip")
    assert response.status_code == 200 and response.mimetype == "application/zip"
    assert 'filename="Trip.zip"' in response.headers["Content-Disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert sorted(archive.namelist()) == ["Day 1/beach.jpg", "notes.txt"]
    assert archive.read("notes.txt") == b"hello"
    assert archive.getinfo("notes.txt").compress_type == zipfile.ZIP_DEFLATED


def test_too_big_for_one_zip(app, monkeypatch):
    from jdrive import previews
    monkeypatch.setattr(previews, "ZIP_MAX_FILES", 1)
    client = browser(app, make_user(app))
    top = new_folder(client, "Many")
    upload(client, "a.txt", parent_id=top)
    upload(client, "b.txt", parent_id=top)
    assert client.get(f"/folders/{top}/zip").status_code == 413


# ---------- Through a shared link ----------

def link_token(app, client, item_id, **settings):
    post(client, f"/items/{item_id}/share", {"action": "link_on"})
    if settings:
        post(client, f"/items/{item_id}/share", {"action": "link_settings", "link_role": "viewer",
                                                 "password_action": "keep", **settings})
    with app.app_context(), app.test_request_context():
        return links.token_for(db.session().scalar(select(ShareLink).where(ShareLink.item_id == item_id)))


def test_link_visitors_see_previews_and_zips(app):
    maria = browser(app, make_user(app))
    top = new_folder(maria, "Album")
    photo = upload(maria, "cat.png", png_bytes(), parent_id=top, content_type="image/png")["item"]["id"]
    outside = upload(maria, "private.png", png_bytes(), content_type="image/png")["item"]["id"]
    token = link_token(app, maria, top)
    visitor = app.test_client()
    page = text(visitor.get(f"/s/{token}/i/{photo}"))
    assert f"/s/{token}/p/{photo}" in page
    assert visitor.get(f"/s/{token}/p/{photo}").status_code == 302
    assert visitor.get(f"/s/{token}/p/{outside}").status_code == 404
    assert visitor.get(f"/s/{token}/z/{top}").status_code == 200


def test_downloads_off_still_shows_previews_but_no_zip(app):
    maria = browser(app, make_user(app))
    top = new_folder(maria, "Album")
    photo = upload(maria, "cat.png", png_bytes(), parent_id=top, content_type="image/png")["item"]["id"]
    token = link_token(app, maria, top, allow_download="")
    visitor = app.test_client()
    assert visitor.get(f"/s/{token}/p/{photo}").status_code == 302
    assert visitor.get(f"/s/{token}/z/{top}").status_code == 403
    assert "Download all" not in text(visitor.get(f"/s/{token}"))
