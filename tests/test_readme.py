"""The README shows every screenshot, and every picture it shows exists."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCREENSHOTS = ROOT / "docs" / "screenshots"


def in_readme():
    return re.findall(r"docs/screenshots/([\w-]+\.png)", (ROOT / "README.md").read_text(encoding="utf-8"))


def test_every_picture_in_the_readme_exists():
    missing = [name for name in in_readme() if not (SCREENSHOTS / name).exists()]
    assert missing == []


def test_every_screenshot_is_in_the_readme():
    unused = [path.name for path in SCREENSHOTS.glob("*.png") if path.name not in in_readme()]
    assert unused == []


def test_the_screenshot_script_takes_every_picture():
    script = (ROOT / "docs" / "take_screenshots.py").read_text(encoding="utf-8")
    not_taken = [name for name in in_readme() if f'"{name}"' not in script]
    assert not_taken == []
