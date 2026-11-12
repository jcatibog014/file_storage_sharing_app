"""Every role against every action, through the real pages. Add a row for every new action."""
import pytest

from jdrive import db, permissions, sharing
from jdrive.dates import utcnow
from jdrive.models import Item, User

from .conftest import CSRF, browser, get_item, make_user, new_folder, post, upload


def test_the_rule_table():
    table = {
        "viewer": {"view", "download"},
        "editor": {"view", "download", "upload", "new_folder", "rename", "move", "new_version", "trash"},
        "coowner": {"view", "download", "upload", "new_folder", "rename", "move", "new_version", "trash",
                    "share", "restore", "delete_version"},
        "owner": set(permissions.NEEDS),
    }
    for role, allowed in table.items():
        for action in permissions.NEEDS:
            assert permissions.allows(role, action) == (action in allowed), (role, action)
    for action in permissions.NEEDS:
        assert not permissions.allows(None, action)


@pytest.fixture
def team(app):
    """Maria owns Team/ (with Inner/ and plan.txt). Ana is Viewer, Ben Editor, Carlo Co-owner; Jose is a stranger."""
    people = {name: make_user(app, full) for name, full in
              (("owner", "Maria Santos"), ("viewer", "Ana Reyes"), ("editor", "Ben Torres"),
               ("coowner", "Carlo Diaz"), ("stranger", "Jose Rizal"))}
    maria = browser(app, people["owner"])
    team_id = new_folder(maria, "Team")
    inner_id = new_folder(maria, "Inner", team_id)
    file_id = upload(maria, "plan.txt", b"the plan", parent_id=team_id)["item"]["id"]
    with app.app_context():
        s = db.session()
        owner, team = s.get(User, people["owner"]), s.get(Item, team_id)
        for role in ("viewer", "editor", "coowner"):
            sharing.add_people(s, owner, team, [s.get(User, people[role]).email], role, utcnow())
        s.commit()
    return {"people": people, "team": team_id, "inner": inner_id, "file": file_id}


ROLES = ("owner", "coowner", "editor", "viewer", "stranger")


def expect(role, least):
    """What a role should get when the action needs `least`."""
    if role == "stranger":
        return "hidden"
    return "ok" if permissions.RANK[role] >= permissions.RANK[least] else "refused"


def outcome(status):
    return {200: "ok", 302: "ok", 403: "refused", 404: "hidden"}.get(status, status)


@pytest.mark.parametrize("role", ROLES)
def test_who_can_do_what(app, team, role):
    client = browser(app, team["people"][role])
    file_id, team_id, inner_id = team["file"], team["team"], team["inner"]
    checks = [
        ("view", "viewer", lambda: client.get(f"/folders/{team_id}").status_code),
        ("view a folder inside", "viewer", lambda: client.get(f"/folders/{inner_id}").status_code),
        ("view the file", "viewer", lambda: client.get(f"/files/{file_id}").status_code),
        ("download", "viewer", lambda: client.get(f"/files/{file_id}/download").status_code),
        ("new folder", "editor", lambda: post(client, "/folders/new", {"parent_id": team_id, "name": f"by {role}"}).status_code),
        ("upload", "editor", lambda: client.post("/api/uploads", json={"parent_id": team_id, "name": "x.txt", "size": 1},
                                                headers={"X-CSRF-Token": CSRF}).status_code),
        ("rename", "editor", lambda: post(client, f"/items/{inner_id}/rename", {"name": f"Inner {role}"}).status_code),
        ("move", "editor", lambda: client.get(f"/items/{file_id}/move").status_code),
        ("share page", "coowner", lambda: client.get(f"/items/{team_id}/share").status_code),
        ("move to trash", "editor", lambda: post(client, f"/items/{inner_id}/trash").status_code),
    ]
    for name, least, run in checks:
        got = outcome(run())
        assert got == expect(role, least), (role, name, got)


def test_an_editors_upload_belongs_to_the_folder_owner(app, team):
    ben = browser(app, team["people"]["editor"])
    answer = upload(ben, "ben.txt", b"hi", parent_id=team["team"])
    item = get_item(app, answer["item"]["id"])
    assert item.owner_id == team["people"]["owner"]
    assert item.created_by_id == team["people"]["editor"]


def test_shared_people_never_see_the_owners_folders_above(app):
    maria = browser(app, make_user(app))
    ana_id = make_user(app, "Ana Reyes")
    private = new_folder(maria, "Private Stuff")
    shared = new_folder(maria, "For Ana", private)
    post(maria, f"/items/{shared}/share", {"action": "add", "emails": "ana@example.com", "role": "viewer"})
    ana = browser(app, ana_id)
    page = ana.get(f"/folders/{shared}").get_data(as_text=True)
    assert "Shared With Me" in page and "Private Stuff" not in page
    assert ana.get(f"/folders/{private}").status_code == 404


def test_the_highest_role_wins(app, team):
    """Ana is a Viewer of Team, and an Editor of Inner: she can edit inside Inner only."""
    with app.app_context():
        s = db.session()
        sharing.add_people(s, s.get(User, team["people"]["owner"]), s.get(Item, team["inner"]),
                           ["ana@example.com"], "editor", utcnow())
        s.commit()
    ana = browser(app, team["people"]["viewer"])
    assert post(ana, "/folders/new", {"parent_id": team["inner"], "name": "Ana's"}).status_code == 302
    assert post(ana, "/folders/new", {"parent_id": team["team"], "name": "Ana's"}).status_code == 403


def test_removing_a_share_takes_access_away(app, team):
    maria = browser(app, team["people"]["owner"])
    page = maria.get(f"/items/{team['team']}/share").get_data(as_text=True)
    assert "ana@example.com" in page
    with app.app_context():
        from jdrive.models import Share
        share_id = db.session().query(Share).filter_by(email="ana@example.com").one().id
    post(maria, f"/items/{team['team']}/share", {"action": "remove", "share_id": share_id})
    assert browser(app, team["people"]["viewer"]).get(f"/folders/{team['team']}").status_code == 404


def test_coowners_manage_people_but_not_the_owner(app, team):
    carlo = browser(app, team["people"]["coowner"])
    response = post(carlo, f"/items/{team['team']}/share", {"action": "add", "emails": "maria@example.com",
                                                            "role": "viewer"})
    assert "owner already" in response.get_data(as_text=True)
    assert post(carlo, f"/items/{team['team']}/share", {"action": "add", "emails": "jose@example.com",
                                                        "role": "editor"}).status_code == 302
    assert browser(app, team["people"]["stranger"]).get(f"/folders/{team['team']}").status_code == 200
    # Only the Owner passes ownership on.
    with app.app_context():
        from jdrive.models import Share
        share_id = db.session().query(Share).filter_by(email="ben@example.com").one().id
    assert post(carlo, f"/items/{team['team']}/share", {"action": "transfer", "share_id": share_id}).status_code == 403


def test_a_share_id_from_another_item_is_404(app, team):
    maria = browser(app, team["people"]["owner"])
    other = new_folder(maria, "Other")
    with app.app_context():
        from jdrive.models import Share
        share_id = db.session().query(Share).filter_by(email="ana@example.com").one().id
    assert post(maria, f"/items/{other}/share", {"action": "remove", "share_id": share_id}).status_code == 404
