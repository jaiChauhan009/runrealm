-- ============================================================
--  RunRealm — social links + daily todos
--  Run in Supabase Dashboard → SQL Editor
-- ============================================================

-- Social media links on user_profiles
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS instagram_handle  TEXT,
    ADD COLUMN IF NOT EXISTS twitter_handle    TEXT,
    ADD COLUMN IF NOT EXISTS strava_url        TEXT,
    ADD COLUMN IF NOT EXISTS linkedin_url      TEXT;

-- Daily todos
CREATE TABLE IF NOT EXISTS daily_todos (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT,
    todo_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    is_completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    category     TEXT DEFAULT 'GENERAL',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE daily_todos DISABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_daily_todos_user_date
    ON daily_todos (user_id, todo_date);
