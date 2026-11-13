"""The Alembic migrations make exactly the tables that models.py describes."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from jdrive import create_app, db
from jdrive.db import Base


def test_upgrade_on_an_empty_database_matches_the_models(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fresh.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.upgrade_db()
        db.upgrade_db()  # running it again changes nothing
        with db.engine().connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
            tables = set(inspect(connection).get_table_names())
    # Nothing left for "alembic revision --autogenerate" to write: the
    # migrations and the models agree. If this fails after changing
    # models.py, make a new migration (see AGENTS.md).
    assert differences == []
    assert {"users", "email_codes", "alembic_version"} <= tables


def test_upgrade_db_command(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    result = app.test_cli_runner().invoke(args=["upgrade-db"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output
