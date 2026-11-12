"""The demo's sample files, drawn by code (no real photos or people).

Each sample is made once, put in the bucket under a fixed key (from its
name), and marked is_sample, so every Try Demo points at the same objects:
a demo costs no storage, and the sweeper never deletes them.
"""
import hashlib
import io
import math
import struct
import wave
from functools import cache

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from . import storage
from .dates import utcnow
from .models import Blob


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # an old Pillow without the built-in scalable font
        return ImageFont.load_default()


def _jpeg(picture):
    out = io.BytesIO()
    picture.convert("RGB").save(out, "JPEG", quality=82)
    return out.getvalue()


def _png(picture):
    out = io.BytesIO()
    picture.save(out, "PNG", optimize=True)
    return out.getvalue()


def landscape(sky_top, sky_bottom, hills, sun=None, size=(1200, 800)):
    """A simple drawn landscape: a sky that fades, a sun, and rows of hills."""
    width, height = size
    picture = Image.new("RGB", size)
    draw = ImageDraw.Draw(picture)
    for y in range(height):
        mix = y / height
        draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * mix) for a, b in zip(sky_top, sky_bottom)))
    if sun:
        x, y, r, color = sun
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    for row, color in enumerate(hills):
        base = int(height * (0.5 + row * 0.13))
        points = [(0, height)]
        for x in range(0, width + 40, 40):
            points.append((x, base - int(60 * math.sin(x / (140 + row * 50) + row * 2) + 30 * math.sin(x / 61))))
        points.append((width, height))
        draw.polygon(points, fill=color)
    return picture


def leche_flan():
    picture = Image.new("RGB", (1000, 750), (253, 246, 227))
    draw = ImageDraw.Draw(picture)
    draw.ellipse((150, 430, 850, 640), fill=(230, 230, 236))
    draw.ellipse((230, 300, 770, 560), fill=(214, 140, 40))
    draw.rectangle((230, 300 + 130, 770, 430 + 60), fill=(214, 140, 40))
    draw.ellipse((230, 230, 770, 420), fill=(240, 196, 92))
    draw.ellipse((330, 260, 670, 360), fill=(150, 80, 20))
    return picture


