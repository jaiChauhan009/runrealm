-- ============================================================
--  RunRealm — Enable RLS on all tables
--  Run in Supabase Dashboard → SQL Editor
--
--  The FastAPI backend uses service_role and bypasses RLS,
--  so this ONLY affects direct PostgREST access (anon/authenticated).
--  Nothing in the backend breaks.
-- ============================================================


-- ── 1. Enable RLS on every table ─────────────────────────────────────────────

ALTER TABLE user_profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE streaks              ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_sessions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE route_points         ENABLE ROW LEVEL SECURITY;
ALTER TABLE territories          ENABLE ROW LEVEL SECURITY;
ALTER TABLE territory_captures   ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams                ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members         ENABLE ROW LEVEL SECURITY;
ALTER TABLE habits               ENABLE ROW LEVEL SECURITY;
ALTER TABLE habit_logs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE xp_transactions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_feed        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_friends         ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications        ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_queue           ENABLE ROW LEVEL SECURITY;
ALTER TABLE achievements         ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_achievements    ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_todos          ENABLE ROW LEVEL SECURITY;
ALTER TABLE leagues              ENABLE ROW LEVEL SECURITY;
ALTER TABLE league_members       ENABLE ROW LEVEL SECURITY;
ALTER TABLE league_join_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE league_delete_votes  ENABLE ROW LEVEL SECURITY;


-- ── 2. user_profiles ─────────────────────────────────────────────────────────
-- Public profiles readable by anyone signed in; only owner can write.

CREATE POLICY "profiles_select" ON user_profiles
  FOR SELECT TO authenticated
  USING (is_public = TRUE OR user_id = auth.uid());

CREATE POLICY "profiles_insert" ON user_profiles
  FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "profiles_update" ON user_profiles
  FOR UPDATE TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY "profiles_delete" ON user_profiles
  FOR DELETE TO authenticated
  USING (user_id = auth.uid());


-- ── 3. streaks ───────────────────────────────────────────────────────────────

CREATE POLICY "streaks_owner" ON streaks
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 4. run_sessions ──────────────────────────────────────────────────────────

CREATE POLICY "run_sessions_owner" ON run_sessions
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 5. route_points ──────────────────────────────────────────────────────────
-- GPS data — strictly private. Fixes "Sensitive Columns Exposed" warning.

CREATE POLICY "route_points_owner" ON route_points
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 6. territories ───────────────────────────────────────────────────────────
-- Public game map data — anyone signed in can read. Only backend writes.
-- Fixes "Sensitive Columns Exposed" for center_lat/center_lon (still readable,
-- but now scoped to authenticated users only, not fully anonymous).

CREATE POLICY "territories_select" ON territories
  FOR SELECT TO authenticated
  USING (TRUE);

-- No direct INSERT/UPDATE/DELETE from clients — backend (service_role) handles it.


-- ── 7. territory_captures ────────────────────────────────────────────────────

CREATE POLICY "territory_captures_select" ON territory_captures
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "territory_captures_insert" ON territory_captures
  FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());


-- ── 8. teams ─────────────────────────────────────────────────────────────────

CREATE POLICY "teams_select" ON teams
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "teams_insert" ON teams
  FOR INSERT TO authenticated
  WITH CHECK (created_by = auth.uid());

CREATE POLICY "teams_update" ON teams
  FOR UPDATE TO authenticated
  USING (created_by = auth.uid());

CREATE POLICY "teams_delete" ON teams
  FOR DELETE TO authenticated
  USING (created_by = auth.uid());


-- ── 9. team_members ──────────────────────────────────────────────────────────

CREATE POLICY "team_members_select" ON team_members
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "team_members_write" ON team_members
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 10. habits ───────────────────────────────────────────────────────────────

CREATE POLICY "habits_owner" ON habits
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 11. habit_logs ───────────────────────────────────────────────────────────

CREATE POLICY "habit_logs_owner" ON habit_logs
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 12. xp_transactions ──────────────────────────────────────────────────────

