r"""Make the app icons (jdrive/static/icons/) from the logo, static/icons/logo.svg.

The logo is a white folder with "J:" on it (the J from Janelle, and a drive
letter like C: or D:), on a violet tile. Phones and browsers want PNG pictures in fixed sizes, plus
favicon.ico, so this draws the SVG in a browser at each size. Run it again
after changing logo.svg. Like take_screenshots.py, it uses Playwright + Edge
(Playwright isn't in requirements.txt: borrow another project's):

    ..\expense_tracker\.venv\Scripts\python docs\make_icons.py
"""
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
ICONS = ROOT / "jdrive" / "static" / "icons"
LOGO = ICONS / "logo.svg"
TILE = '<rect width="400" height="400" rx="92" fill="url(#bg)"/>'
GLOW = '<ellipse cx="200" cy="120" rx="220" ry="110" fill="#FFFFFF" fill-opacity="0.10"/>'

# (file, size, shape). "rounded" is the logo as drawn, with see-through corners, for
# browser tabs and installed apps. "square" fills every pixel: iPhones round the corners
# themselves. "maskable" is square too, with the drawing shrunk into the middle 80%,
# because Android crops these into its own shape (a circle, a squircle...).
PNGS = [
    ("icon-32.png", 32, "rounded"),
    ("icon-192.png", 192, "rounded"),
    ("icon-512.png", 512, "rounded"),
    ("icon-maskable-512.png", 512, "maskable"),
    ("apple-touch-icon.png", 180, "square"),
]
ICO_SIZES = (16, 32, 48)


def svg_for(shape, size):
    svg = LOGO.read_text(encoding="utf-8").replace('width="1024" height="1024"', f'width="{size}" height="{size}"')
    assert TILE in svg, "logo.svg changed: update TILE in make_icons.py"
    if shape in ("square", "maskable"):
        svg = svg.replace(TILE, TILE.replace('rx="92"', 'rx="0"'))
    if shape == "maskable":
        # The drawing (after the tile and its soft highlight) shrunk to 80% around the middle.
        assert GLOW in svg, "logo.svg changed: update GLOW in make_icons.py"
        svg = svg.replace(GLOW, GLOW + '<g transform="translate(40 40) scale(0.8)">')
        svg = svg.replace("</svg>", "</g></svg>")
    return svg


def render(browser, size, shape):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(f'<style>html, body {{ margin: 0; background: transparent; }} svg {{ display: block; }}</style>'
                     f'<div id="icon">{svg_for(shape, size)}</div>')
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
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, size, shape in PNGS:
            (ICONS / name).write_bytes(render(browser, size, shape))
            print("saved", name)
        write_ico(ICONS / "favicon.ico", [(size, render(browser, size, "rounded")) for size in ICO_SIZES])
        print("saved favicon.ico")
        browser.close()


if __name__ == "__main__":
    main()
