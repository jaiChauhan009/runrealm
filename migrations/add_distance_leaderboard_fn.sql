-- Distance leaderboard aggregation function.
-- Replaces the Python-side aggregation that fetched every run_sessions row.
-- Run this once in Supabase SQL Editor (Dashboard → SQL Editor → New Query).

CREATE OR REPLACE FUNCTION get_distance_leaderboard(scope_ids uuid[] DEFAULT NULL)
RETURNS TABLE(user_id text, total_km double precision)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT
    rs.user_id::text,
    COALESCE(SUM(rs.distance_km), 0) AS total_km
  FROM run_sessions rs
  WHERE rs.status = 'COMPLETED'
    AND (scope_ids IS NULL OR rs.user_id = ANY(scope_ids))
  GROUP BY rs.user_id
  ORDER BY total_km DESC;
$$;

-- Allow authenticated users to call this function via PostgREST RPC
GRANT EXECUTE ON FUNCTION get_distance_leaderboard(uuid[]) TO authenticated;
