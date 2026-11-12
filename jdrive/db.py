"""The database: one SQLAlchemy engine per app and one session per request.

Locally and in tests it is a SQLite file in instance/ (git ignores it).
On the live site DATABASE_URL points at PostgreSQL (Neon). The models and
queries are the same for both: SQLAlchemy writes the right SQL for each.

Use it like this in a view:

    s = db.session()
    item = s.get(Item, 7)
    item.name = "Hello.txt"
    s.commit()          # nothing is saved without commit()

The session is closed at the end of every request (close_session).
"""
from pathlib import Path

import click
from alembic import command
from alembic.config import Config
from flask import current_app, g
from flask.cli import with_appcontext
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

MIGRATIONS = Path(__file__).parent / "migrations"

# Every constraint gets a predictable name (uq_users_email, ...).
# Alembic needs names to change or drop a constraint in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The parent class of every model in models.py."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def database_url(config):
    """The SQLAlchemy URL: Neon's DATABASE_URL if set, else the SQLite file."""
    url = config.get("DATABASE_URL")
    if not url:
        return "sqlite:///" + config["DATABASE"]
    # Neon gives "postgresql://..." (some hosts "postgres://..."). SQLAlchemy
    # also needs the driver's name, and ours is psycopg (version 3).
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url):
    # pool_pre_ping: Neon closes idle connections, so check one before using it.
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def turn_on_foreign_keys(dbapi_connection, _record):
            # SQLite ignores FOREIGN KEY rules unless asked, per connection.
            dbapi_connection.execute("PRAGMA foreign_keys = ON")
    return engine


def engine():
    return current_app.extensions["jdrive_engine"]


def session() -> Session:
    """This request's session (made on first use)."""
    if "db" not in g:
        # expire_on_commit=False: objects keep their values after commit(),
        # so a view can still read item.name after saving it.
        g.db = Session(engine(), expire_on_commit=False)
    return g.db


def close_session(_error=None):
    s = g.pop("db", None)
    if s is not None:
        s.close()


# ---------- Making and upgrading the tables ----------

def create_tables():
    """Make every table straight from the models (tests use this; it's fast)."""
    from . import models  # noqa: F401  (registers the tables on Base.metadata)
    Base.metadata.create_all(engine())


def drop_tables():
    from . import models  # noqa: F401
    Base.metadata.drop_all(engine())


def alembic_config():
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def upgrade_db(revision="head"):
    """Run the Alembic migrations that this database hasn't had yet.

    It keeps all data, and does nothing when the tables are up to date.
    Render runs it before every start (render.yaml).
    """
    config = alembic_config()
    with engine().connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite changes a table by copying it and DROPPING the old one. With
            # foreign keys on, that drop deletes every row pointing at the old table
            # (ON DELETE CASCADE): changing "users" would empty every other table.
            # The switch only works outside a transaction, so commit straight after.
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection  # migrations/env.py uses it
                command.upgrade(config, revision)
            if sqlite:
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"The upgrade left {len(broken)} broken links between tables: {broken[:5]}")
        finally:
            if sqlite:
                connection.rollback()
                connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                connection.commit()


@click.command("upgrade-db")
@with_appcontext
def upgrade_db_command():
    """Make or update the tables."""
    upgrade_db()
    click.echo("The database is up to date.")


def init_app(app):
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.extensions["jdrive_engine"] = make_engine(database_url(app.config))
    app.teardown_appcontext(close_session)
    app.cli.add_command(upgrade_db_command)
