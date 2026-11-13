r"""Make the logo and app icons (jdrive/static/icons/) from the owner's logo.

The logo is the owner's own drawing: a violet folder with a paper sticking
out and a white "J" on the front (docs/logo/j-drive-logo-original.png). This
script cuts away its soft shadow and the empty space around it, then saves:

- logo.png: the folder for the pages (the menu, the login panel), 2x sharp;
- icon-32/192/512.png and favicon.ico: the folder on a see-through background,
  for browser tabs and installed apps;
- apple-touch-icon.png: on a soft lilac square (iPhones don't allow see-through
  icons, and round the corners themselves);
- icon-maskable-512.png: on lilac, the folder inside the middle 80%, because
  Android crops these into its own shape (a circle, a squircle...).

Run it again after changing the original:

    cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
    .venv\Scripts\python docs\make_icons.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "docs" / "logo" / "j-drive-logo-original.png"
ICONS = ROOT / "jdrive" / "static" / "icons"
TILE = (243, 238, 254, 255)       # soft lilac behind the folder where a solid square is needed
LOGO_HEIGHT = 128                 # logo.png: shown at up to 64px tall, so 2x for sharp screens


def folder():
    """The folder alone: the shadow (dark, see-through pixels) removed, cropped to the drawing."""
    picture = Image.open(SOURCE).convert("RGBA")
    pixels = picture.load()
    width, height = picture.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < 255 and max(r, g, b) < 60:  # the shadow is black with some see-through
                pixels[x, y] = (0, 0, 0, 0)
    return picture.crop(picture.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())


def fitted(drawing, size, share, background=(0, 0, 0, 0)):
    """The drawing in the middle of a size x size square, its longer side `share` of the square."""
    scale = size * share / max(drawing.size)
    small = drawing.resize((max(1, round(drawing.width * scale)), max(1, round(drawing.height * scale))),
                           Image.LANCZOS)
    square = Image.new("RGBA", (size, size), background)
    square.alpha_composite(small, ((size - small.width) // 2, (size - small.height) // 2))
    return square


def main():
    drawing = folder()
    logo = drawing.resize((round(drawing.width * LOGO_HEIGHT / drawing.height), LOGO_HEIGHT), Image.LANCZOS)
    logo.save(ICONS / "logo.png", optimize=True)
    print("saved logo.png", logo.size)
    for name, size in (("icon-32.png", 32), ("icon-192.png", 192), ("icon-512.png", 512)):
        fitted(drawing, size, 0.96).save(ICONS / name, optimize=True)
        print("saved", name)
    fitted(drawing, 180, 0.78, TILE).convert("RGB").save(ICONS / "apple-touch-icon.png", optimize=True)
    print("saved apple-touch-icon.png")
    fitted(drawing, 512, 0.66, TILE).convert("RGB").save(ICONS / "icon-maskable-512.png", optimize=True)
    print("saved icon-maskable-512.png")
    fitted(drawing, 256, 0.96).save(ICONS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("saved favicon.ico")


if __name__ == "__main__":
    main()
