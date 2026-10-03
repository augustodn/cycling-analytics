CREATE TABLE cycling_users (
    user_id text PRIMARY KEY CHECK (length(user_id) BETWEEN 1 AND 256),
    google_sub text NOT NULL UNIQUE CHECK (length(google_sub) BETWEEN 1 AND 256),
    email text NOT NULL UNIQUE CHECK (email = lower(email)),
    email_verified boolean NOT NULL CHECK (email_verified),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_login_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE cycling_invites (
    email text PRIMARY KEY CHECK (email = lower(email)),
    invited_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    accepted_user_id text UNIQUE REFERENCES cycling_users(user_id),
    accepted_at timestamptz
);
