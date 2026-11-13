"""Try Demo: a sample drive for one visitor, deleted after 24 hours.

The visitor is "Janelle Demo". Three sample people (Ana, Ben, Carlo) share
with her and she shares with them, so every page has something to show:
folders, pictures, PDFs, text, audio, versions, the Trash, stars, recent
files, shares (one still waiting for a sign-up), a view link, an upload link,
activity, notifications and two logged-in devices.

Every demo user's address ends in .invalid, so no email is ever sent. Files
point at the shared sample blobs (samples.py): a demo uses no extra storage.
The demo can upload too, up to its own small space (DEMO_QUOTA).
"""
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select

from . import activity, auth, devices, links, notify, permissions, samples
from .models import FILE, FOLDER, FileVersion, Item, Recent, Share, Star, User

DEMO_LIFETIME = timedelta(hours=24)
DEMO_QUOTA = 50 * 1000 * 1000
MAX_DEMOS = 300


def _person(s, name, moment, tag):
    first = name.split()[0].lower()
    user = auth.create_user(s, name, f"{first}.{tag}@demo.invalid", secrets.token_urlsafe(24),
                            verified_at=moment - timedelta(days=60), is_demo=True,
                            demo_expires_at=moment + DEMO_LIFETIME, quota_bytes=DEMO_QUOTA)
    # Nobody logs in to a demo person with a password, and an old cookie of a
    # deleted demo can't match a new one.
    user.session_version = secrets.randbelow(1_000_000_000) + 2
    return user


def _folder(s, owner, parent, name, by, at):
    folder = Item(kind=FOLDER, name=name, parent_id=parent.id if parent else None, owner_id=owner.id,
                  created_by_id=by.id, created_at=at, updated_at=at)
    s.add(folder)
    s.flush()
    activity.log(s, by, folder, "folder_made", at)
    return folder


def _file(s, owner, parent, sample, by, at, name=None, versions=None):
    """A file pointing at sample blobs. versions: [(sample, by, at), ...] oldest first."""
    versions = versions or [(sample, by, at)]
    filename = name or samples.files()[versions[-1][0]][0]
    item = Item(kind=FILE, name=filename, parent_id=parent.id if parent else None, owner_id=owner.id,
                created_by_id=versions[0][1].id, created_at=versions[0][2], updated_at=versions[-1][2])
    s.add(item)
    s.flush()
    for number, (version_sample, version_by, version_at) in enumerate(versions, start=1):
        blob = samples.blob(s, version_sample)
        s.add(FileVersion(item_id=item.id, number=number, blob_id=blob.id, size=blob.size,
                          uploaded_by_id=version_by.id, created_at=version_at))
        item.size, item.content_type = blob.size, blob.content_type
        activity.log(s, version_by, item, "uploaded" if number == 1 else "new_version", version_at)
    s.flush()
    return item


def _share(s, item, person, role, by, at):
    s.add(Share(item_id=item.id, email=person.email, user_id=person.id, role=role, invited_by_id=by.id, created_at=at))
    activity.log(s, by, item, "shared", at, detail=f"with {person.email} as {permissions.ROLE_NAMES[role]}")
    notify.send(s, person.id, f"{by.name} shared {item.name} with you.",
                f"/{'folders' if item.is_folder else 'files'}/{item.id}", at)


