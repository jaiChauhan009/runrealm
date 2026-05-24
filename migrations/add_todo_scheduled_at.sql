-- ============================================================
--  RunRealm — todo scheduled_at field
--  Run in Supabase Dashboard → SQL Editor
-- ============================================================

ALTER TABLE daily_todos
    ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_daily_todos_scheduled_at
    ON daily_todos (user_id, scheduled_at)
    WHERE scheduled_at IS NOT NULL;
