"""Apply ordered SQL migrations using one transaction per version."""

import re
from pathlib import Path

MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"


def apply_migrations(connection, directory: Path = MIGRATION_DIR) -> list[str]:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cycling_schema_migrations "
        "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
    )
    applied = {
        row[0]
        for row in connection.execute(
            "SELECT version FROM cycling_schema_migrations"
        ).fetchall()
    }
    newly_applied = []
    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        version = path.name[:3]
        if version in applied:
            continue
        source = path.read_text()
        if re.search(r"^\s*(BEGIN|COMMIT)\s*;", source, re.IGNORECASE | re.MULTILINE):
            raise ValueError(f"Migration controls its own transaction: {path.name}")
        statements = [
            statement.strip()
            for statement in re.split(r";[ \t]*(?=\r?\n|$)", source)
            if statement.strip()
            and not all(
                not line.strip() or line.lstrip().startswith("--")
                for line in statement.splitlines()
            )
        ]
        with connection.transaction():
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO cycling_schema_migrations (version) VALUES (%s)",
                (version,),
            )
        newly_applied.append(version)
    return newly_applied
