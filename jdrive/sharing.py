"""Sharing with people by email: Viewer, Editor or Co-owner.

- Someone with a confirmed account gets access at once, and an email.
- An email with no account (or one not confirmed yet) becomes a WAITING
  share, and gets an email inviting them to sign up with that address.
  The share is handed over only after the emailed code proves the address
  (claim_waiting, run from auth.on_verified).
- A share on a folder covers everything inside it. The highest role wins
  (a Viewer of a folder who is an Editor of one file inside can edit that file).
- A Co-owner manages people like the owner does, but nobody can remove the
  Owner or make someone else Owner except the Owner ("transfer").
"""
from flask import url_for
from sqlalchemy import func, select, update

from . import auth, limits, mailer, permissions, tree
from .models import Item, Share, User

MAX_PEOPLE_AT_ONCE = 20
INVITE_EMAILS_PER_HOUR = 50


class Problem(Exception):
    pass


def split_emails(text):
    """"ana@x.com, ben@y.com; carlo@z.com" -> a clean list (no repeats)."""
    seen = []
    for part in (text or "").replace(";", ",").replace("\n", ",").split(","):
        email = auth.clean_email(part)
        if email and email not in seen:
            seen.append(email)
    return seen


def shares_on(s, item):
    """The shares made on this very item (not the folders above it)."""
    return list(s.scalars(select(Share).where(Share.item_id == item.id).order_by(Share.created_at, Share.id)))


def inherited_shares(s, path):
    """Shares on the folders above (they cover this item too), nearest first."""
    above = [item.id for item in path[:-1]]
    if not above:
        return []
    by_item = {item.id: item for item in path}
    rows = list(s.scalars(select(Share).where(Share.item_id.in_(above))))
    rows.sort(key=lambda share: -[item.id for item in path].index(share.item_id))
    return [(share, by_item[share.item_id]) for share in rows]


def role_from_shares(s, user, path):
    """permissions.role_sources: the highest role any share on the path gives this user."""
    if user is None or not path:
        return None
    roles = s.scalars(select(Share.role).where(Share.user_id == user.id,
                                               Share.item_id.in_([item.id for item in path])))
    best = None
    for role in roles:
        best = permissions.higher(best, role)
    return best


def first_shared_index(s, user, path):
    """Where a shared person's breadcrumbs start: the highest item on the path
    they were given (they can't see the owner's folders above it)."""
    if user is None or not path or path[-1].owner_id == user.id:
        return 0
    shared = set(s.scalars(select(Share.item_id).where(Share.user_id == user.id,
                                                       Share.item_id.in_([item.id for item in path]))))
    for index, item in enumerate(path):
        if item.id in shared:
            return index
    return len(path) - 1


def item_link(item, external=True):
    endpoint = "drive.folder" if item.is_folder else "drive.file"
    return url_for(endpoint, item_id=item.id, _external=external)


def add_people(s, actor, item, emails, role, moment, note=""):
    """Share item with each email. Returns (added, updated) counts.
    The caller has checked that actor may share (Co-owner or Owner)."""
    if role not in permissions.SHARE_ROLES:
        raise Problem("Choose Viewer, Editor or Co-owner.")
    if not emails:
        raise Problem("Type at least one email.")
    if len(emails) > MAX_PEOPLE_AT_ONCE:
        raise Problem(f"Add at most {MAX_PEOPLE_AT_ONCE} people at once.")
    for email in emails:
        problem = auth.email_problem(email)
        if problem:
            raise Problem(f"{email}: {problem}")
    if item.owner.email in emails:
        raise Problem(f"{item.owner.email} is the owner already.")
    if not limits.hit(s, f"invites:{actor.id}", INVITE_EMAILS_PER_HOUR, 3600, moment):
        raise Problem("That's a lot of invites for one hour. Please wait a while.")
    added = updated = 0
    for email in emails:
        person = auth.find_user(s, email)
        verified = person if person is not None and person.is_verified else None
        share = s.scalar(select(Share).where(Share.item_id == item.id, Share.email == email))
        if share is not None:
            if share.role != role:
                share.role = role
                updated += 1
            continue
        share = Share(item_id=item.id, email=email, user_id=verified.id if verified else None, role=role,
                      invited_by_id=actor.id, created_at=moment)
        s.add(share)
        s.flush()
        added += 1
        _send_invite(actor, item, share, verified, note)
    return added, updated


