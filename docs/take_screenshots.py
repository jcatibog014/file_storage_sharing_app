r"""Take the README screenshots (docs/screenshots/*.png).

Retake them in every pull request that visibly changes a page, and add a new
one when a page or feature is added: the README shows everything J: Drive
can do. tests/test_readme.py fails if the pictures and the README disagree.

Every picture comes from "Try demo", so only made-up people and drawn sample
files show. Use an empty practice database, never the live one. It uses
Microsoft Edge, which Windows already has, through Playwright. Playwright is
only a tool for this script, so it is not in requirements.txt (borrow another
project's):

    cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
    $env:DATABASE_URL = "sqlite:///C:/Temp/jdrive-screenshots.sqlite"
    .venv\Scripts\flask --app jdrive upgrade-db
    .venv\Scripts\flask --app jdrive run --debug --port 5012     (keep this one running)
    ..\expense_tracker\.venv\Scripts\python docs\take_screenshots.py

Every picture is one screen (no full-page shots), so they are all the same
size and the README stays even. Add a line to PAGES when a page is added.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5012"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

# (file name, where, theme, screen size). "where" is an address, or a path of names
# to click from My Drive ("Photos > Baguio Trip"), with an optional "!share" (its Share
# page), "@" (a visitor opening its link) or "?view=grid" at the end.
PAGES = [
    ("login.png", "/login", "light", DESKTOP),
    ("check-email.png", "signup", "light", DESKTOP),
    ("demo-start.png", "/drive", "light", DESKTOP),  # the welcome toast, right after Try demo
    ("my-drive.png", "/drive?view=list", "light", DESKTOP),
    ("photos-grid.png", "Photos > Baguio Trip?view=grid", "light", DESKTOP),
    ("picture.png", "Photos > Baguio Trip > Burnham Park.jpg", "light", DESKTOP),
    ("text-and-versions.png", "School > Thesis > Reading list.md", "light", DESKTOP),
    ("folder-activity.png", "School > Thesis?view=list#activity", "light", DESKTOP),
    ("share.png", "Photos > Baguio Trip!share", "light", DESKTOP),
    ("link-settings.png", "School > Homework Box!share#settings", "light", DESKTOP),
    ("shared-with-me.png", "/shared", "light", DESKTOP),
    ("visitor-folder.png", "Photos > Baguio Trip@", "light", DESKTOP),
    ("visitor-upload.png", "School > Homework Box@", "light", DESKTOP),
    ("search.png", "/search?q=a", "light", DESKTOP),
    ("recent.png", "/recent", "light", DESKTOP),
    ("starred.png", "/starred", "light", DESKTOP),
    ("trash.png", "/trash", "light", DESKTOP),
    ("notifications.png", "/notifications", "light", DESKTOP),
    ("activity.png", "/activity", "light", DESKTOP),
    ("settings.png", "/settings#security", "light", DESKTOP),
    ("dark-grid.png", "Photos > Baguio Trip?view=grid", "dark", DESKTOP),
    ("dark-share.png", "Photos > Baguio Trip!share", "dark", DESKTOP),
    ("phone-drive.png", "/drive?view=list", "light", PHONE),
    ("phone-picture.png", "Photos > Baguio Trip > Burnham Park.jpg", "light", PHONE),
]


def start_demo(browser, theme, size):
    page = browser.new_page(viewport=size, color_scheme=theme)
    page.goto(f"{BASE}/login")
    page.get_by_role("button", name="Try demo").click()
    page.wait_for_url(f"{BASE}/drive")
    return page


def open_path(page, names):
    """From My Drive, click each name in turn."""
    page.goto(f"{BASE}/drive?view=list")
    for name in names:
        page.locator(".item-link", has_text=name).first.click()
        page.wait_for_load_state("networkidle")
    return page.url


def go(page, browser, where, theme, size):
    """Open the page for one picture. Returns the page to photograph (a visitor's is a new one)."""
    view = ""
    if "?view=" in where:
        where, view = where.split("?view=")
        view, _, anchor = view.partition("#")
        view, where = f"?view={view}", where + (f"#{anchor}" if anchor else "")
    where, _, anchor = where.partition("#")
    if where == "signup":
        visitor = browser.new_page(viewport=size, color_scheme=theme)
        visitor.goto(f"{BASE}/signup")
        visitor.fill("input[name=name]", "Maria Santos")
        visitor.fill("input[name=email]", "maria.santos@example.com")
        visitor.fill("input[name=password]", "a long password")
        visitor.fill("input[name=confirm_password]", "a long password")
        visitor.get_by_role("button", name="Sign up").click()
        visitor.wait_for_load_state("networkidle")
        return visitor
    if where.startswith("/"):
        page.goto(f"{BASE}{where}", wait_until="networkidle")
        return page
    visitor_view = where.endswith("@")
    share_page = "!share" in where
    names = [name.strip() for name in where.rstrip("@").replace("!share", "").split(">")]
    url = open_path(page, names)
    if share_page or visitor_view:
        page.goto(url.replace("/folders/", "/items/").replace("/files/", "/items/").split("?")[0] + "/share",
                  wait_until="networkidle")
    if visitor_view:
        link = page.locator("#share-link").input_value()
        visitor = browser.new_page(viewport=size, color_scheme=theme)
        visitor.goto(link, wait_until="networkidle")
        return visitor
    if view:
        page.goto(page.url.split("?")[0] + view, wait_until="networkidle")
    if anchor == "settings":
        page.locator(".link-settings summary").click()
        page.locator(".link-settings").scroll_into_view_if_needed()
    elif anchor == "activity":
        page.locator(".folder-activity summary").click()
        page.locator(".folder-activity").scroll_into_view_if_needed()
    elif anchor == "security":
        page.get_by_role("heading", name="Two-Step Sign-In").scroll_into_view_if_needed()
    return page


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        pages = {}
        for name, where, theme, size in PAGES:
            key = (theme, size["width"])
            if name == "login.png":
                shot = browser.new_page(viewport=size, color_scheme=theme)
                shot.goto(f"{BASE}/login")
            elif name == "demo-start.png":
                shot = pages[key] = start_demo(browser, theme, size)  # the welcome toast is still showing
            else:
                if key not in pages:
                    pages[key] = start_demo(browser, theme, size)
                shot = go(pages[key], browser, where, theme, size)
                shot.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
            shot.wait_for_timeout(300)
            shot.screenshot(path=str(OUT / name))
            print("saved", name)
            if shot not in pages.values():
                shot.close()
        browser.close()


if __name__ == "__main__":
    main()
