"""Disable an existing account by its invited Google email."""

import argparse
import os

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    email = parser.parse_args().email.strip().lower()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL before revoking users")
    with psycopg.connect(database_url) as connection:
        result = connection.execute(
            "UPDATE cycling_users SET is_active=false WHERE email=%s RETURNING user_id",
            (email,),
        ).fetchone()
        if result is None:
            raise SystemExit("No account found for that email")
    print(f"Disabled {email}")


if __name__ == "__main__":
    main()
