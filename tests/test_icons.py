"""The app icon: every size exists, every page points at it, and the manifest is right."""
import json
import struct
from pathlib import Path

import pytest

STATIC = Path(__file__).parent.parent / "jdrive" / "static"
ICONS = STATIC / "icons"


def png_size(path):
    """Width and height straight from the PNG header (no picture library needed)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("name, size", [
    ("icon-32.png", 32), ("icon-192.png", 192), ("icon-512.png", 512),
    ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180),
])
def test_every_icon_has_its_size(name, size):
    assert png_size(ICONS / name) == (size, size)


def test_favicon_has_three_sizes():
    data = (ICONS / "favicon.ico").read_bytes()
    assert struct.unpack("<HHH", data[:6]) == (0, 1, 3)


def test_the_manifest_lists_existing_icons():
    manifest = json.loads((STATIC / "site.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "J: Drive"
    for icon in manifest["icons"]:
        assert (STATIC / icon["src"]).exists()
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])


def test_pages_link_the_icons(client):
    page = client.get("/login").data
    assert b"icons/favicon.ico" in page and b"site.webmanifest" in page and b"apple-touch-icon" in page
    assert client.get("/favicon.ico").status_code == 200
    assert "manifest" in client.get("/static/site.webmanifest").content_type


def test_the_app_can_be_installed(client):
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and "javascript" in worker.content_type
    assert worker.headers["Cache-Control"] == "no-cache"
    for path in ("/static/offline.html", "/static/theme.js", "/static/style.css"):
        assert client.get(path).status_code == 200
    assert "worker-src 'self'" in client.get("/login").headers["Content-Security-Policy"]


def test_the_logo_is_the_j_drive_folder(client):
    logo = (ICONS / "logo.svg").read_text(encoding="utf-8")
    assert logo.startswith("<svg") and 'id="bg"' in logo and '#4c1d95' in logo
    page = client.get("/login").get_data(as_text=True)
    assert "icons/logo.svg" in page
    assert client.get("/static/icons/logo.svg").mimetype == "image/svg+xml"