def build(s, moment):
    """Make one visitor's demo. Returns Janelle Demo (the person to log in as)."""
    tag = secrets.token_hex(4)
    me = _person(s, "Janelle Demo", moment, tag)
    ana = _person(s, "Ana Reyes", moment, tag)
    ben = _person(s, "Ben Torres", moment, tag)
    carlo = _person(s, "Carlo Diaz", moment, tag)
    s.flush()

    def ago(**delta):
        return moment - timedelta(**delta)

    # ---------- Janelle's My Drive ----------
    photos = _folder(s, me, None, "Photos", me, ago(days=40))
    baguio = _folder(s, me, photos, "Baguio Trip", me, ago(days=12))
    for number, sample in enumerate(("baguio-1", "baguio-2", "baguio-3", "baguio-4")):
        _file(s, me, baguio, sample, me, ago(days=12, hours=-number))
    sunset = _file(s, me, photos, "sunset", me, ago(days=30))
    school = _folder(s, me, None, "School", me, ago(days=50))
    thesis = _folder(s, me, school, "Thesis", me, ago(days=45))
    draft = _file(s, me, thesis, None, me, ago(days=44), versions=[
        ("thesis-1", me, ago(days=44)), ("thesis-2", carlo, ago(days=20)), ("thesis-3", me, ago(hours=5))])
    reading = _file(s, me, thesis, None, me, ago(days=40), versions=[
        ("reading-1", me, ago(days=40)), ("reading", carlo, ago(days=2))])
    _file(s, me, thesis, "notes", carlo, ago(days=3))
    homework = _folder(s, me, school, "Homework Box", me, ago(days=9))
    work = _folder(s, me, None, "Work", me, ago(days=25))
    _file(s, me, work, "budget", me, ago(days=8))
    plan = _file(s, me, work, "project-plan", me, ago(days=6))
    resume = _file(s, me, None, "resume", me, ago(days=15))
    memo = _file(s, me, None, "memo", me, ago(days=2))
    welcome = _file(s, me, None, "welcome", me, ago(hours=1))

    # Things in the Trash (one recent, one nearly gone).
    old_notes = _file(s, me, school, "old-notes", me, ago(days=200))
    old_notes.trashed_at, old_notes.trashed_by_id = ago(days=5), me.id
    blurry = _file(s, me, photos, "blurry", me, ago(days=60))
    blurry.trashed_at, blurry.trashed_by_id = ago(days=26), me.id
    activity.log(s, me, old_notes, "trashed", ago(days=5))
    activity.log(s, me, blurry, "trashed", ago(days=26))

    # Janelle shares: the trip with Ana (Editor) and Ben (Viewer), the thesis with Carlo (Co-owner),
    # and the Work folder with someone who hasn't signed up yet (a waiting invite).
    _share(s, baguio, ana, permissions.EDITOR, me, ago(days=11))
    _share(s, baguio, ben, permissions.VIEWER, me, ago(days=11))
    _share(s, thesis, carlo, permissions.COOWNER, me, ago(days=30))
    waiting = f"lorna.{tag}@demo.invalid"
    s.add(Share(item_id=work.id, email=waiting, user_id=None, role=permissions.VIEWER, invited_by_id=me.id,
                created_at=ago(days=1)))
    activity.log(s, me, work, "shared", ago(days=1), detail=f"with {waiting} as Viewer")
    # Ana, as an Editor, added a picture to the trip.
    _file(s, me, baguio, "flan", ana, ago(days=10), name="Pasalubong.jpg")

    # Links: the trip for anyone (a week), and the Homework Box takes files from anyone.
    trip_link = links.turn_on(s, me, baguio, ago(days=11))
    trip_link.expires_at, trip_link.open_count = moment + timedelta(days=7), 14
    activity.log(s, me, baguio, "link_on", ago(days=11))
    activity.log(s, None, baguio, "link_opened", ago(days=2))
    box_link = links.turn_on(s, me, homework, ago(days=9))
    box_link.role, box_link.open_count = links.UPLOADER, 3
    activity.log(s, me, homework, "link_on", ago(days=9))
    sent = _file(s, me, homework, "homework-carlo", me, ago(days=1))
    sent.created_by_id = None  # a visitor with the upload link, not a person with an account
    for version in s.scalars(select(FileVersion).where(FileVersion.item_id == sent.id)):
        version.uploaded_by_id = None
    activity.log(s, None, sent, "link_upload", ago(days=1), detail="into Homework Box")
    notify.send(s, me.id, f"Someone sent {sent.name} into Homework Box.", f"/folders/{homework.id}", ago(days=1))

    # ---------- Shared with Janelle ----------
    recipes = _folder(s, ana, None, "Family Recipes", ana, ago(days=90))
    _file(s, ana, recipes, "adobo", ana, ago(days=90))
    _file(s, ana, recipes, "sinigang", ana, ago(days=80))
    _file(s, ana, recipes, "flan", ana, ago(days=70))
    _share(s, recipes, me, permissions.EDITOR, ana, ago(days=6))
    outing = _file(s, ben, None, "outing", ben, ago(days=4))
    _share(s, outing, me, permissions.VIEWER, ben, ago(days=4))
    # A few of Janelle's own actions to round out the history.
    activity.log(s, me, resume, "downloaded", ago(days=3))
    activity.log(s, me, plan, "renamed", ago(days=6), detail="from plan-final-FINAL.pdf")
    activity.log(s, carlo, draft, "downloaded", ago(hours=20))

    # Stars, Recent, a second device.
    for item in (resume, thesis, recipes):
        s.add(Star(user_id=me.id, item_id=item.id, created_at=ago(days=2)))
    for hours, item in ((1, welcome), (5, draft), (8, reading), (30, sunset), (50, memo), (70, recipes), (90, outing)):
        s.add(Recent(user_id=me.id, item_id=item.id, seen_at=ago(hours=hours)))
    phone_id, _secret = devices.start(s, me, "Safari on iPhone", "112.198.0.24", ago(days=3))
    s.flush()
    from .models import LoginSession
    s.get(LoginSession, phone_id).last_seen_at = ago(hours=2)
    # Older notifications are already read.
    from .models import Notification
    for note in s.scalars(select(Notification).where(Notification.user_id == me.id, Notification.at < ago(days=5))):
        note.read_at = ago(days=4)
    s.flush()
    return me


def purge(s, moment):
    """Delete every demo person whose time is up. Their files, shares, links and
    history go with them (ON DELETE CASCADE); unused objects are swept later."""
    return s.execute(delete(User).where(User.is_demo.is_(True), User.demo_expires_at <= moment)
                     .execution_options(synchronize_session=False)).rowcount


def count(s):
    """How many demos are running (one Janelle each)."""
    return s.scalar(select(func.count()).select_from(User)
                    .where(User.is_demo.is_(True), User.email.like("janelle.%@demo.invalid"))) or 0


def init_app(app):
    from . import tick
    if ("demos_deleted", purge) not in tick.jobs:
        tick.jobs.append(("demos_deleted", purge))

