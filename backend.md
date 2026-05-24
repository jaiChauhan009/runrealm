# RunRealm Backend Documentation

FastAPI + Supabase backend for RunRealm — a gamified fitness app where users run, capture territories, build habits, and compete on leaderboards.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Authentication](#authentication)
3. [Database Schema & Model Relationships](#database-schema--model-relationships)
4. [Features & API Reference](#features--api-reference)
   - [Auth](#auth)
   - [Sessions (Run Tracking)](#sessions-run-tracking)
   - [Habits](#habits)
   - [Todos](#todos)
   - [Territories](#territories)
   - [Social & Friends](#social--friends)
   - [Dashboard](#dashboard)
   - [Leaderboard](#leaderboard)
   - [Map & Location](#map--location)
   - [Notifications](#notifications)
   - [Profile](#profile)
   - [Content (Quotes & Tips)](#content-quotes--tips)
   - [Sync (Offline-first)](#sync-offline-first)
5. [XP & Leveling System](#xp--leveling-system)
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
  ├── Custom Exception Handlers (PostgREST, validation, HTTP)
  └── 13 Routers under /api/v1/
        ├── /auth
        ├── /dashboard
        ├── /sessions
        ├── /territories
        ├── /habits
        ├── /sync
        ├── /social
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
- Offline-first: every mutating action has a `localId` for idempotency; a dedicated `sync_queue` handles batched reconciliation
- XP is awarded inline (not async) and stored in `xp_transactions` for auditability
- No ORM — raw Supabase Python client queries throughout

---

## Authentication

**Module:** `auth.py`

Uses Supabase Auth (JWT-based). Every protected endpoint injects the current user via:

```python
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Client = Depends(get_db),
)
```

Flow:
1. Client sends `Authorization: Bearer <access_token>` header
2. `db.auth.get_user(token)` verifies the JWT with Supabase
3. Returns the `User` object (includes `user.id` as UUID)
4. Returns `401 Unauthorized` if token is missing, expired, or invalid

All database queries are then scoped to `user.id` — there is no server-side RLS enforced in development, so the app layer handles user isolation.

---

## Database Schema & Model Relationships

### Entity Relationship Overview

```
auth.users (Supabase managed)
    │
    ├── user_profiles (1:1)   ← profile, XP, level, location, social links
    ├── streaks (1:1)         ← current/best streak
    ├── run_sessions (1:N)    ← individual runs
    │     └── route_points (1:N)
    ├── habits (1:N)
    │     └── habit_logs (1:N)
    ├── territories (captured_by FK)
    │     └── territory_captures (N:M capture history)
    ├── xp_transactions (1:N) ← audit log for all XP
    ├── activity_feed (1:N)   ← public activity stream
    ├── user_friends (N:M)    ← bidirectional friend graph
    ├── notifications (1:N)
    ├── sync_queue (1:N)      ← offline sync state
    ├── team_members → teams
    └── user_achievements → achievements
```

### Table Definitions

#### `user_profiles`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| user_id | UUID FK → auth.users | unique |
| username | text | unique |
| display_name | text | |
| avatar_url | text | |
| bio | text | |
| city | text | |
| level | int | default 1, derived from xp |
| xp_points | int | cumulative total |
| total_runs | int | |
| total_calories | int | |
| current_streak | int | |
| best_streak | int | |
| territory_owned_sq_km | float | |
| territories_captured | int | |
| is_public | bool | default true |
| device_id | text | for push |
| fcm_token | text | for push |
| last_lat | float | real-time location |
| last_lon | float | real-time location |
| last_location_at | timestamptz | |
| instagram_handle | text | |
| twitter_handle | text | |
| strava_url | text | |
| linkedin_url | text | |

#### `streaks`
Separate table to allow streak logic to be queried independently without loading the full profile.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | unique |
| current_streak | int | |
| best_streak | int | |
| last_activity_date | date | used to detect broken streaks |

#### `run_sessions`
| Column | Type | Notes |
|--------|------|-------|
| local_id | text | client-generated, used for idempotency |
| user_id | UUID FK | |
| activity_type | text | e.g. RUN, WALK, CYCLE |
| start_time | timestamptz | |
| end_time | timestamptz | null until ended |
| duration_seconds | int | |
| distance_km | float | |
| avg_pace_min_per_km | float | |
| max_speed_kmh | float | |
| calories_burned | float | |
| elevation_gain_m | float | |
| route_geo_json | jsonb | full path stored inline |
| xp_earned | int | computed on end |
| status | text | ACTIVE → COMPLETED |
| synced | bool | |
Unique constraint: `(user_id, local_id)`

#### `route_points`
Individual GPS points streamed during a session. Stored separately for granularity; the session also stores `route_geo_json` as a summary.

| Column | Type |
|--------|------|
| local_id | text |
| session_id | UUID FK |
| user_id | UUID FK |
| latitude, longitude, altitude | float |
| speed_kmh, accuracy_m | float |
| sequence_number | int |
| recorded_at | timestamptz |

#### `territories`
Pre-seeded geographic zones. Currently seeded with 5 Delhi landmarks.

| Column | Type | Notes |
|--------|------|-------|
| name | text | |
| center_lat, center_lon | float | used for proximity checks |
| boundary_geo_json | jsonb | polygon |
| area_sq_km | float | |
| captured_by | UUID FK → auth.users | current owner |
| captured_at | timestamptz | |
| point_value | int | default 100 |
| capture_count | int | total captures ever |

#### `territory_captures` (capture history)
| Column | Type |
|--------|------|
| territory_id | UUID FK |
| user_id | UUID FK |
| session_id | UUID FK |
| previous_owner_id | UUID FK (nullable) |
| xp_earned | int |
| captured_at | timestamptz |

#### `habits`
| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| name | text | |
| habit_type | text | e.g. EXERCISE, SLEEP, WATER |
| target_value | float | e.g. 8 for 8 glasses of water |
| unit | text | e.g. "glasses", "hours", "minutes" |
| frequency | text | default DAILY |
| is_active | bool | soft delete |
| icon | text | |
| color_hex | text | |

#### `habit_logs`
One row per habit per day. Upserted on `(habit_id, log_date)`.

| Column | Type |
|--------|------|
| habit_id | UUID FK |
| user_id | UUID FK |
| log_date | date |
| completed_value | float |
| is_completed | bool |
| xp_earned | int |
| notes | text |
| synced | bool |

#### `xp_transactions`
Immutable audit log — never updated, only inserted.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| amount | int | XP amount |
| transaction_type | text | RUN_COMPLETE / HABIT / TERRITORY_CAPTURE / CHALLENGE_WIN |
| reference_id | UUID | points to the source record |
| description | text | human-readable |

#### `activity_feed`
| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| activity_type | text | RUN_COMPLETED / TERRITORY_CAPTURED / etc |
| reference_id | UUID | session or territory id |
| message | text | display string |
| metadata_json | text | extra context |
| is_public | bool | controls social feed visibility |

#### `user_friends`
Bidirectional friend graph. A friendship requires two checks: `user_id=A AND friend_id=B` OR `user_id=B AND friend_id=A`.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | requester |
| friend_id | UUID FK | recipient |
| status | text | PENDING → ACCEPTED |
| accepted_at | timestamptz | |
Unique: `(user_id, friend_id)`

#### `notifications`
| Column | Type |
|--------|------|
| user_id | UUID FK |
| title, body | text |
| notification_type | text |
| is_read | bool |
| reference_id | UUID |
| deep_link | text |

#### `sync_queue`
Tracks offline operations until they are reconciled server-side.

| Column | Type | Notes |
|--------|------|-------|
| user_id | UUID FK | |
| entity_type | text | RUN_SESSION / HABIT_LOG / ROUTE_POINT |
| operation | text | CREATE / UPDATE |
| local_id | text | client-generated |
| server_id | UUID | filled after sync |
| payload | text | JSON string |
| status | text | PENDING → SYNCING → SYNCED / FAILED |
| error_message | text | |
| occurred_at | timestamptz | client-side timestamp |
Unique: `(user_id, local_id)`

#### `teams` & `team_members`
Foundation for future team conquest mode. Not yet exposed via API.

#### `achievements` & `user_achievements`
Scaffolded for future use; no endpoints yet.

---

## Features & API Reference

All endpoints are prefixed with `/api/v1/`. All protected endpoints require `Authorization: Bearer <token>`.

---

### Auth

**Router:** `routers/auth.py` — prefix `/auth`

#### `POST /auth/register`
No auth required.

**Request:**
```json
{
  "email": "user@example.com",
  "username": "runner99",
  "password": "secret123",
  "displayName": "Alex",
  "deviceId": "device-uuid"
}
```

**Flow:**
1. Checks `user_profiles` for existing username — returns `400` if taken
2. Creates Supabase auth user (`db.auth.sign_up`)
3. Inserts row in `user_profiles` with username, display_name, device_id
4. Inserts row in `streaks` (zeroed out)
5. If email confirmation is enabled, returns `emailConfirmationRequired: true` with null tokens

**Response data:**
```json
{
  "accessToken": "...",
  "refreshToken": "...",
  "userId": "uuid",
  "username": "runner99",
  "level": 1,
  "xpPoints": 0,
  "emailConfirmationRequired": false
}
```

---

#### `POST /auth/login`
No auth required.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret123",
  "deviceId": "device-uuid",
  "fcmToken": "firebase-token"
}
```

**Flow:**
1. Signs in with Supabase (`db.auth.sign_in_with_password`)
2. Updates `device_id` and `fcm_token` in `user_profiles` if provided

**Response data:** same shape as register (accessToken, refreshToken, userId, username, level, xpPoints)

---

#### `POST /auth/refresh`
No auth required.

**Query param:** `?refresh_token=<token>`

Calls `db.auth.refresh_session(refresh_token)` and returns new token pair.

---

### Sessions (Run Tracking)

**Router:** `routers/sessions.py` — prefix `/sessions`

Handles the full lifecycle of a run: start → stream GPS points → end.

#### `POST /sessions/start`

**Request:**
```json
{
  "activityType": "RUN",
  "startTime": "2025-02-14T08:00:00Z",
  "localId": "client-generated-uuid"
}
```

**Flow:** Upserts on `(user_id, local_id)` — idempotent, safe to retry. Sets `status=ACTIVE`.

---

#### `POST /sessions/{session_id}/end`

**Request:**
```json
{
  "endTime": "2025-02-14T08:45:00Z",
  "distanceKm": 5.2,
  "avgPaceMinPerKm": 8.65,
  "maxSpeedKmh": 12.3,
  "caloriesBurned": 420,
  "elevationGainM": 45.0,
  "routeGeoJson": "{...}"
}
```

**Flow:**
1. Updates session: sets `end_time`, metrics, `status=COMPLETED`, calculates `duration_seconds`
2. Streak logic:
   - Loads `streaks` row for user
   - If `last_activity_date` is today → no change
   - If yesterday → increment `current_streak`
   - If gap > 1 day → reset `current_streak = 1`
   - Updates `best_streak` if current exceeds it
3. Calculates XP: `base = distance_km × 10`, then `xp = base × min(1 + streak × 0.1, 3.0)`
4. Inserts `xp_transactions` row (type=RUN_COMPLETE)
5. Updates `user_profiles`: increments `xp_points`, `total_runs`, `total_calories`, recalculates `level`
6. Inserts `activity_feed` entry (type=RUN_COMPLETED, is_public=true)

---

#### `POST /sessions/{session_id}/points`

**Request:** Array of route points
```json
[
  {
    "latitude": 28.6139,
    "longitude": 77.2090,
    "altitude": 220.5,
    "speedKmh": 10.2,
    "accuracyM": 5.0,
    "sequenceNumber": 1,
    "recordedAt": "2025-02-14T08:01:00Z",
    "localId": "point-uuid-1"
  }
]
```

Batch inserts into `route_points`. Used to stream GPS data during an active run.

---

#### `GET /sessions`

**Query params:** `?page=0&size=20`

Returns paginated `run_sessions` for the current user ordered by `start_time DESC`.

**Response data:**
```json
{
  "items": [...],
  "totalElements": 42,
  "totalPages": 3,
  "page": 0,
  "size": 20
}
```

---

#### `GET /sessions/{session_id}`

Returns a single session. Validates ownership — returns `404` if not found or not owned by user.

---

### Habits

**Router:** `routers/habits.py` — prefix `/habits`

#### `GET /habits`
Returns all active (`is_active=true`) habits for the current user.

---

#### `POST /habits`

**Request:**
```json
{
  "name": "Drink Water",
  "description": "8 glasses daily",
  "habitType": "HYDRATION",
  "targetValue": 8,
  "unit": "glasses",
  "frequency": "DAILY",
  "icon": "💧",
  "colorHex": "#2196F3"
}
```

Inserts into `habits`.

---

#### `POST /habits/log`

**Request:**
```json
{
  "habitId": "uuid",
  "logDate": "2025-02-14",
  "completedValue": 6.0,
  "notes": "Almost there",
  "localId": "log-uuid"
}
```

**Flow:**
1. Upserts on `(habit_id, log_date)` — safe to call multiple times per day
2. Sets `is_completed = completedValue >= targetValue`
3. If completed: awards XP via `xp_calculator.for_habit(streak)`, inserts `xp_transactions`, updates `user_profiles.xp_points`

---

#### `GET /habits/logs`

**Query param:** `?date_str=2025-02-14` (defaults to today)

Returns all habit logs for the given date for the current user.

---

#### `GET /habits/stats`

Returns completion statistics across three time windows:

| Period | Lookback | Weight |
|--------|----------|--------|
| Daily | Today only | 40% |
| Weekly | Last 7 days | 35% |
| Monthly | Last 30 days | 25% |

**Response data:**
```json
{
  "daily":   { "completedCount": 3, "totalCount": 5, "percentage": 60.0 },
  "weekly":  { "completedCount": 18, "totalCount": 35, "percentage": 51.4 },
  "monthly": { "completedCount": 72, "totalCount": 150, "percentage": 48.0 },
  "overallScore": 53.3
}
```

`overallScore = daily% × 0.4 + weekly% × 0.35 + monthly% × 0.25`

---

### Todos

**Router:** `routers/todos.py` — prefix `/todos`

Daily task management. Todos are date-scoped — you fetch/create them for a specific date.

#### `GET /todos`

**Query param:** `?todo_date=2025-02-14` (defaults to today)

Returns all todos for that date for the current user, ordered by `created_at ASC`.

---

#### `POST /todos`

**Request:**
```json
{
  "title": "Morning stretching",
  "description": "10 minutes",
  "todoDate": "2025-02-14",
  "category": "GENERAL"
}
```

---

#### Todo status field

Every todo now carries a `status` field in addition to `is_completed`:

| status | is_completed | UI indicator |
|--------|-------------|--------------|
| `PENDING` | false | 3 action buttons: Done / Cancel / Later |
| `DONE` | true | Green ✓ checkmark, title strikethrough |
| `CANCELLED` | false | Red ✗ cross, title strikethrough |
| `DEFERRED` | false | Amber clock icon, separate visual treatment |

Any status can be changed back to another by calling the status endpoint again.

---

#### `PATCH /todos/{todo_id}/status` ★ primary action endpoint

**Request:**
```json
{ "status": "DONE" }
```
Accepts: `PENDING` | `DONE` | `CANCELLED` | `DEFERRED`

Sets `status`, syncs `is_completed` (true only for DONE), and sets/clears `completed_at`.

---

#### `PATCH /todos/{todo_id}/complete`

Shorthand — sets `status=DONE`, `is_completed=true`, `completed_at=utcnow()`. No request body.

---

#### `PATCH /todos/{todo_id}/incomplete`

Shorthand — sets `status=PENDING`, `is_completed=false`, `completed_at=null`.

---

#### `PATCH /todos/{todo_id}/cancel`

Sets `status=CANCELLED`. No request body.

---

#### `PATCH /todos/{todo_id}/defer`

Sets `status=DEFERRED` ("Do Later"). No request body.

---

#### `PATCH /todos/{todo_id}`

**Request (all fields optional):**
```json
{
  "title": "Updated title",
  "description": "Updated desc",
  "category": "FITNESS"
}
```

Only non-null fields are applied.

---

#### `DELETE /todos/{todo_id}`

Hard delete. Validates ownership first.

---

#### `GET /todos/stats`

Same structure and weighting formula as `/habits/stats`.

---

### Territories

**Router:** `routers/territories.py` — prefix `/territories`

Gamified geographic conquest. Users capture pre-defined zones by running near them.

#### `GET /territories/nearby`

**Query params:** `?lat=28.6&lon=77.2&radiusKm=5`

**Flow:**
1. Rough bounding box filter: `±radiusKm/111` degrees from center (fast, avoids scanning all rows)
2. Precise haversine filter on the result set (`geo_utils.haversine_km`)
3. Returns territories within radius with owner profile data

---

#### `POST /territories/{territory_id}/capture`

**Query param:** `?sessionId=uuid`

**Flow:**
1. Loads territory and current `captured_by`
2. Records `previous_owner_id`
3. Updates `territories.captured_by = user.id`, increments `capture_count`
4. Inserts into `territory_captures`
5. Awards 50 XP: inserts `xp_transactions`, updates `user_profiles.xp_points` and `territories_captured`
6. Updates `user_profiles.territory_owned_sq_km`
7. Inserts `activity_feed` entry (type=TERRITORY_CAPTURED, message="Captured {name}!")

**Response data:**
```json
{
  "id": "territory-uuid",
  "territory": { ... },
  "user": { ... },
  "previousOwnerId": "uuid-or-null",
  "xpEarned": 50
}
```

---

#### `GET /territories/mine`

**Query params:** `?page=0&size=20`

Returns the current user's captured territories, paginated.

---

### Social & Friends

**Router:** `routers/social.py` — prefix `/social`

#### `GET /social/feed`

**Query params:** `?page=0&size=20`

**Flow:**
1. Finds all accepted friendships (bidirectional: `user_id=me OR friend_id=me`, status=ACCEPTED)
2. Collects friend IDs + own ID
3. Queries `activity_feed` where `user_id IN (...)` and `is_public=true`
4. Orders by `created_at DESC`, paginates
5. Joins profile data (username, display_name, avatar_url) for each feed entry

---

#### `POST /social/friends/{friend_id}/request`

**Flow:**
1. Checks for existing row in `user_friends` — returns `400` if already sent
2. Inserts with `status=PENDING`

---

#### `POST /social/friends/{friend_id}/accept`

**Flow:**
Updates the `user_friends` row (where `user_id=friend_id AND friend_id=me`) to `status=ACCEPTED` and sets `accepted_at`.

---

#### `GET /social/friends`

Returns all accepted friends. Bidirectional query — union of rows where user is either `user_id` or `friend_id`.

---

#### `GET /social/friends/pending`

Returns pending incoming requests (where `friend_id=me AND status=PENDING`).

---

### Dashboard

**Router:** `routers/dashboard.py` — prefix `/dashboard`

Single aggregation endpoint for the home screen. All data fetched in parallel-ish Supabase queries.

#### `GET /dashboard`

**Response data:**
```json
{
  "currentStreak": 5,
  "bestStreak": 12,
  "weeklyDistanceKm": 22.5,
  "weeklyCalories": 1850,
  "totalXp": 3400,
  "level": 6,
  "xpToNextLevel": 200,
  "territoryOwnedSqKm": 2.3,
  "territoriesCaptured": 4,
  "todayHabits": [
    {
      "habitId": "uuid",
      "name": "Drink Water",
      "habitType": "HYDRATION",
      "targetValue": 8,
      "completedValue": 5,
      "completed": false,
      "unit": "glasses",
      "colorHex": "#2196F3"
    }
  ],
  "recentActivities": [
    {
      "sessionId": "uuid",
      "activityType": "RUN",
      "distanceKm": 5.2,
      "durationSeconds": 2700,
      "caloriesBurned": 420,
      "startTime": "2025-02-14T08:00:00Z"
    }
  ]
}
```

- `weeklyDistanceKm` / `weeklyCalories`: summed from `run_sessions` where `status=COMPLETED` and `start_time >= 7 days ago`
- `todayHabits`: habits joined with today's log (left join — `completedValue=0` if not yet logged)
- `recentActivities`: last 5 completed sessions

---

### Leaderboard

**Router:** `routers/leaderboard.py` — prefix `/leaderboard`

#### `GET /leaderboard`

**Query params:** `?type=xp&top=50` (top max 100)

| `type` | Logic |
|--------|-------|
| `xp` | Ranks `user_profiles` by `xp_points DESC`, top N |
| `distance` | Sums `distance_km` from `run_sessions` (status=COMPLETED), groups by user, ranks by sum DESC |

**Response data:**
```json
[
  { "rank": 1, "userId": "uuid", "username": "fastrunner", "score": 8450 },
  { "rank": 2, ... }
]
```

---

### Map & Location

**Router:** `routers/map.py` — prefix `/map`

#### `POST /map/location`

**Request:**
```json
{ "latitude": 28.6139, "longitude": 77.2090 }
```

Updates `user_profiles.last_lat`, `last_lon`, `last_location_at` (UTC). Called periodically (~10s) during active runs.

---

#### `GET /map/route/{session_id}`

Returns the session's route as a **GeoJSON FeatureCollection** with:

| Feature | Geometry | Description |
|---------|----------|-------------|
| 1 | LineString | Full path: `[lon, lat, altitude]` coords. Color-coded by pace |
| 2 | Point | Start marker |
| 3 | Point | Finish marker |
| 4+ | Point | Territory centers within the session's bounding box |

**Pace color thresholds (LineString):**
- < 4.5 min/km → `#CCFF00` (neon green, fast)
- 4.5–6 min/km → `#00FFFF` (cyan)
- 6–8 min/km → `#8A2BE2` (purple)
- \> 8 min/km → `#FF4444` (red, slow)

Territory point colors:
- Owned by current user → `#CCFF00`
- Owned by others → `#8A2BE2`

---

#### `GET /map/territories/live`

**Query params:** `?lat=X&lon=Y&radiusKm=3`

Returns nearby territories as a GeoJSON FeatureCollection.

Each Feature's `properties`:
```json
{
  "territoryId": "uuid",
  "name": "Connaught Place",
  "areaSqKm": 1.2,
  "pointValue": 100,
  "captureCount": 7,
  "owner": { "id": "uuid", "username": "...", "level": 5 },
  "fillColor": "#CCFF00",
  "strokeColor": "#CCFF00"
}
```

Color logic:
- Owned by me → `#CCFF00` (neon green)
- Unclaimed → `#050505` (near-black)
- Owned by others → `#8A2BE2` (purple)

**Response metadata:**
```json
{
  "meta": { "total": 12, "mine": 3, "unclaimed": 5, "contested": 4 }
}
```

---

#### `GET /map/nearby-users`

**Query params:** `?lat=X&lon=Y&radiusKm=5`

**Flow:**
1. Bounding box filter on `user_profiles` by `last_lat/last_lon`
2. Haversine precision filter
3. Excludes: self, already-accepted friends, users with `is_public=false`
4. Sorts by distance ascending

**Response data:**
```json
{
  "users": [
    {
      "userId": "uuid",
      "username": "runner42",
      "displayName": "Sam",
      "avatarUrl": "...",
      "level": 4,
      "xpPoints": 2100,
      "distanceKm": 1.3,
      "lastSeenAt": "2025-02-14T08:12:00Z"
    }
  ],
  "totalNearby": 3,
  "radiusKm": 5,
  "centerLat": 28.6,
  "centerLon": 77.2
}
```

---

### Notifications

**Router:** `routers/notifications.py` — prefix `/notifications`

Notifications are created server-side (inside other routers) — no endpoint to create them from clients.

#### `GET /notifications`

**Query params:** `?page=0&size=30`

Returns paginated notifications for current user, ordered by `created_at DESC`.

---

#### `GET /notifications/unread-count`

Returns `{ "unreadCount": 4 }`.

---

#### `POST /notifications/read-all`

Sets `is_read=true` for all notifications of the current user.

---

#### `POST /notifications/{notification_id}/read`

Sets `is_read=true` for a single notification (validates ownership).

---

#### `POST /notifications/{notification_id}/todo-action`

Updates a todo's status directly from the notification bell — no need to navigate to the todo screen.

**Preconditions:**
- Notification must be owned by the current user
- `notification_type` must be `"TODO_REMINDER"`
- `reference_id` must point to a valid todo owned by the current user

**Request:**
```json
{ "status": "DONE" }
```
Accepts: `DONE` | `CANCELLED` | `DEFERRED`

**Flow:**
1. Validates notification ownership and type
2. Resolves `reference_id` → todo, validates ownership
3. Updates todo status (same logic as `PATCH /todos/{id}/status`)
4. Marks the notification as read

**Response data:**
```json
{ "todo": { ...updated todo row... } }
```

**Creating TODO_REMINDER notifications (server-side):** when creating a notification that should support in-notification todo actions, set:
- `notification_type = "TODO_REMINDER"`
- `reference_id = <todo_id>`

---

### Profile

**Router:** `routers/profile.py` — prefix `/profile`

#### `GET /profile`

Returns the current user's full profile.

**Response data:**
```json
{
  "id": "uuid",
  "userId": "uuid",
  "username": "runner99",
  "level": 6,
  "xpPoints": 3400,
  "displayName": "Alex",
  "avatarUrl": "https://...",
  "bio": "I run therefore I am",
  "city": "Delhi",
  "totalRuns": 42,
  "totalCalories": 18500,
  "currentStreak": 5,
  "bestStreak": 12,
  "territoryOwnedSqKm": 2.3,
  "territoriesCaptured": 4,
  "isPublic": true,
  "updatedAt": "2025-02-14T10:00:00Z",
  "socialLinks": {
    "instagram": "runner99",
    "twitter": null,
    "strava": "https://strava.com/athletes/...",
    "linkedin": null
  }
}
```

---

#### `GET /profile/{user_id}`

Returns any user's public profile. Returns `400` if profile not found or if `isPublic=false` and requester is not the owner.

---

#### `PATCH /profile`

Updates the current user's profile. All fields are optional — only provided (non-null) fields are applied.

**Request:**
```json
{
  "displayName": "Alex Runner",
  "bio": "Trail runner",
  "city": "Mumbai",
  "avatarUrl": "https://...",
  "isPublic": true,
  "instagramHandle": "alex_runs",
  "twitterHandle": "alexruns",
  "stravaUrl": "https://strava.com/athletes/123",
  "linkedinUrl": null
}
```

---

### Content (Quotes & Tips)

**Router:** `routers/content.py` — prefix `/content`

Static content served from in-memory lists (34 quotes, 25 tips). No database reads.

#### `GET /content/quote`
Returns today's quote — deterministic seed: `int(date.today().strftime("%Y%m%d")) % len(quotes)`. Same response all day, rotates daily.

#### `GET /content/quote/random`
Returns a randomly selected quote.

#### `GET /content/tip`
Returns today's health tip (same deterministic logic).

#### `GET /content/tip/random`
**Query param:** `?category=NUTRITION` (optional)

Categories: `NUTRITION`, `RECOVERY`, `TRAINING`, `MENTAL`, `WORKLIFE`

#### `GET /content/feed`
Combined response: `{ "quote": {...}, "tip": {...} }` using today's deterministic picks.

---

### Sync (Offline-first)

**Router:** `routers/sync.py` — prefix `/sync`

Allows clients to queue operations while offline and batch-reconcile when connectivity returns.

#### `POST /sync/batch`

**Request:**
```json
{
  "items": [
    {
      "entityType": "RUN_SESSION",
      "operation": "CREATE",
      "localId": "local-uuid",
      "payload": "{\"activityType\": \"RUN\", ...}",
      "occurredAt": "2025-02-14T08:00:00Z"
    }
  ]
}
```

**Flow per item:**
1. Checks `sync_queue` for existing row with `(user_id, local_id)`
2. If already `SYNCED` → returns existing `server_id` immediately
3. Otherwise: inserts/updates row as `SYNCING`, dispatches to handler
4. Handlers:
   - `RUN_SESSION` → upsert into `run_sessions` on `(user_id, local_id)`
   - `HABIT_LOG` → upsert into `habit_logs` on `(habit_id, log_date)`
   - `ROUTE_POINT` → upsert into `route_points` on `local_id`
5. On success: updates `sync_queue` → `SYNCED`, sets `server_id`
6. On error: updates `sync_queue` → `FAILED`, stores `error_message`

**Response data:**
```json
{
  "totalReceived": 10,
  "totalSynced": 9,
  "totalFailed": 1,
  "results": [
    { "localId": "...", "serverId": "uuid", "status": "SYNCED" },
    { "localId": "...", "error": "...", "status": "FAILED" }
  ]
}
```

---

#### `GET /sync/pending-count`

Returns `{ "pendingCount": 3 }` — count of `sync_queue` rows with `status IN (PENDING, SYNCING)` for the current user.

---

## XP & Leveling System

**Module:** `xp_calculator.py`

### XP Sources

| Action | Base XP | Modifier |
|--------|---------|----------|
| Run completed | `distance_km × 10` | × streak multiplier |
| Territory captured | 50 | none |
| Habit completed | 5 | × streak multiplier |
| Challenge won | 100 | none |

### Streak Multiplier

```
multiplier = min(1.0 + current_streak × 0.1, 3.0)
```

- Streak of 0–1: 1.0× (no bonus)
- Streak of 5: 1.5×
- Streak of 10: 2.0×
- Streak of 20+: capped at 3.0×

### Level Calculation

```
level = floor(sqrt(total_xp / 100)) + 1
xp_threshold(level) = level² × 100
xp_to_next_level = xp_threshold(current_level + 1) - total_xp
```

Examples:
- 0 XP → Level 1
- 400 XP → Level 3 (threshold = 9 × 100 = 900 for level 4)
- 10,000 XP → Level 11

---

## Response Format

All endpoints return the same envelope:

**Success:**
```json
{
  "success": true,
  "message": "Optional human-readable message",
  "data": { ... },
  "timestamp": "2025-02-14T10:30:00.000000Z"
}
```

**Error:**
```json
{
  "success": false,
  "message": "Description of what went wrong",
  "data": null,
  "timestamp": "2025-02-14T10:30:00.000000Z"
}
```

### Error Handlers (registered in `main.py`)

| Exception | HTTP Status | Behavior |
|-----------|-------------|----------|
| PostgRESTError | 400/500 | Extracts Supabase error message |
| RequestValidationError | 422 | Lists all validation failures |
| HTTPException | varies | Passes through |
| Generic Exception | 500 | "Internal server error" |

---

## Tech Stack & Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.0 | Web framework |
| uvicorn[standard] | 0.30.6 | ASGI server |
| supabase | 2.30.0 | Database + Auth client |
| pydantic[email] | >=2.10.0 | Request/response validation |
| python-dotenv | 1.0.1 | Environment variables |
| httpx | 0.27.2 | HTTP client (used by supabase) |
| requests | 2.32.3 | HTTP client |

**Environment variables required:**
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_KEY` — Supabase anon/service key

**Deployment:**
- `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- Python version: 3.11 (set via `PYTHON_VERSION` env var on Render)

