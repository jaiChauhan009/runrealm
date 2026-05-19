-- ============================================================
--  RunRealm — Supabase schema
--  Run this in Supabase Dashboard → SQL Editor
-- ============================================================

-- user_profiles (extends auth.users)
CREATE TABLE IF NOT EXISTS user_profiles (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    username         TEXT UNIQUE NOT NULL,
    display_name     TEXT,
    avatar_url       TEXT,
    bio              TEXT,
    city             TEXT,
    level            INTEGER DEFAULT 1,
    xp_points        INTEGER DEFAULT 0,
    total_runs       INTEGER DEFAULT 0,
    total_calories   INTEGER DEFAULT 0,
    current_streak   INTEGER DEFAULT 0,
    best_streak      INTEGER DEFAULT 0,
    territory_owned_sq_km  FLOAT DEFAULT 0,
    territories_captured   INTEGER DEFAULT 0,
    is_public        BOOLEAN DEFAULT TRUE,
    device_id        TEXT,
    fcm_token        TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- streaks
CREATE TABLE IF NOT EXISTS streaks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    current_streak      INTEGER DEFAULT 0,
    best_streak         INTEGER DEFAULT 0,
    last_activity_date  DATE,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- run_sessions
CREATE TABLE IF NOT EXISTS run_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id            TEXT,
    user_id             UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    activity_type       TEXT NOT NULL,
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ,
    duration_seconds    INTEGER,
    distance_km         FLOAT DEFAULT 0,
    avg_pace_min_per_km FLOAT,
    max_speed_kmh       FLOAT,
    calories_burned     INTEGER DEFAULT 0,
    elevation_gain_m    FLOAT DEFAULT 0,
    route_geo_json      TEXT,
    xp_earned           INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'ACTIVE',
    synced              BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, local_id)
);

