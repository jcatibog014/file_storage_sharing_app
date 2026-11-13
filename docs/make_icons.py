r"""Make the logo and the app icons (jdrive/static/icons/) from the owner's logo.

The logo is the owner's own drawing: a violet folder with a paper sticking
out, with a soft shadow (docs/logo/jdrive-folder-plain.svg). It also brings a
dark background (#bg) and a violet glow (#glow) for the app icon. This script:

- copies it to static/icons/logo.svg for the pages (the menu, the login panel);
- draws icon-32.png and favicon.ico: the folder alone, see-through, for browser tabs;
- draws the app icons: the folder on the dark tile with the glow behind it.
  icon-192/512.png keep the tile's round corners (installed apps);
  apple-touch-icon.png is square (iPhones round the corners themselves);
  icon-maskable-512.png is square with the folder smaller, inside the middle,
  because Android crops these into its own shape (a circle, a squircle...).

Phones and browsers want PNG pictures in fixed sizes, so this draws the SVG in
a browser at each size, through Playwright + Edge (Playwright isn't in
requirements.txt: borrow another project's):

    cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
    ..\expense_tracker\.venv\Scripts\python docs\make_icons.py
"""
import re
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "docs" / "logo" / "jdrive-folder-plain.svg"
ICONS = ROOT / "jdrive" / "static" / "icons"
# The folder's own box in the drawing (without its shadow), and its middle.
FOLDER_BOX = "78 100 244 218"
MIDDLE = (200, 208)


def parts():
    """The drawing's <defs> (gradients, shadow) and its folder (the <g> with the shadow)."""
    svg = SOURCE.read_text(encoding="utf-8")
    defs = re.search(r"<defs>.*?</defs>", svg, re.S).group(0)
    folder = re.search(r"<g filter=\"url\(#ds\)\">.*?</g>", svg, re.S).group(0)
    return defs, folder


def plain_svg(size):
    """The folder alone on nothing, filling the square (for browser tabs)."""
    defs, folder = parts()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{FOLDER_BOX}" width="{size}" height="{size}">'
            f"{defs}{folder}</svg>")


def tile_svg(size, rounded, scale):
    """The folder on the dark tile, with the violet glow behind it."""
    defs, folder = parts()
    corner = 88 if rounded else 0
    x, y = MIDDLE
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" width="{size}" height="{size}">{defs}'
            f'<rect width="400" height="400" rx="{corner}" fill="url(#bg)"/>'
            f'<circle cx="200" cy="205" r="175" fill="url(#glow)"/>'
            f'<g transform="translate(200 205) scale({scale}) translate({-x} {-y})">{folder}</g></svg>')


# (file, size, how): "plain" = the folder alone; "rounded"/"square" = on the tile;
# "maskable" = on the square tile, smaller, inside Android's safe circle.
PNGS = [
    ("icon-32.png", 32, "plain"),
    ("icon-192.png", 192, "rounded"),
    ("icon-512.png", 512, "rounded"),
    ("apple-touch-icon.png", 180, "square"),
    ("icon-maskable-512.png", 512, "maskable"),
]
ICO_SIZES = (16, 32, 48)


def svg_for(how, size):
    if how == "plain":
        return plain_svg(size)
    if how == "maskable":
        return tile_svg(size, rounded=False, scale=1.0)
    return tile_svg(size, rounded=how == "rounded", scale=1.28)


def render(browser, svg, size):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(f'<style>html, body {{ margin: 0; background: transparent; }} svg {{ display: block; }}</style>'
                     f'<div id="icon">{svg}</div>')
    png = page.locator("#icon svg").screenshot(omit_background=True)
    page.close()
    return png


def write_ico(path, pictures):
    """favicon.ico is a small header, a list, then the PNG files themselves."""
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = 6 + 16 * len(pictures)
    entries, data = b"", b""
    for size, png in pictures:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def main():
    (ICONS / "logo.svg").write_text(SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    print("saved logo.svg")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, size, how in PNGS:
            (ICONS / name).write_bytes(render(browser, svg_for(how, size), size))
            print("saved", name)
        write_ico(ICONS / "favicon.ico", [(size, render(browser, plain_svg(size), size)) for size in ICO_SIZES])
        print("saved favicon.ico")
        browser.close()


if __name__ == "__main__":
    main()
