# RunRealm Backend Documentation

FastAPI + Supabase backend for RunRealm — a gamified fitness app where users run, capture territories, build habits, compete on leaderboards, and form leagues.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Authentication Flow](#authentication-flow)
3. [Database Schema & Relationships](#database-schema--relationships)
4. [XP & Leveling System](#xp--leveling-system)
5. [Features & API Reference](#features--api-reference)
   - [Auth](#auth)
   - [Sessions (Run Tracking)](#sessions-run-tracking)
   - [Habits](#habits)
   - [Todos](#todos)
   - [Territories](#territories)
   - [Leagues](#leagues)
   - [Social & Friends](#social--friends)
   - [Dashboard](#dashboard)
   - [Leaderboard](#leaderboard)
   - [Map & Location](#map--location)
   - [Notifications](#notifications)
   - [Profile](#profile)
   - [Content (Quotes & Tips)](#content-quotes--tips)
   - [Sync (Offline-first)](#sync-offline-first)
6. [Response Format](#response-format)
7. [Tech Stack & Dependencies](#tech-stack--dependencies)

---

## Architecture Overview

```
Client (iOS/Android)
        │
        ▼
FastAPI App (main.py)
  ├── CORS Middleware (all origins allowed)
  ├── Exception Handlers: PostgREST, RequestValidation, HTTP, Generic
  └── 14 Routers under /api/v1/
        ├── /auth
        ├── /dashboard
        ├── /sessions
        ├── /territories
        ├── /habits
        ├── /sync
        ├── /social
        ├── /leagues
        ├── /leaderboard
        ├── /notifications
        ├── /profile
        ├── /map
        ├── /todos
        └── /content
              │
              ▼
        Supabase (Postgres + Auth)
```

**Key design decisions:**
- All responses use a unified `ok()` wrapper — `{success, message, data, timestamp}`
- No ORM — raw Supabase Python client throughout (faster to iterate, fewer abstraction layers)
- Offline-first: every mutating action has a `localId` for idempotency; a dedicated `sync_queue` handles batched reconciliation when connectivity returns
- XP is awarded inline (not async) and logged in `xp_transactions` for full auditability
- No server-side RLS in development — the app layer handles user isolation by scoping every query to `user.id`

---

## Authentication Flow

**Module:** `auth.py`

Every protected endpoint uses:
```python
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Client = Depends(get_db),
)
```
1. Client sends `Authorization: Bearer <access_token>`
2. `db.auth.get_user(token)` verifies the JWT with Supabase
3. Returns the `User` object (`user.id` is the UUID used everywhere)
4. Returns `401` if missing, expired, or invalid

---

## Database Schema & Relationships

### Entity Relationship Map

```
auth.users (Supabase managed)
    │
    ├── user_profiles (1:1)         ← XP, level, location, stats, social links
    ├── streaks (1:1)               ← current/best streak, last activity date
    ├── run_sessions (1:N)          ← individual run/walk/cycle sessions
    │     └── route_points (1:N)   ← raw GPS points streamed during run
    ├── habits (1:N)
    │     └── habit_logs (1:N)     ← one log per habit per day
    ├── daily_todos (1:N)           ← date-scoped task items
    ├── territories (captured_by FK)
    │     └── territory_captures (history)
    ├── xp_transactions (1:N)       ← immutable XP audit log
    ├── activity_feed (1:N)         ← public timeline of runs, captures
    ├── user_friends (N:M)          ← bidirectional friend graph
    ├── notifications (1:N)
    ├── sync_queue (1:N)            ← offline operation queue
    ├── league_members → leagues
    └── league_join_requests → leagues
```

### Table: `user_profiles`
**Why it exists:** Centralises everything about a user that other screens need (XP, avatar, location, stats). Kept separate from `auth.users` so we can extend it freely.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| user_id | UUID FK → auth.users | unique |
| username | text | unique, min 3 chars |
| display_name | text | shown in UI |
| avatar_url | text | |
| bio | text | |
| city | text | used for LOCAL leaderboard scope |
| level | int | derived from xp_points, updated on every XP award |
| xp_points | int | running total |
| total_runs | int | incremented on session end |
| total_calories | int | incremented on session end |
| territory_owned_sq_km | float | updated on capture |
| territories_captured | int | updated on capture |
| is_public | bool | hides profile from non-friends if false |
| device_id | text | for push notifications |
| fcm_token | text | Firebase Cloud Messaging |
| last_lat / last_lon | float | real-time position, updated by POST /map/location |
| last_location_at | timestamptz | |
| instagram_handle | text | stored without URL prefix |
| twitter_handle | text | |
| strava_url | text | full URL |
| linkedin_url | text | full URL |

### Table: `streaks`
**Why it exists:** Separated from `user_profiles` so streak logic can be queried and updated without loading the entire profile row. Drives the XP streak multiplier.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | unique |
| current_streak | int | days in a row with at least one activity |
| best_streak | int | all-time peak |
| last_activity_date | date | compared against today to detect broken streaks |

**Streak update logic** (in `sessions.py → _update_streak`):
- Same day as last activity → no change
- Next consecutive day → `current_streak + 1`
- Gap > 1 day → reset to `1`
- `best_streak = max(best_streak, current_streak)` always

### Table: `run_sessions`
**Why it exists:** Core entity. Every run, walk, or cycle the user does is a session. Drives XP, streaks, leaderboard distance scores, and the map route view.

| Column | Type | Notes |
|--------|------|-------|
| local_id | text | client UUID, idempotency key |
| user_id | UUID FK | |
| activity_type | text | RUN / WALK / CYCLE |
| start_time | timestamptz | set on POST /sessions/start |
| end_time | timestamptz | set on POST /sessions/{id}/end |
| duration_seconds | int | computed as end - start |
| distance_km | float | sent by client on end |
| avg_pace_min_per_km | float | |
| max_speed_kmh | float | |
| calories_burned | float | |
| elevation_gain_m | float | |
| route_geo_json | jsonb | full GeoJSON LineString stored inline |
| xp_earned | int | computed on end: `distance_km × 10 × streak_multiplier` |
| status | text | ACTIVE → COMPLETED |
| synced | bool | always true for direct API calls |

Unique constraint: `(user_id, local_id)` — prevents duplicates from retried requests.

### Table: `route_points`
**Why it exists:** Raw GPS stream during an active run, stored per-point for granularity. The session also stores `route_geo_json` as a summary for fast map rendering.

| Column | Type |
|--------|------|
| local_id | text |
| session_id | UUID FK |
| user_id | UUID FK |
| latitude / longitude / altitude | float |
| speed_kmh / accuracy_m | float |
| sequence_number | int |
| recorded_at | timestamptz |

### Table: `habits`
**Why it exists:** Users define repeating personal goals (drink water, meditate, etc.) that show up on the dashboard and award XP on completion.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| name | text | |
| habit_type | text | EXERCISE / SLEEP / WATER / HYDRATION / etc |
| target_value | float | e.g. 8 for "8 glasses" |
| unit | text | "glasses", "hours", "minutes" |
| frequency | text | default DAILY |
| is_active | bool | soft delete — deactivate instead of delete |
| icon | text | emoji or icon key |
| color_hex | text | |

### Table: `habit_logs`
**Why it exists:** Tracks daily progress per habit. Upserted on `(habit_id, log_date)` — calling log twice on the same day updates, not duplicates.

| Column | Type | Notes |
|--------|------|-------|
| habit_id | UUID FK | |
| user_id | UUID FK | |
| log_date | date | |
| completed_value | float | actual progress |
| is_completed | bool | `completed_value >= target_value` |
| xp_earned | int | awarded when `is_completed` flips to true |
| notes | text | |
| local_id | text | offline idempotency |
| synced | bool | |

### Table: `daily_todos`
**Why it exists:** Date-scoped task management. Separate from habits — todos are one-off tasks for a specific date, habits are repeating goals.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| title | text | 1–200 chars |
| description | text | |
| todo_date | date | which day the todo belongs to |
| category | text | GENERAL / FITNESS / etc |
| status | text | PENDING / DONE / CANCELLED / DEFERRED |
| is_completed | bool | true only when status=DONE |
| completed_at | timestamptz | set when DONE, cleared otherwise |
| scheduled_at | timestamptz | optional reminder time |

### Table: `territories`
**Why it exists:** Pre-seeded geographic zones that users capture by running near them. Drives the conquest layer on the map and the territory leaderboard.

| Column | Type | Notes |
|--------|------|-------|
| name | text | landmark name |
| center_lat / center_lon | float | used for proximity checks |
| boundary_geo_json | jsonb | polygon for filled map layer |
| area_sq_km | float | |
| captured_by | UUID FK → auth.users | current owner (null = unclaimed) |
| captured_at | timestamptz | |
| point_value | int | default 100 |
| capture_count | int | total times captured across all users |

### Table: `territory_captures`
**Why it exists:** Immutable capture history — who captured what and when, including who they took it from.

| Column | Type |
|--------|------|
| territory_id | UUID FK |
| user_id | UUID FK |
| session_id | UUID FK |
| previous_owner_id | UUID FK (nullable) |
| xp_earned | int |
| captured_at | timestamptz |

### Table: `leagues`
**Why it exists:** User-created competitive groups. Members share a leaderboard, can invite others, and have a social/community identity.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| name | text | 1–100 chars |
| description | text | |
| scope | text | GLOBAL / COUNTRY / STATE / LOCAL |
| creator_id | UUID FK → auth.users | |
| social_links | jsonb | array of `{platform, url, label}` |
| created_at | timestamptz | |
| vote_deadline | timestamptz | set when first delete-vote cast; NULL otherwise |

### Table: `league_members`
**Why it exists:** Tracks who is in which league and their role. Primary key is `(league_id, user_id)` — a user can be in many leagues but only once per league.

| Column | Type | Notes |
|--------|------|-------|
| league_id | UUID FK | |
| user_id | UUID FK | |
| role | text | CREATOR / LEADER / MEMBER |
| joined_at | timestamptz | |

**Role hierarchy:**
- `CREATOR` — full control: delete, promote, remove, leave (transfers ownership)
- `LEADER` — admin: accept/reject join requests, remove members
- `MEMBER` — participate, vote to delete, leave

### Table: `league_join_requests`
**Why it exists:** Leagues are invite/request-only. Prevents spam membership.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| league_id | UUID FK | |
| user_id | UUID FK | |
| status | text | PENDING / ACCEPTED / REJECTED |
| requested_at | timestamptz | |

Unique: `(league_id, user_id)` — one active request per user per league.

### Table: `league_delete_votes`
**Why it exists:** Democratic deletion — any member can vote to disband. The league is deleted when `votes >= ceil(member_count / 2)` within a 30-minute window.

| Column | Type |
|--------|------|
| league_id | UUID FK |
| user_id | UUID FK |
| voted_at | timestamptz |

PK: `(league_id, user_id)` — one vote per user.

### Table: `xp_transactions`
**Why it exists:** Immutable audit log — never updated, only inserted. Lets you reconstruct a user's full XP history and debug discrepancies.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| amount | int | |
| transaction_type | text | RUN_COMPLETE / HABIT / TERRITORY_CAPTURE / CHALLENGE_WIN |
| reference_id | UUID | points to the source record |
| description | text | human-readable |

### Table: `activity_feed`
**Why it exists:** Public timeline of notable events (runs completed, territories captured) that friends can see in their social feed.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| activity_type | text | RUN_COMPLETED / TERRITORY_CAPTURED |
| reference_id | UUID | session or territory id |
| message | text | display string |
| metadata_json | text | extra context as JSON string |
| is_public | bool | controls social feed visibility |

### Table: `user_friends`
**Why it exists:** Bidirectional friend graph. A friendship requires a request from one user and acceptance by the other.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | requester |
| friend_id | UUID FK | recipient |
| status | text | PENDING → ACCEPTED |
| accepted_at | timestamptz | |

Unique: `(user_id, friend_id)`. To check if two users are friends, query both directions: `(A→B) OR (B→A)` with `status=ACCEPTED`.

### Table: `notifications`
**Why it exists:** In-app notification bell. Created server-side by other routers (league updates, todo reminders). Never created by clients directly.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| title | text | |
| body | text | |
| notification_type | text | TODO_REMINDER / LEAGUE_UPDATE / etc |
| is_read | bool | default false |
| reference_id | UUID | points to the related entity |
| deep_link | text | optional navigation target |

### Table: `sync_queue`
**Why it exists:** Tracks offline operations until they are reconciled. The client queues mutations while offline and calls POST /sync/batch when back online.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| entity_type | text | RUN_SESSION / HABIT_LOG / ROUTE_POINT |
| operation | text | CREATE / UPDATE |
| local_id | text | client UUID |
| server_id | UUID | filled after successful sync |
| payload | text | JSON blob |
| status | text | PENDING → SYNCING → SYNCED / FAILED |
| error_message | text | |
| occurred_at | timestamptz | client-side timestamp |

Unique: `(user_id, local_id)`

---

## XP & Leveling System

**Module:** `utils/xp_calculator.py`

### XP Sources

| Action | Formula | Notes |
|--------|---------|-------|
| Run completed | `distance_km × 10 × streak_multiplier` | main XP source |
| Territory captured | 50 flat | no multiplier |
| Habit completed | 5 flat × streak_multiplier | awarded once per completion per day |
| Challenge won | 100 flat | reserved for future |

### Streak Multiplier
```
multiplier = min(1.0 + current_streak × 0.1, 3.0)
```
- Streak 0–1: 1.0× (no bonus)
- Streak 5: 1.5×
- Streak 10: 2.0×
- Streak 20+: capped at 3.0×

### Level Calculation
```
level = floor(sqrt(total_xp / 100)) + 1
xp_threshold(level) = level² × 100
xp_to_next_level = xp_threshold(current_level + 1) - total_xp
```
Examples: 0 XP → L1 · 400 XP → L3 · 10 000 XP → L11

---

## Features & API Reference

All endpoints: prefix `/api/v1/`. Protected endpoints require `Authorization: Bearer <token>`.

---

### Auth
**Router:** `routers/auth.py` — prefix `/auth`
**Why it exists:** Wraps Supabase Auth so the client only needs to call our API and never touches Supabase directly. Also handles profile creation on first registration.

#### `POST /auth/register`
No auth required.

**Logic:**
1. Check `user_profiles` for username conflict → `400` if taken
2. `db.auth.sign_up(email, password)` → creates Supabase auth user
3. Inserts `user_profiles` row (username, display_name, device_id, level=1, xp=0)
4. Inserts zeroed `streaks` row
5. If email confirmation is enabled, session is null → returns `emailConfirmationRequired: true` with null tokens

**Request:**
```json
{ "email": "x@x.com", "username": "runner99", "password": "secret", "displayName": "Alex", "deviceId": "uuid" }
```
**Response data:**
```json
{ "accessToken": "...", "refreshToken": "...", "userId": "uuid", "username": "runner99", "level": 1, "xpPoints": 0, "emailConfirmationRequired": false }
```

---

#### `POST /auth/login`
No auth required.

**Logic:**
1. `db.auth.sign_in_with_password(email, password)` → `401` on failure
2. Updates `device_id` and `fcm_token` in profile if provided
3. Reads profile to return current level and XP

**Request:**
```json
{ "email": "x@x.com", "password": "secret", "deviceId": "uuid", "fcmToken": "firebase-token" }
```
**Response data:** same shape as register.

---

#### `POST /auth/refresh`
No auth required. Query param: `?refresh_token=<token>`

Calls `db.auth.refresh_session` and returns a new token pair.

---

### Sessions (Run Tracking)
**Router:** `routers/sessions.py` — prefix `/sessions`
**Why it exists:** The core gameplay loop. A session tracks a run from start to finish, computes XP, updates streaks and profile totals, and posts to the activity feed.

#### `POST /sessions/start`
**Logic:**
- Checks for existing row by `(user_id, local_id)` — idempotent, safe to retry
- Inserts session with `status=ACTIVE`, `distance_km=0`

**Request:** `{ "activityType": "RUN", "startTime": "...", "localId": "client-uuid" }`

---

#### `POST /sessions/{session_id}/end`
**Logic:**
1. Validates session ownership
2. Computes `duration_seconds = end_time - start_time`
3. Calls `_update_streak(db, uid, activity_date)` — updates `streaks` table and `user_profiles` streak fields
4. Computes `xp_earned = distance_km × 10 × streak_multiplier`
5. Updates session: sets all metrics, `status=COMPLETED`
6. Inserts `xp_transactions` row (type=RUN_COMPLETE)
7. Atomically increments `user_profiles.xp_points` and recomputes `level`
8. Increments `total_runs`, `total_calories` on profile
9. Inserts `activity_feed` entry (type=RUN_COMPLETED, is_public=true)

**Request:** `{ "endTime": "...", "distanceKm": 5.2, "avgPaceMinPerKm": 8.6, "maxSpeedKmh": 12.3, "caloriesBurned": 420, "elevationGainM": 45, "routeGeoJson": "{...}" }`

---

#### `POST /sessions/{session_id}/points`
Batch-inserts raw GPS points into `route_points`. Called every few seconds during a live run to build the polyline.

**Request:** array of `{ latitude, longitude, altitude, speedKmh, accuracyM, sequenceNumber, recordedAt, localId }`

---

#### `GET /sessions`
**Query params:** `?page=0&size=20`
Returns paginated sessions for current user, newest first.
**Response data:** `{ content, totalElements, totalPages, number, size }`

---

#### `GET /sessions/{session_id}`
Returns a single session — validates ownership, `400` if not found.

---

### Habits
**Router:** `routers/habits.py` — prefix `/habits`
**Why it exists:** Daily repeating goals. Separate from todos because they reset every day and are tracked over time for stats.

#### `GET /habits`
Returns all active (`is_active=true`) habits for the current user.

---

#### `POST /habits`
Creates a new habit. Sets `is_active=true` by default.

**Request:** `{ "name": "Drink Water", "habitType": "HYDRATION", "targetValue": 8, "unit": "glasses", "frequency": "DAILY", "icon": "💧", "colorHex": "#2196F3" }`

---

#### `POST /habits/log`
**Logic:**
1. Validates habit ownership
2. Checks if `completed_value >= target_value` → sets `is_completed`
3. Upserts on `(habit_id, log_date)` — calling twice on same day updates, not duplicates
4. If newly completed: awards XP (`5 × streak_multiplier`), updates `user_profiles.xp_points`

**Request:** `{ "habitId": "uuid", "logDate": "2026-05-25", "completedValue": 6.0, "notes": "...", "localId": "uuid" }`

---

#### `GET /habits/logs`
**Query param:** `?date_str=2026-05-25` (defaults to today)
Returns all logs for that date for the current user.

---

#### `GET /habits/stats`
Computes completion stats across three windows using the weighted formula:

| Period | Lookback | Weight |
|--------|----------|--------|
| Daily | Today | 40% |
| Weekly | Last 7 days | 35% |
| Monthly | Last 30 days | 25% |

`overallScore = daily% × 0.4 + weekly% × 0.35 + monthly% × 0.25`

**Response data:** `{ daily: {completedCount, totalCount, percentage}, weekly: {...}, monthly: {...}, overallScore }`

---

### Todos
**Router:** `routers/todos.py` — prefix `/todos`
**Why it exists:** Date-scoped one-off task management. Distinct from habits (habits repeat, todos don't). Supports 4 statuses and optional scheduling for reminder notifications.

#### `GET /todos`
**Query param:** `?todo_date=2026-05-25` (defaults to today)
Returns all todos for that date, ordered by `created_at ASC`.

---

#### `POST /todos`
**Request:** `{ "title": "Morning stretch", "description": "10 mins", "todoDate": "2026-05-25", "category": "FITNESS", "scheduledAt": "2026-05-25T07:00:00Z" }`

`scheduledAt` is optional — used to trigger a notification at a specific time.

---

#### `PATCH /todos/{todo_id}/status` ★ primary status endpoint
Accepts any status transition.

**Request:** `{ "status": "DONE" }` — values: `PENDING | DONE | CANCELLED | DEFERRED`

**Logic:** Sets `status`, syncs `is_completed` (true only for DONE), sets/clears `completed_at`.

| Status | is_completed | completed_at |
|--------|-------------|-------------|
| PENDING | false | null |
| DONE | true | NOW() |
| CANCELLED | false | null |
| DEFERRED | false | null |

---

#### `PATCH /todos/{todo_id}/complete`
Shorthand for `status=DONE`. No request body.

---

#### `PATCH /todos/{todo_id}/incomplete`
Shorthand for `status=PENDING`. No request body.

---

#### `PATCH /todos/{todo_id}/cancel`
Sets `status=CANCELLED`. No request body.

---

#### `PATCH /todos/{todo_id}/defer`
Sets `status=DEFERRED` ("Do Later"). No request body.

---

#### `PATCH /todos/{todo_id}`
Updates `title`, `description`, and/or `category`. Only provided (non-null) fields are applied.

---

#### `DELETE /todos/{todo_id}`
Hard delete. Validates ownership first.

---

#### `GET /todos/stats`
Same 3-window structure and formula as `/habits/stats`, but for todos.

---

### Territories
**Router:** `routers/territories.py` — prefix `/territories`
**Why it exists:** The conquest game mechanic. Pre-seeded geographic zones that users capture by running near them. Drives XP, social competition, and the map's conquest layer.

#### `GET /territories/nearby`
**Query params:** `?lat=28.6&lon=77.2&radiusKm=5`

**Logic:**
1. Bounding box filter: `±radiusKm/111` degrees (fast DB pre-filter)
2. Precise Haversine distance filter in Python (eliminates bounding box corners)

Returns raw territory rows with no owner enrichment (lightweight — used for proximity checks).

---

#### `POST /territories/{territory_id}/capture`
**Query param:** `?sessionId=uuid`

**Logic:**
1. Loads territory — `400` if not found
2. Records `previous_owner_id` before overwrite
3. Updates `territories.captured_by = uid`, increments `capture_count`
4. Inserts `territory_captures` history row
5. Increments `user_profiles.territories_captured` and `territory_owned_sq_km`
6. Awards 50 XP: inserts `xp_transactions`, updates `user_profiles.xp_points` + `level`
7. Inserts `activity_feed` entry (type=TERRITORY_CAPTURED, is_public=true)

**Response data:** `{ id, territory: {id, name}, user: {id}, previousOwnerId, xpEarned: 50 }`

---

#### `GET /territories/mine`
**Query params:** `?page=0&size=20`
Returns current user's captured territories, paginated.

---

### Leagues
**Router:** `routers/leagues.py` — prefix `/leagues`
**Why it exists:** Community-driven competitive groups. Users create leagues, invite others, and compete on a shared leaderboard. Supports democratic deletion via a vote system.

**Role hierarchy:** `CREATOR > LEADER > MEMBER`

#### `GET /leagues`
**Query param:** `?scope=GLOBAL|COUNTRY|STATE|LOCAL` (optional — omit for all)

Returns all leagues (or filtered by scope) with `memberCount` and `myRole` (null if not a member). Uses two batch queries to avoid N+1.

---

#### `POST /leagues`
**Logic:**
1. Inserts row into `leagues`
2. Auto-inserts the creator into `league_members` with `role=CREATOR`

**Request:** `{ "name": "Delhi Runners", "description": "...", "scope": "LOCAL", "socialLinks": [{"platform": "INSTAGRAM", "url": "...", "label": "Instagram"}] }`

**Response data:** League summary with `memberCount: 1, myRole: "CREATOR"`.

---

#### `GET /leagues/{id}`
**Logic:**
1. **Deadline check** — if `vote_deadline IS NOT NULL` and `NOW() > vote_deadline`:
   - Counts votes vs `ceil(member_count/2)` needed
   - If `votes >= needed` → deletes league, sends "League Dissolved" notification to all members → `404 League was dissolved by majority vote`
   - If `votes < needed` → resets `vote_deadline = NULL`, sends "Vote Failed" notification → continues with updated data
2. Loads members with enriched profile data
3. Loads pending join requests — **only visible to CREATOR and LEADER**
4. Loads delete votes count, computes `deleteVotesNeeded = ceil(member_count/2)`

**Response data:**
```json
{
  "league": { "id", "name", "scope", "memberCount", "myRole", "deleteVoteDeadline", ... },
  "members": [{ "userId", "role", "username", "level", "xpPoints", ... }],
  "pendingRequests": [...],   // empty for MEMBER callers
  "deleteVotes": 1,
  "deleteVotesNeeded": 3,
  "myVoteForDelete": false
}
```

---

#### `POST /leagues/{id}/join-request`
Submits a join request. Idempotent — upserts on `(league_id, user_id)` so retrying is safe.
Errors: `400 Already a member` | `400 Join request already pending`

---

#### `POST /leagues/{id}/join-requests/{user_id}/accept` *(CREATOR or LEADER only)*
**Logic:** Validates pending request exists → updates status to ACCEPTED → inserts member row with `role=MEMBER`.

---

#### `DELETE /leagues/{id}/join-requests/{user_id}` *(CREATOR or LEADER only)*
Updates request status to REJECTED.

---

#### `DELETE /leagues/{id}/members/{user_id}` *(CREATOR or LEADER only)*
Removes a member. Cannot remove the CREATOR.

---

#### `POST /leagues/{id}/members/{user_id}/promote` *(CREATOR only)*
Promotes a MEMBER or LEADER to LEADER. Only the CREATOR can do this.

---

#### `POST /leagues/{id}/leave`
**Logic:**
- Non-CREATOR: simply removes from `league_members`
- CREATOR leaving:
  - Finds oldest LEADER as successor; if none, finds oldest MEMBER
  - Promotes successor to CREATOR, updates `leagues.creator_id`
  - Removes departing creator from members
  - If no other members exist → deletes the league

**Response data:** `{ "newCreatorId": "uuid" }` when ownership transfers.

---

#### `DELETE /leagues/{id}` *(CREATOR only)*
Hard delete. Cascades to `league_members`, `league_join_requests`, `league_delete_votes`.

---

#### `POST /leagues/{id}/vote-delete` *(any member)*
**Logic:**
1. If `vote_deadline IS NULL` (first vote) → sets `vote_deadline = NOW() + 30 minutes`
2. If `vote_deadline` exists and window has closed → `400 Vote window has closed`
3. Records vote (upsert — idempotent)
4. Counts votes vs `ceil(member_count/2)` needed
5. If threshold met → deletes league, notifies all members ("League Dissolved")
6. Otherwise → returns current tally with deadline

**Response data:** `{ "deleted": bool, "votes": 2, "needed": 3, "deadline": "ISO timestamp" }`

---

### Social & Friends
**Router:** `routers/social.py` — prefix `/social`
**Why it exists:** Friend graph and activity feed. Friends see each other's runs and territory captures in a shared timeline.

#### `GET /social/feed`
**Query params:** `?page=0&size=20`

**Logic:**
1. Queries `user_friends` bidirectionally to get all accepted friend IDs (`user_id=me OR friend_id=me`)
2. Adds own UID to the set
3. Fetches `activity_feed` where `user_id IN (friend_ids)` and `is_public=true`, newest first

---

#### `POST /social/friends/{friend_id}/request`
Checks for existing row first (idempotent — returns `200` with "already sent" if duplicate). Inserts with `status=PENDING`.

---

#### `POST /social/friends/{friend_id}/accept`
Finds the PENDING row where `user_id=friend_id AND friend_id=me` (the recipient accepts). Updates to `status=ACCEPTED`.

---

#### `GET /social/friends`
Bidirectional query: `(user_id=me OR friend_id=me) AND status=ACCEPTED`.

---

#### `GET /social/friends/pending`
Incoming requests only: `friend_id=me AND status=PENDING`.

---

### Dashboard
**Router:** `routers/dashboard.py` — prefix `/dashboard`
**Why it exists:** Single aggregation endpoint for the home screen. One call instead of 6 — reduces round trips on app launch.

#### `GET /dashboard`
**Logic (all in one request):**
1. Reads `user_profiles` + `streaks`
2. Queries `run_sessions` (COMPLETED, last 7 days) → sums `weekly_distance` and `weekly_calories`; also grabs last 5 for `recentActivities`
3. Counts `territories` captured by user
4. Reads all active habits → left-joins today's logs (habits with no log get `completedValue=0, completed=false`)

**Response data:**
```json
{
  "currentStreak": 5, "bestStreak": 12,
  "weeklyDistanceKm": 22.5, "weeklyCalories": 1850,
  "totalXp": 3400, "level": 6, "xpToNextLevel": 200,
  "territoryOwnedSqKm": 2.3, "territoriesCaptured": 4,
  "todayHabits": [{ "habitId", "name", "targetValue", "completedValue", "completed", "unit", "colorHex" }],
  "recentActivities": [{ "sessionId", "activityType", "distanceKm", "durationSeconds", "caloriesBurned", "startTime" }]
}
```

---

### Leaderboard
**Router:** `routers/leaderboard.py` — prefix `/leaderboard`
**Why it exists:** Competition drives engagement. Two types (XP and distance) across multiple scopes (global, local, league) let users compete in the context that's meaningful to them.

#### `GET /leaderboard`
**Query params:** `?type=xp&top=50&scope=GLOBAL&league_id=uuid`

**Scope resolution (`_resolve_scope`):**
| scope | Filter applied |
|-------|---------------|
| `GLOBAL` | No filter — all users |
| `LOCAL` / `STATE` / `COUNTRY` | Fetches calling user's `city`; filters `user_profiles` by matching `city` |
| `LEAGUE` | Fetches `league_members` for `league_id`; filters to those user IDs |

**Type logic:**
- `xp`: queries `user_profiles` ordered by `xp_points DESC`, limited to `top`
- `distance`: fetches all COMPLETED `run_sessions` for in-scope users, sums `distance_km` per user in Python, sorts, takes top N

Response now includes `displayName`, `avatarUrl`, `level` in addition to `rank`, `userId`, `username`, `score`.

---

### Map & Location
**Router:** `routers/map.py` — prefix `/map`
**Why it exists:** Powers three distinct map views — run route replay, real-time territory conquest layer, and nearby user discovery.

#### `POST /map/location`
Updates `user_profiles.last_lat`, `last_lon`, `last_location_at`. Called every ~10 seconds during a live run to power the nearby-users feed.

**Request:** `{ "latitude": 28.6139, "longitude": 77.2090 }`

---

#### `GET /map/route/{session_id}`
Returns a GeoJSON FeatureCollection for run replay.

**Logic:**
1. Validates session ownership
2. Fetches `route_points` ordered by `sequence_number`
3. Builds 3 features: LineString path (styled by pace), start Point, finish Point
4. Fetches all territories within the route's bounding box (±0.5 km padding) and adds them as Point features

**Pace → color mapping:**
| Pace | Color | Label |
|------|-------|-------|
| < 4.5 min/km | `#CCFF00` | Elite — neon green |
| 4.5–6 min/km | `#00E5FF` | Good — cyan |
| 6–8 min/km | `#8A2BE2` | Steady — purple |
| > 8 min/km | `#FF6B6B` | Slow — red |

Territory colors: owned by me → `#CCFF00`, others → `#8A2BE2`.

---

#### `GET /map/territories/live`
**Query params:** `?lat=X&lon=Y&radiusKm=3`

Lightweight territory overlay — returns **Point features** (centers only) enriched with owner profile data. Used during an active run where polygon rendering is expensive.

**Feature properties:** `territoryId, name, areaSqKm, pointValue, captureCount, ownedByMe, unclaimed, owner: {userId, username, level}, fillColor, strokeColor, fillOpacity`

**Color logic:** mine=`#CCFF00` · unclaimed=`#050505`/stroke`#00E5FF` · others=`#8A2BE2`

**Response metadata:** `{ total, mine, unclaimed, contested }`

---

#### `GET /map/territories/polygons`
**Query params:** `?lat=X&lon=Y&radiusKm=5`

Full polygon layer — returns **two features per territory** for richer map rendering:

| Feature type | Geometry | Used for |
|---|---|---|
| `territory_polygon` | Polygon (from `boundary_geo_json`) | FillLayer — filled captured area |
| `territory_label` | Point (center) | CircleLayer — owner-initial badge |

Both features share `territoryId` so the client can pair them. Territories without `boundary_geo_json` return only the Point feature. Label features include `ownerInitial` (first character of owner's username, uppercased).

---

#### `GET /map/nearby-users`
**Query params:** `?lat=X&lon=Y&radiusKm=5` (max 50 km)

**Logic:**
1. Bounding box pre-filter on `user_profiles` by `last_lat/last_lon`
2. Haversine precision filter
3. Excludes: self, already-accepted friends, `is_public=false` users
4. Sorted by distance ascending

**Response data:** `{ users: [{userId, username, displayName, avatarUrl, level, xpPoints, distanceKm, lastSeenAt}], totalNearby, radiusKm, centerLat, centerLon }`

---

### Notifications
**Router:** `routers/notifications.py` — prefix `/notifications`
**Why it exists:** In-app notification bell. Notifications are created by other routers (league events, todo reminders) — the client never creates them directly, only reads and marks them.

#### `GET /notifications`
**Query params:** `?page=0&size=30`
Returns paginated notifications newest-first. **Response data:** `{ content, totalElements }`

---

#### `GET /notifications/unread-count`
Returns `{ "unreadCount": 4 }`. Called on app launch and after foreground push receipt to update the bell badge.

---

#### `POST /notifications/read-all`
Bulk-marks all unread notifications as read for the current user.

---

#### `POST /notifications/{notification_id}/read`
Marks a single notification read — validates ownership first.

---

#### `POST /notifications/{notification_id}/todo-action`
**Why it exists:** Lets users act on a todo directly from the notification bell without navigating to the todo screen.

**Preconditions:**
- Notification must be owned by caller
- `notification_type` must be `"TODO_REMINDER"`
- `reference_id` must point to a valid todo owned by caller

**Request:** `{ "status": "DONE" }` — accepts `DONE | CANCELLED | DEFERRED`

**Logic:** Validates notification → resolves todo → updates todo status → marks notification read.

**Response data:** `{ "todo": { ...updated todo row... } }`

---

### Profile
**Router:** `routers/profile.py` — prefix `/profile`
**Why it exists:** User identity and stats. Used by both the owner (my profile) and visitors (public profile sheet).

#### `GET /profile`
Returns the current user's full profile.

**Logic:** Fetches `user_profiles` row + calls `_total_distance(uid, db)` which sums `distance_km` from all COMPLETED `run_sessions`.

**Response data:**
```json
{
  "id", "userId", "username", "level", "xpPoints", "displayName", "avatarUrl",
  "bio", "city", "totalRuns", "totalCalories", "totalDistanceKm",
  "currentStreak", "bestStreak", "territoryOwnedSqKm", "territoriesCaptured",
  "isPublic", "updatedAt",
  "socialLinks": { "instagram", "twitter", "strava", "linkedin" }
}
```

---

#### `GET /profile/{user_id}`
Same shape as `GET /profile`. Returns `400` if profile is private (`is_public=false`) and caller is not the owner.

---

#### `PATCH /profile`
Partial update — only non-null fields are applied. Handles both profile fields and social links.

**Request (all optional):** `{ "displayName", "bio", "city", "avatarUrl", "isPublic", "instagramHandle", "twitterHandle", "stravaUrl", "linkedinUrl" }`

---

### Content (Quotes & Tips)
**Router:** `routers/content.py` — prefix `/content`
**Why it exists:** Daily motivation. No DB reads — served from in-memory lists with deterministic daily seeding so every user sees the same quote on the same day.

#### `GET /content/quote`
Returns today's quote. Seed: `int(date.today().strftime("%Y%m%d"))` — same response all day, rotates daily.

#### `GET /content/quote/random`
Returns a randomly selected quote (not seeded).

#### `GET /content/tip`
Today's health tip (same deterministic logic, seed +1 to differ from quote).

#### `GET /content/tip/random`
**Query param:** `?category=NUTRITION|RECOVERY|TRAINING|MENTAL|WORKLIFE`
Returns a random tip, optionally filtered by category.

#### `GET /content/feed`
Combined: `{ "quote": {...}, "tip": {...} }` — single call for daily content. Used on the home screen.

---

### Sync (Offline-first)
**Router:** `routers/sync.py` — prefix `/sync`
**Why it exists:** The app must work without internet. When connectivity returns, the client batches all queued mutations and calls this endpoint once.

#### `POST /sync/batch`
**Logic per item:**
1. Check `sync_queue` for `(user_id, local_id)` — if already `SYNCED`, return cached `server_id` immediately (idempotent)
2. Upsert queue row as `SYNCING`
3. Dispatch to handler:
   - `RUN_SESSION` → upsert into `run_sessions` on `(user_id, local_id)`
   - `HABIT_LOG` → upsert into `habit_logs` on `(habit_id, log_date)`
   - `ROUTE_POINT` → upsert into `route_points` on `local_id`
4. Success → update queue to `SYNCED`, store `server_id`
5. Failure → update queue to `FAILED`, store `error_message`

**Response data:** `{ totalReceived, totalSynced, totalFailed, results: [{localId, serverId, success, error}] }`

---

#### `GET /sync/pending-count`
Returns `{ "pendingCount": 3 }` — count of `sync_queue` rows with `status=PENDING`. Client uses this to show a sync indicator.

---

## Response Format

Every endpoint returns the same envelope:

**Success:**
```json
{
  "success": true,
  "message": "Optional human-readable string",
  "data": { ... },
  "timestamp": "2026-05-25T10:30:00.000000Z"
}
```

**Error:**
```json
{
  "success": false,
  "message": "Description of what went wrong",
  "data": null,
  "timestamp": "2026-05-25T10:30:00.000000Z"
}
```

### Registered Exception Handlers (`main.py`)

| Exception | HTTP Status | Behaviour |
|-----------|-------------|-----------|
| `PostgRESTError` | 400 or 503 | Extracts Supabase message; 503 if "schema cache" error (run migrations) |
| `RequestValidationError` | 422 | Returns first validation failure message |
| `HTTPException` | varies | Passes status + detail through |
| Any other `Exception` | 500 | "Internal server error" |

---

## Tech Stack & Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.0 | Web framework |
| uvicorn[standard] | 0.30.6 | ASGI server |
| supabase | 2.30.0 | Database + Auth client |
| pydantic[email] | >=2.10.0 | Request validation |
| python-dotenv | 1.0.1 | Environment variables |
| httpx | 0.27.2 | HTTP client (used by supabase) |
| requests | 2.32.3 | HTTP client |

**Environment variables required:**
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_KEY` — Supabase anon/service key

**Deployment:**
- `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- Python 3.11 (set via `PYTHON_VERSION` env var on Render)

**Migrations** (run in Supabase Dashboard → SQL Editor, in order):
1. `migrations/schema.sql` — full initial schema
2. `migrations/add_location.sql` — `last_lat/last_lon/last_location_at` on user_profiles
3. `migrations/add_social_todos.sql` — social links + daily_todos table
4. `migrations/add_todo_status.sql` — `status` column on daily_todos
5. `migrations/add_todo_scheduled_at.sql` — `scheduled_at` column on daily_todos
6. `migrations/add_leagues.sql` — leagues, league_members, league_join_requests, league_delete_votes tables + `vote_deadline` column





  Now addressing your message about multiple workers. You can run uvicorn with multiple worker processes to handle more concurrent requests. Here's what to know:
 
  For development (current --reload mode): multiple workers + reload don't work together. You're already on 1 worker, which is correct for development.

  For production deployment, replace the current start command with:

  uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

  Or better, use Gunicorn as the process manager with uvicorn workers:

  gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

  Worker count rule of thumb: (2 × CPU cores) + 1. On a 2-core server → 5 workers. On a 4-core → 9 workers.

  Important: the in-memory cache (cache.py) is per-process. With 4 workers, each has its own cache — a request hitting worker 1 won't see worker 2's cached data. This means slightly
  more DB hits on a freshly started multi-worker setup, but correctness is not affected. If you want a shared cache across workers, that requires Redis — but for current scale,
  per-process cache is fine.

  If you're on Render/Railway/Fly.io, set the start command to the gunicorn line above in your deployment config instead of the uvicorn single-worker command.

