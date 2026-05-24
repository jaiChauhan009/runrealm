-- ============================================================
--  RunRealm — todo status field
--  Run in Supabase Dashboard → SQL Editor
-- ============================================================

-- Add status column: PENDING | DONE | CANCELLED | DEFERRED
ALTER TABLE daily_todos
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'PENDING';

-- Backfill from existing is_completed flag
UPDATE daily_todos SET status = 'DONE'    WHERE is_completed = TRUE  AND status = 'PENDING';
UPDATE daily_todos SET status = 'PENDING' WHERE is_completed = FALSE AND status IS NULL;

-- Optional index for filtering by status
CREATE INDEX IF NOT EXISTS idx_daily_todos_status
    ON daily_todos (user_id, todo_date, status);
