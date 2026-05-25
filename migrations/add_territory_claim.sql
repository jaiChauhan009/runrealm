-- Territory polygon claim system migration
-- Run in Supabase Dashboard → SQL Editor

-- Add new columns to territories table
ALTER TABLE territories ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS min_lat FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS max_lat FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS min_lon FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS max_lon FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS perimeter_km FLOAT DEFAULT 0;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIVE';
ALTER TABLE territories ADD COLUMN IF NOT EXISTS avg_speed_kmh FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS max_speed_kmh FLOAT;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS point_count INT DEFAULT 0;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS validation_score FLOAT DEFAULT 1.0;
ALTER TABLE territories ADD COLUMN IF NOT EXISTS point_value INT DEFAULT 100;

-- Back-fill status for existing rows
UPDATE territories SET status = 'ACTIVE' WHERE status IS NULL;

-- Back-fill bounding box from center for existing territories (rough estimate)
UPDATE territories
SET
    min_lat = center_lat - 0.002,
    max_lat = center_lat + 0.002,
    min_lon = center_lon - 0.002,
    max_lon = center_lon + 0.002
WHERE min_lat IS NULL AND center_lat IS NOT NULL;

-- Indexes for bounding box overlap queries and status filtering
CREATE INDEX IF NOT EXISTS territories_bbox_idx    ON territories(min_lat, max_lat, min_lon, max_lon);
CREATE INDEX IF NOT EXISTS territories_status_idx  ON territories(status);
CREATE INDEX IF NOT EXISTS territories_session_idx ON territories(session_id);
CREATE INDEX IF NOT EXISTS territories_owner_idx   ON territories(captured_by, status);
