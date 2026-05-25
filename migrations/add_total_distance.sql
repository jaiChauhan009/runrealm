-- Add total_distance_km to user_profiles so profile reads don't scan all sessions.
-- Run this in Supabase Dashboard → SQL Editor.

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS total_distance_km FLOAT DEFAULT 0;

-- Back-fill from existing completed sessions (run once after adding the column)
UPDATE user_profiles up
SET total_distance_km = COALESCE((
  SELECT SUM(distance_km)
  FROM run_sessions rs
  WHERE rs.user_id = up.user_id
    AND rs.status = 'COMPLETED'
), 0);