-- route_points
CREATE TABLE IF NOT EXISTS route_points (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id        TEXT,
    session_id      UUID REFERENCES run_sessions(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    latitude        FLOAT NOT NULL,
    longitude       FLOAT NOT NULL,
    altitude        FLOAT,
    speed_kmh       FLOAT,
    accuracy_m      FLOAT,
    sequence_number INTEGER,
    recorded_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- territories
CREATE TABLE IF NOT EXISTS territories (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    center_lat        FLOAT NOT NULL,
    center_lon        FLOAT NOT NULL,
    boundary_geo_json TEXT,
    area_sq_km        FLOAT DEFAULT 0,
    captured_by       UUID REFERENCES auth.users(id),
    captured_at       TIMESTAMPTZ,
    team_id           UUID,
    point_value       INTEGER DEFAULT 100,
    capture_count     INTEGER DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- territory_captures
CREATE TABLE IF NOT EXISTS territory_captures (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    territory_id      UUID REFERENCES territories(id),
    user_id           UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id        UUID REFERENCES run_sessions(id),
    previous_owner_id UUID REFERENCES auth.users(id),
    xp_earned         INTEGER DEFAULT 50,
    captured_at       TIMESTAMPTZ DEFAULT NOW()
);

-- teams
CREATE TABLE IF NOT EXISTS teams (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 TEXT UNIQUE NOT NULL,
    description          TEXT,
    territory_color_hex  TEXT,
    created_by           UUID REFERENCES auth.users(id),
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- team_members
CREATE TABLE IF NOT EXISTS team_members (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id   UUID REFERENCES teams(id) ON DELETE CASCADE,
    user_id   UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    role      TEXT DEFAULT 'MEMBER',
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, user_id)
);

-- habits
CREATE TABLE IF NOT EXISTS habits (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT,
    habit_type   TEXT NOT NULL,
    target_value FLOAT,
    unit         TEXT,
    frequency    TEXT DEFAULT 'DAILY',
    is_active    BOOLEAN DEFAULT TRUE,
    icon         TEXT,
    color_hex    TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- habit_logs
CREATE TABLE IF NOT EXISTS habit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id        TEXT,
    habit_id        UUID REFERENCES habits(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    log_date        DATE NOT NULL,
    completed_value FLOAT DEFAULT 0,
    is_completed    BOOLEAN DEFAULT FALSE,
    xp_earned       INTEGER DEFAULT 0,
    notes           TEXT,
    synced          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(habit_id, log_date)
);

-- xp_transactions
CREATE TABLE IF NOT EXISTS xp_transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    amount           INTEGER NOT NULL,
    transaction_type TEXT NOT NULL,
    reference_id     UUID,
    description      TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- activity_feed
CREATE TABLE IF NOT EXISTS activity_feed (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    activity_type TEXT NOT NULL,
    reference_id  UUID,
    message       TEXT,
    metadata_json TEXT,
    is_public     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- user_friends
CREATE TABLE IF NOT EXISTS user_friends (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    friend_id   UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    status      TEXT DEFAULT 'PENDING',
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, friend_id)
);

-- notifications
CREATE TABLE IF NOT EXISTS notifications (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    body              TEXT,
    notification_type TEXT NOT NULL,
    is_read           BOOLEAN DEFAULT FALSE,
    reference_id      UUID,
    deep_link         TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- sync_queue
CREATE TABLE IF NOT EXISTS sync_queue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    entity_type   TEXT NOT NULL,
    operation     TEXT NOT NULL,
    local_id      TEXT NOT NULL,
    server_id     UUID,
    payload       TEXT,
    status        TEXT DEFAULT 'PENDING',
    error_message TEXT,
    occurred_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, local_id)
);

-- achievements
CREATE TABLE IF NOT EXISTS achievements (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    rarity      TEXT DEFAULT 'COMMON',
    xp_reward   INTEGER DEFAULT 0,
    icon        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- user_achievements
CREATE TABLE IF NOT EXISTS user_achievements (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    achievement_id UUID REFERENCES achievements(id),
    earned_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, achievement_id)
);

-- ── Disable RLS for development (re-enable + add policies for production) ──
ALTER TABLE user_profiles      DISABLE ROW LEVEL SECURITY;
ALTER TABLE streaks             DISABLE ROW LEVEL SECURITY;
ALTER TABLE run_sessions        DISABLE ROW LEVEL SECURITY;
ALTER TABLE route_points        DISABLE ROW LEVEL SECURITY;
ALTER TABLE territories         DISABLE ROW LEVEL SECURITY;
ALTER TABLE territory_captures  DISABLE ROW LEVEL SECURITY;
ALTER TABLE teams               DISABLE ROW LEVEL SECURITY;
ALTER TABLE team_members        DISABLE ROW LEVEL SECURITY;
ALTER TABLE habits              DISABLE ROW LEVEL SECURITY;
ALTER TABLE habit_logs          DISABLE ROW LEVEL SECURITY;
ALTER TABLE xp_transactions     DISABLE ROW LEVEL SECURITY;
ALTER TABLE activity_feed       DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_friends        DISABLE ROW LEVEL SECURITY;
ALTER TABLE notifications       DISABLE ROW LEVEL SECURITY;
ALTER TABLE sync_queue          DISABLE ROW LEVEL SECURITY;
ALTER TABLE achievements        DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_achievements   DISABLE ROW LEVEL SECURITY;

-- ── Seed a few territories so the map has data ────────────────────────────
INSERT INTO territories (name, center_lat, center_lon, area_sq_km, point_value)
VALUES
  ('Connaught Place',   28.6315,  77.2167, 1.2, 150),
  ('India Gate',        28.6129,  77.2295, 0.8, 100),
  ('Lodi Garden',       28.5931,  77.2200, 0.9, 120),
  ('Nehru Place',       28.5491,  77.2513, 0.6, 80),
  ('Saket District',    28.5213,  77.2133, 1.1, 110)
ON CONFLICT DO NOTHING;
