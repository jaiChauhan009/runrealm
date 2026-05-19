-- ============================================================
--  RunRealm — add location tracking columns
--  Run in Supabase Dashboard → SQL Editor
-- ============================================================

-- Add last known location to user_profiles
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS last_lat   FLOAT,
    ADD COLUMN IF NOT EXISTS last_lon   FLOAT,
    ADD COLUMN IF NOT EXISTS last_location_at TIMESTAMPTZ;

-- Index for bounding-box queries (fast nearby-user lookup)
CREATE INDEX IF NOT EXISTS idx_user_profiles_location
    ON user_profiles (last_lat, last_lon)
    WHERE last_lat IS NOT NULL;
