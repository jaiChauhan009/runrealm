-- ============================================================
--  RunRealm — leagues system
--  Run in Supabase Dashboard → SQL Editor
-- ============================================================

CREATE TABLE IF NOT EXISTS leagues (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    description TEXT,
    scope       TEXT        NOT NULL DEFAULT 'GLOBAL',
    creator_id  UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    social_links JSONB      DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS league_members (
    league_id  UUID        NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    user_id    UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL DEFAULT 'MEMBER', -- CREATOR | LEADER | MEMBER
    joined_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS league_join_requests (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id    UUID        NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    status       TEXT        NOT NULL DEFAULT 'PENDING', -- PENDING | ACCEPTED | REJECTED
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS league_delete_votes (
    league_id  UUID        NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    user_id    UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    voted_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_leagues_scope           ON leagues(scope);
CREATE INDEX IF NOT EXISTS idx_league_members_user     ON league_members(user_id);
CREATE INDEX IF NOT EXISTS idx_league_members_league   ON league_members(league_id);
CREATE INDEX IF NOT EXISTS idx_join_requests_league    ON league_join_requests(league_id, status);
CREATE INDEX IF NOT EXISTS idx_delete_votes_league     ON league_delete_votes(league_id);

-- Disable RLS — consistent with all other tables in this project (app layer handles isolation)
ALTER TABLE leagues              DISABLE ROW LEVEL SECURITY;
ALTER TABLE league_members       DISABLE ROW LEVEL SECURITY;
ALTER TABLE league_join_requests DISABLE ROW LEVEL SECURITY;
ALTER TABLE league_delete_votes  DISABLE ROW LEVEL SECURITY;
