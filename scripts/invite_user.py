"""Invite one Google account by email; requires an operator-owned DATABASE_URL."""

import argparse
import os
import re

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()
    email = args.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise SystemExit("Provide a valid email address")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL before inviting users")
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO cycling_invites (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
            (email,),
        )
    print(f"Invited {email}")


if __name__ == "__main__":
    main()