def pdf(title, lines, pages=1):
    """A small PDF: pages of text drawn with Pillow."""
    sheets = []
    for number in range(pages):
        page = Image.new("RGB", (850, 1100), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((0, 0, 850, 14), fill=(109, 40, 217))
        draw.text((80, 90), title, fill=(42, 24, 56), font=_font(40))
        y = 170
        for line in lines if number == 0 else [f"Page {number + 1}", "", *lines[-3:]]:
            draw.text((80, y), line, fill=(60, 60, 70), font=_font(22))
            y += 40
        draw.text((80, 1040), f"{number + 1} / {pages}", fill=(140, 140, 150), font=_font(16))
        sheets.append(page)
    out = io.BytesIO()
    sheets[0].save(out, "PDF", save_all=True, append_images=sheets[1:], resolution=100)
    return out.getvalue()


def tone(seconds=3.0, rate=16000):
    """A soft three-note chime, as a WAV file."""
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            t = i / rate
            note = (523.25, 659.25, 783.99)[min(2, int(t / (seconds / 3)))]
            fade = max(0.0, 1 - (t % (seconds / 3)) / (seconds / 3))
            frames += struct.pack("<h", int(9000 * fade * math.sin(2 * math.pi * note * t)))
        sound.writeframes(bytes(frames))
    return out.getvalue()


THESIS_LINES = ["Chapter 1. Introduction", "Chapter 2. Related studies", "Chapter 3. Method",
                "Chapter 4. Results", "Chapter 5. Conclusion"]


@cache
def files():
    """{sample name: (file name, content type, bytes)}. Made once per process."""
    pine = [(46, 94, 69), (33, 72, 55), (20, 48, 38)]
    return {
        "baguio-1": ("Burnham Park.jpg", "image/jpeg",
                     _jpeg(landscape((126, 184, 230), (214, 236, 247), pine, (920, 180, 70, (255, 244, 214))))),
        "baguio-2": ("Mines View.jpg", "image/jpeg",
                     _jpeg(landscape((176, 196, 222), (232, 236, 244), [(120, 132, 150), (82, 96, 116), (54, 66, 84)]))),
        "baguio-3": ("Strawberry farm.jpg", "image/jpeg",
                     _jpeg(landscape((160, 210, 240), (236, 246, 250), [(98, 160, 72), (70, 128, 52), (190, 58, 68)],
                                     (200, 160, 60, (255, 250, 230))))),
        "baguio-4": ("Night market.jpg", "image/jpeg",
                     _jpeg(landscape((24, 22, 58), (88, 52, 110), [(36, 28, 64), (24, 18, 44)], (980, 140, 46, (250, 240, 200))))),
        "sunset": ("Sunset at Manila Bay.png", "image/png",
                   _png(landscape((109, 40, 217), (251, 146, 60), [(46, 26, 71), (30, 16, 48)], (600, 520, 150, (253, 186, 116)), size=(1000, 650)))),
        "flan": ("Leche flan.jpg", "image/jpeg", _jpeg(leche_flan())),
        "blurry": ("Blurry photo.jpg", "image/jpeg",
                   _jpeg(landscape((200, 200, 210), (225, 225, 230), [(170, 170, 180)], size=(800, 600)))),
        "thesis-1": ("Thesis draft.pdf", "application/pdf", pdf("Thesis draft (first)", THESIS_LINES[:2])),
        "thesis-2": ("Thesis draft.pdf", "application/pdf", pdf("Thesis draft (second)", THESIS_LINES[:4], pages=2)),
        "thesis-3": ("Thesis draft.pdf", "application/pdf", pdf("Thesis draft", THESIS_LINES, pages=3)),
        "resume": ("Resume.pdf", "application/pdf",
                   pdf("Janelle Demo", ["Web developer (Python, Flask)", "", "Projects", "- J: Drive: file sharing",
                                        "- Ticket Tock: a help desk", "", "Skills", "Python, SQL, HTML, CSS"])),
        "project-plan": ("Project plan.pdf", "application/pdf",
                         pdf("Project plan", ["Week 1: plan", "Week 2: build", "Week 3: test", "Week 4: launch"])),
        "outing": ("Barkada outing 2026.pdf", "application/pdf",
                   pdf("Barkada outing 2026", ["Where: La Union", "When: May 16-18", "Bring: sunscreen, snacks"])),
        "homework-carlo": ("Homework - Carlo.pdf", "application/pdf", pdf("Homework 3", ["Name: Carlo Diaz", "Answers: 1. B  2. D  3. A"])),
        "memo": ("Voice memo.wav", "audio/wav", tone()),
        "budget": ("Budget 2026.csv", "text/csv",
                   b"Month,Food,Rent,Transport,Savings\nJanuary,6500,8000,1800,3000\nFebruary,6200,8000,1700,3500\n"
                   b"March,6900,8000,2100,2800\nApril,6400,8000,1900,3200\n"),
        "reading-1": ("Reading list.md", "text/markdown", b"# Reading list\n\n- Clean Code\n"),
        "reading": ("Reading list.md", "text/markdown",
                    b"# Reading list\n\n- Clean Code\n- The Pragmatic Programmer\n- Designing Data-Intensive Applications\n"
                    b"- Don't Make Me Think\n"),
        "notes": ("Research notes.txt", "text/plain",
                  b"Research notes\n\n- Ask Carlo for the survey results\n- Read two more studies on file sharing\n"
                  b"- Signed URLs: links that run out after a short time\n"),
        "welcome": ("Welcome to J: Drive.txt", "text/plain",
                    b"Welcome to J: Drive!\n\nThis is a sample drive. Try things:\n- Upload a file (drag it onto a folder)\n"
                    b"- Open Share on a folder, and turn on its link\n- Look at Photos > Baguio Trip in the grid view\n"
                    b"- Move something to the Trash and restore it\n\nEverything here is deleted after 24 hours.\n"),
        "old-notes": ("Old notes.txt", "text/plain", b"Notes from last year.\n"),
        "adobo": ("Adobo.txt", "text/plain",
                  b"Chicken adobo\n\n1 kg chicken, 1/2 cup soy sauce, 1/3 cup vinegar, garlic, bay leaves, pepper.\n"
                  b"Simmer 40 minutes. Do not stir after adding the vinegar until it boils.\n"),
        "sinigang": ("Sinigang.md", "text/markdown",
                     b"# Sinigang na baboy\n\n- pork ribs\n- tamarind\n- kangkong, radish, sitaw\n"),
    }


def key_for(name):
    """The fixed bucket key of a sample (so every demo finds the same object)."""
    return "b/" + hashlib.sha256(f"jdrive-sample:{name}:1".encode()).hexdigest()[:32]


def blob(s, name):
    """The sample's blob, putting it in the bucket the first time."""
    key = key_for(name)
    found = s.scalar(select(Blob).where(Blob.key == key))
    if found is not None:
        return found
    _filename, content_type, data = files()[name]
    bucket = storage.get()
    if bucket.head(key) is None:
        bucket.put_bytes(key, data, content_type)
    found = Blob(key=key, size=len(data), content_type=content_type, state="ready", created_at=utcnow(),
                 is_sample=True)
    s.add(found)
    s.flush()
    return found
