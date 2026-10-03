"""Apply PostgreSQL SQL migrations using DATABASE_URL."""

import os

import psycopg

from cycling.migrations import apply_migrations


def main() -> None:
    database_url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get(
        "DATABASE_URL"
    )
    if not database_url:
        raise SystemExit("Set DATABASE_URL before running migrations")
    with psycopg.connect(database_url, autocommit=True) as connection:
        versions = apply_migrations(connection)
    print("Applied migrations: " + (", ".join(versions) if versions else "none"))


if __name__ == "__main__":
    main()