CREATE POLICY "xp_transactions_owner" ON xp_transactions
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 13. activity_feed ────────────────────────────────────────────────────────

CREATE POLICY "activity_feed_select" ON activity_feed
  FOR SELECT TO authenticated
  USING (is_public = TRUE OR user_id = auth.uid());

CREATE POLICY "activity_feed_write" ON activity_feed
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 14. user_friends ─────────────────────────────────────────────────────────

CREATE POLICY "user_friends_select" ON user_friends
  FOR SELECT TO authenticated
  USING (user_id = auth.uid() OR friend_id = auth.uid());

CREATE POLICY "user_friends_write" ON user_friends
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 15. notifications ────────────────────────────────────────────────────────

CREATE POLICY "notifications_owner" ON notifications
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 16. sync_queue ───────────────────────────────────────────────────────────

CREATE POLICY "sync_queue_owner" ON sync_queue
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 17. achievements (catalog table) ─────────────────────────────────────────
-- Read-only for all authenticated users; only service_role inserts.

CREATE POLICY "achievements_select" ON achievements
  FOR SELECT TO authenticated
  USING (TRUE);


-- ── 18. user_achievements ────────────────────────────────────────────────────

CREATE POLICY "user_achievements_select" ON user_achievements
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "user_achievements_write" ON user_achievements
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 19. daily_todos ──────────────────────────────────────────────────────────

CREATE POLICY "daily_todos_owner" ON daily_todos
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 20. leagues ──────────────────────────────────────────────────────────────

CREATE POLICY "leagues_select" ON leagues
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "leagues_insert" ON leagues
  FOR INSERT TO authenticated
  WITH CHECK (creator_id = auth.uid());

CREATE POLICY "leagues_update" ON leagues
  FOR UPDATE TO authenticated
  USING (creator_id = auth.uid());

CREATE POLICY "leagues_delete" ON leagues
  FOR DELETE TO authenticated
  USING (creator_id = auth.uid());


-- ── 21. league_members ───────────────────────────────────────────────────────

CREATE POLICY "league_members_select" ON league_members
  FOR SELECT TO authenticated
  USING (TRUE);

CREATE POLICY "league_members_write" ON league_members
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 22. league_join_requests ─────────────────────────────────────────────────

CREATE POLICY "join_requests_select" ON league_join_requests
  FOR SELECT TO authenticated
  USING (
    user_id = auth.uid()
    OR league_id IN (SELECT id FROM leagues WHERE creator_id = auth.uid())
  );

CREATE POLICY "join_requests_insert" ON league_join_requests
  FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "join_requests_update" ON league_join_requests
  FOR UPDATE TO authenticated
  USING (
    league_id IN (SELECT id FROM leagues WHERE creator_id = auth.uid())
  );


-- ── 23. league_delete_votes ──────────────────────────────────────────────────

CREATE POLICY "delete_votes_select" ON league_delete_votes
  FOR SELECT TO authenticated
  USING (
    league_id IN (SELECT league_id FROM league_members WHERE user_id = auth.uid())
  );

CREATE POLICY "delete_votes_write" ON league_delete_votes
  FOR ALL TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());


-- ── 24. Fix get_distance_leaderboard function ─────────────────────────────────
-- Fix: set a fixed search_path to prevent search_path injection.
-- Revoke public/anon execute; keep authenticated only.

REVOKE EXECUTE ON FUNCTION get_distance_leaderboard(uuid[]) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION get_distance_leaderboard(uuid[]) FROM anon;

CREATE OR REPLACE FUNCTION get_distance_leaderboard(scope_ids uuid[] DEFAULT NULL)
RETURNS TABLE(user_id text, total_km double precision)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
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

GRANT EXECUTE ON FUNCTION get_distance_leaderboard(uuid[]) TO authenticated;


-- ── 25. Fix rls_auto_enable function if it exists ────────────────────────────
-- Revoke public access to this internal utility function.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname = 'rls_auto_enable'
  ) THEN
    REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC;
    REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM anon;
    REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM authenticated;
  END IF;
END;
$$;
