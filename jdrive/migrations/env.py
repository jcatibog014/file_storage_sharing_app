"""Alembic runs this file for every migration command.

The app's upgrade-db command hands us an open connection
(config.attributes["connection"]). From the command line,

    .venv\\Scripts\\alembic revision --autogenerate -m "add something"

we build the same database address the app uses (DATABASE_URL, or the
SQLite file in instance/).
"""
from alembic import context

from jdrive import models  # noqa: F401  (puts every table on Base.metadata)
from jdrive.db import Base, database_url, make_engine


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        # SQLite can't ALTER most things in place; "batch" mode copies the table instead.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    from jdrive import create_app

    app = create_app()
    engine = make_engine(database_url(app.config))
    with engine.begin() as new_connection:
        run(new_connection)