def a_role(role):
    """"a Viewer", "an Editor", "a Co-owner"."""
    name = permissions.ROLE_NAMES[role]
    return ("an " if name[0] in "AEIOU" else "a ") + name


def _send_invite(actor, item, share, person, note):
    kind = "folder" if item.is_folder else "file"
    role = a_role(share.role)
    note_line = f'\n{actor.first_name} wrote: "{note.strip()[:500]}"\n' if note and note.strip() else ""
    if person is not None:
        body = (f"Hi {person.first_name},\n\n{actor.name} ({actor.email}) shared the {kind} \"{item.name}\" "
                f"with you on J: Drive. You are {role}.\n{note_line}\nOpen it: {item_link(item)}\n\nJ: Drive")
    else:
        signup = url_for("auth.signup", email=share.email, next=item_link(item, external=False), _external=True)
        body = (f"Hi,\n\n{actor.name} ({actor.email}) shared the {kind} \"{item.name}\" with you on J: Drive. "
                f"You are {role}.\n{note_line}\nTo open it, sign up with this email address ({share.email}). "
                f"We'll send you a code to check it's yours, then the {kind} is waiting for you:\n{signup}\n\nJ: Drive")
    mailer.send(share.email, f"{actor.name} shared \"{item.name}\" with you", body)


def change_role(share, role):
    if role not in permissions.SHARE_ROLES:
        raise Problem("Choose Viewer, Editor or Co-owner.")
    share.role = role


def claim_waiting(s, user, moment):
    """auth.on_verified: the email is proven, so waiting shares for it are theirs now."""
    s.execute(update(Share).where(Share.email == user.email, Share.user_id.is_(None)).values(user_id=user.id))


def shared_with_me(s, user):
    """(share, item) for everything shared directly with this person, newest first."""
    rows = s.execute(select(Share, Item).join(Item, Item.id == Share.item_id)
                     .where(Share.user_id == user.id, Item.owner_id != user.id)
                     .order_by(Share.created_at.desc(), Share.id.desc())).all()
    return [(share, item) for share, item in rows]


def transfer(s, actor, item, new_owner_share, moment):
    """The Owner passes ownership of a top-level item (and everything in it) to
    someone it is shared with. The old owner stays on as Co-owner. Only for
    items at the top of My Drive, so a tree always has one owner."""
    if item.parent_id is not None:
        raise Problem("Move it to the top of My Drive first, then pass it on.")
    person = new_owner_share.user
    if person is None:
        raise Problem("They need to sign up first.")
    from . import files
    files.check_space(s, person.id, total_size(s, item))
    ids = tree.descendant_ids(s, item.id)
    s.execute(update(Item).where(Item.id.in_(ids)).values(owner_id=person.id))
    # Their old share goes (they own it now); the old owner becomes Co-owner.
    s.delete(new_owner_share)
    s.flush()
    old = s.scalar(select(Share).where(Share.item_id == item.id, Share.email == actor.email))
    if old is None:
        s.add(Share(item_id=item.id, email=actor.email, user_id=actor.id, role=permissions.COOWNER,
                    invited_by_id=person.id, created_at=moment))
    else:
        old.role, old.user_id = permissions.COOWNER, actor.id
    item.name = files.unique_name(s, person.id, None, item.name, skip_id=item.id)
    s.flush()


def total_size(s, item):
    from .models import FileVersion
    ids = tree.descendant_ids(s, item.id) if item.is_folder else [item.id]
    return s.scalar(select(func.coalesce(func.sum(FileVersion.size), 0)).where(FileVersion.item_id.in_(ids))) or 0


def init_app(app):
    # Module-level lists, and tests make many apps: add each hook once.
    if role_from_shares not in permissions.role_sources:
        permissions.role_sources.append(role_from_shares)
    if claim_waiting not in auth.on_verified:
        auth.on_verified.append(claim_waiting)
    app.jinja_env.globals["a_role"] = a_role


def people_count(s, item):
    return s.scalar(select(func.count()).select_from(Share).where(Share.item_id == item.id)) or 0


def users_by_id(s, ids):
    return {user.id: user for user in s.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
