-- ============================================================
-- add_performance_indexes.sql
--
-- Run in: Supabase Dashboard → SQL Editor → New Query → Run
-- All statements use IF NOT EXISTS — safe to run multiple times.
-- ============================================================


-- ── run_sessions ─────────────────────────────────────────────
-- Covers: dashboard week query  (.eq user_id .eq status .gte start_time .order start_time DESC)
--         session list           (.eq user_id .order start_time DESC .range ...)
-- The existing UNIQUE(user_id, local_id) does NOT cover status + start_time filters.
CREATE INDEX IF NOT EXISTS idx_run_sessions_user_status_time
    ON run_sessions (user_id, status, start_time DESC);


-- ── habit_logs ───────────────────────────────────────────────
-- Covers: dashboard today-habits  (.eq user_id .eq log_date)
--         habit stats             (.eq user_id .gte log_date .eq is_completed)
-- The existing UNIQUE(habit_id, log_date) is keyed on habit_id, not user_id —
-- it does NOT accelerate these user-scoped range queries.
CREATE INDEX IF NOT EXISTS idx_habit_logs_user_date_completed
    ON habit_logs (user_id, log_date, is_completed);


-- ── habits ───────────────────────────────────────────────────
-- Covers: list_habits, habit_stats, dashboard (.eq user_id .eq is_active true)
-- No index exists on this table at all currently.
CREATE INDEX IF NOT EXISTS idx_habits_user_active
    ON habits (user_id, is_active);


-- ── user_friends ─────────────────────────────────────────────
-- Covers: pending_requests (.eq friend_id .eq status "PENDING")
--         list_friends OR query on friend_id side
-- The existing UNIQUE(user_id, friend_id) is ordered user_id-first,
-- so friend_id lookups require a full scan of that index.
CREATE INDEX IF NOT EXISTS idx_user_friends_friend_status
    ON user_friends (friend_id, status);


-- ── activity_feed ────────────────────────────────────────────
-- Covers: feed query (.in user_id [friend_ids] .eq is_public true .order created_at DESC)
-- No index on this table currently.
CREATE INDEX IF NOT EXISTS idx_activity_feed_user_public_time
    ON activity_feed (user_id, is_public, created_at DESC);


-- ── user_profiles — leaderboard columns ──────────────────────
-- Covers: XP leaderboard  (.order xp_points DESC .limit N)
-- Without this, Postgres does a full table sort on every leaderboard request.
CREATE INDEX IF NOT EXISTS idx_user_profiles_xp_desc
    ON user_profiles (xp_points DESC);

-- Covers: geographic leaderboard  (.eq city X .limit 500)
-- Partial index (WHERE city IS NOT NULL) skips rows with no city set.
CREATE INDEX IF NOT EXISTS idx_user_profiles_city
    ON user_profiles (city)
    WHERE city IS NOT NULL;


-- ── route_points ─────────────────────────────────────────────
-- Covers: territory claim point fetch (.eq session_id .order sequence_number)
-- The FK on session_id creates a single-column index; the compound index
-- avoids a sort on sequence_number after the session_id filter.
CREATE INDEX IF NOT EXISTS idx_route_points_session_seq
    ON route_points (session_id, sequence_number);


-- ── xp_transactions ──────────────────────────────────────────
-- Covers: any XP history lookup per user ordered by time.
CREATE INDEX IF NOT EXISTS idx_xp_transactions_user_time
    ON xp_transactions (user_id, created_at DESC);


-- ── notifications ────────────────────────────────────────────
-- Covers: per-user notification listing ordered by recency.
CREATE INDEX IF NOT EXISTS idx_notifications_user_time
    ON notifications (user_id, created_at DESC);
