-- Apply once, after identity migrations, using a migration role/direct connection.
-- The authenticated subject is an opaque text ID supplied by the auth boundary.
-- One catalog athlete per subject; no account creation or personal defaults here.
CREATE TABLE cycling_athletes (
    user_id text PRIMARY KEY REFERENCES cycling_users(user_id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE cycling_parameters (
    user_id text NOT NULL REFERENCES cycling_athletes(user_id),
    id text NOT NULL,
    effective_date date NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    settings jsonb NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX ON cycling_parameters (user_id, effective_date DESC, recorded_at DESC, id DESC);

-- Objects, not their contents. Keys are generated and owner-validated in the store.
CREATE TABLE cycling_files (
    user_id text NOT NULL REFERENCES cycling_athletes(user_id),
    object_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('samples', 'originals')),
    sha256 text NOT NULL,
    size bigint NOT NULL CHECK (size >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, object_key)
);

CREATE TABLE cycling_activities (
    user_id text NOT NULL REFERENCES cycling_athletes(user_id),
    id text NOT NULL,
    source_hash text NOT NULL,
    strava_id_hint text,
    start_time text NOT NULL,
    duration_s bigint NOT NULL CHECK (duration_s >= 0),
    modality text NOT NULL,
    metadata jsonb NOT NULL,
    sample_path text NOT NULL,
    original_path text,
    normalizer_version text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, source_hash),
    FOREIGN KEY (user_id, sample_path) REFERENCES cycling_files(user_id, object_key),
    FOREIGN KEY (user_id, original_path) REFERENCES cycling_files(user_id, object_key)
);
CREATE INDEX ON cycling_activities (user_id, start_time DESC);

CREATE TABLE cycling_activity_context (
    user_id text NOT NULL,
    id text NOT NULL,
    activity_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    context jsonb NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, activity_id, id),
    FOREIGN KEY (user_id, activity_id) REFERENCES cycling_activities(user_id, id)
);
CREATE INDEX ON cycling_activity_context (user_id, activity_id, recorded_at DESC, id DESC);

CREATE TABLE cycling_metrics (
    user_id text NOT NULL,
    id text NOT NULL,
    activity_id text NOT NULL,
    cache_key text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    parameter_id text,
    context_id text,
    sample_path text NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, activity_id) REFERENCES cycling_activities(user_id, id),
    FOREIGN KEY (user_id, parameter_id) REFERENCES cycling_parameters(user_id, id),
    FOREIGN KEY (user_id, activity_id, context_id)
        REFERENCES cycling_activity_context(user_id, activity_id, id),
    FOREIGN KEY (user_id, sample_path) REFERENCES cycling_files(user_id, object_key)
);
-- Append-only snapshots, including forced recomputations of the same key.
CREATE INDEX ON cycling_metrics (user_id, activity_id, cache_key, computed_at DESC);

CREATE TABLE cycling_quality_flags (
    user_id text NOT NULL,
    activity_id text NOT NULL,
    flag text NOT NULL,
    PRIMARY KEY (user_id, activity_id, flag),
    FOREIGN KEY (user_id, activity_id) REFERENCES cycling_activities(user_id, id)
);

CREATE TABLE cycling_activity_laps (
    user_id text NOT NULL,
    activity_id text NOT NULL,
    lap_index integer NOT NULL,
    lap jsonb NOT NULL,
    PRIMARY KEY (user_id, activity_id, lap_index),
    FOREIGN KEY (user_id, activity_id) REFERENCES cycling_activities(user_id, id)
);

CREATE TABLE cycling_sources (
    user_id text NOT NULL REFERENCES cycling_athletes(user_id),
    path text NOT NULL,
    hash text,
    size bigint CHECK (size >= 0),
    status text NOT NULL,
    error text,
    last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, path)
);
CREATE INDEX ON cycling_sources (user_id, hash);

CREATE TABLE cycling_processing_runs (
    user_id text NOT NULL REFERENCES cycling_athletes(user_id),
    id text NOT NULL,
    run_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    status text NOT NULL,
    summary jsonb NOT NULL,
    PRIMARY KEY (user_id, id)
);

-- Daily training load is calculated by CyclingService, not persisted or cached.
