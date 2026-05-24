# RunRealm — Frontend API Guide

> **Base URL:** `http://localhost:8000/api/v1` (dev) · swap to your deployed URL in prod  
> **Auth:** Every endpoint except `/auth/*` requires `Authorization: Bearer <accessToken>`  
> **Content-Type:** `application/json`  
> **Interactive docs:** `http://localhost:8000/docs` (Swagger UI — try every endpoint live)

---

## Response envelope

Every response is wrapped in the same shape:

```json
{
  "success": true,
  "message": "optional string",
  "data": { ... },
  "timestamp": "2026-05-19T08:00:00+00:00"
}
```

On error:
```json
{
  "success": false,
  "message": "Descriptive error",
  "data": null,
  "timestamp": "..."
}
```

---

## Design system colours (use in map/UI)

| Token | Hex | Use |
|---|---|---|
| Primary | `#CCFF00` | My territory, fast pace, CTA buttons |
| Secondary | `#00E5FF` | Unclaimed territory, good pace, links |
| Tertiary | `#8A2BE2` | Enemy territory, steady pace |
| Neutral | `#050505` | Backgrounds |
| Danger | `#FF6B6B` | Slow pace, errors |

---

## 1. Auth

### POST `/auth/register`
Create a new account.

**Request**
```json
{
  "email": "jai@example.com",
  "password": "Min6Chars!",
  "username": "CommanderJax",
  "displayName": "Jai (optional)",
  "deviceId": "android-uuid (optional)"
}
```

**Response**
```json
{
  "data": {
    "accessToken": "eyJ...",
    "refreshToken": "eyJ...",
    "tokenType": "Bearer",
    "expiresIn": 3600,
    "userId": "uuid",
    "username": "CommanderJax",
    "email": "jai@example.com",
    "level": 1,
    "xpPoints": 0,
    "emailConfirmationRequired": false
  }
}
```

> ⚠️ If `emailConfirmationRequired: true` — show "Check your email" screen.  
> The user must verify before they can log in.

---

### POST `/auth/login`

**Request**
```json
{
  "email": "jai@example.com",
  "password": "Min6Chars!",
  "deviceId": "optional",
  "fcmToken": "firebase-push-token (optional)"
}
```

**Response** — same shape as register

---

### POST `/auth/refresh`

**Query param:** `?refresh_token=eyJ...`

**Response**
```json
{
  "data": {
    "accessToken": "eyJ...",
    "refreshToken": "eyJ...",
    "tokenType": "Bearer",
    "expiresIn": 3600
  }
}
```

---

## 2. Dashboard

### GET `/dashboard`
Full home-screen data in one call.

```json
{
  "data": {
    "currentStreak": 12,
    "bestStreak": 31,
    "weeklyDistanceKm": 42.5,
    "weeklyCalories": 1800,
    "totalXp": 2450,
    "level": 14,
    "xpToNextLevel": 350,
    "territoryOwnedSqKm": 52.0,
    "territoriesCaptured": 8,
    "todayHabits": [
      {
        "habitId": "uuid",
        "name": "Hydration",
        "habitType": "HYDRATION",
        "targetValue": 8.0,
        "completedValue": 5.0,
        "completed": false,
        "unit": "glasses",
        "colorHex": "#00E5FF"
      }
    ],
    "recentActivities": [
      {
        "sessionId": "uuid",
        "activityType": "RUN",
        "distanceKm": 5.2,
        "durationSeconds": 1680,
        "caloriesBurned": 420,
        "startTime": "2026-05-19T07:00:00Z"
      }
    ]
  }
}
```

---

## 3. Run Sessions

### POST `/sessions/start`
Start a run. **Idempotent via `localId`** — safe to retry if network drops.

```json
{
  "activityType": "RUN",
  "startTime": "2026-05-19T07:00:00Z",
  "localId": "client-generated-uuid"
}
```

**Activity types:** `RUN` · `WALK` · `CYCLE` · `HIKE`

**Response**
```json
{
  "data": {
    "id": "server-uuid",
    "local_id": "client-uuid",
    "activity_type": "RUN",
    "start_time": "2026-05-19T07:00:00Z",
    "status": "ACTIVE",
    "distance_km": 0.0,
    "synced": true
  }
}
```

---

### POST `/sessions/{sessionId}/end`

```json
{
  "endTime": "2026-05-19T07:28:00Z",
  "distanceKm": 5.2,
  "avgPaceMinPerKm": 5.38,
  "maxSpeedKmh": 14.2,
  "caloriesBurned": 420,
  "elevationGainM": 35.0,
  "routeGeoJson": "optional GeoJSON string"
}
```

**Response adds:** `xp_earned`, `duration_seconds`, `status: COMPLETED`

---

### POST `/sessions/{sessionId}/points`
Batch-upload GPS points. Send arrays of up to 100 points.

```json
[
  {
    "latitude": 28.6139,
    "longitude": 77.2090,
    "altitude": 220.5,
    "speedKmh": 10.4,
    "accuracyM": 5.0,
    "sequenceNumber": 1,
    "recordedAt": "2026-05-19T07:00:05Z",
    "localId": "optional-point-uuid"
  }
]
```

---

### GET `/sessions?page=0&size=20`
Paginated run history.

```json
{
  "data": {
    "content": [ /* RunSession objects */ ],
    "totalElements": 42,
    "totalPages": 3,
    "number": 0,
    "size": 20
  }
}
```

### GET `/sessions/{sessionId}`
Single session by ID.

---

## 4. Map — Running Path & Territory

### GET `/map/route/{sessionId}`
Returns a **GeoJSON FeatureCollection** ready to hand directly to Google Maps or Mapbox.

```json
{
  "data": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [[77.209, 28.613], [77.210, 28.615], ...]
        },
        "properties": {
          "type": "run_path",
          "distanceKm": 5.2,
          "durationSeconds": 1680,
          "avgPaceMinPerKm": 5.38,
          "strokeColor": "#00E5FF",
          "strokeWidth": 4
        }
      },
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [77.209, 28.613] },
        "properties": { "type": "run_start", "label": "Start", "iconColor": "#00E5FF" }
      },
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [77.216, 28.621] },
        "properties": { "type": "run_finish", "label": "Finish", "iconColor": "#CCFF00" }
      },
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [77.2167, 28.6315] },
        "properties": {
          "type": "territory",
          "territoryId": "uuid",
          "name": "Connaught Place",
          "ownedByMe": false,
          "captureCount": 3,
          "fillColor": "#8A2BE2",
          "strokeColor": "#8A2BE2",
          "fillOpacity": 0.4
        }
      }
    ],
    "meta": {
      "sessionId": "uuid",
      "totalPoints": 120,
      "distanceKm": 5.2,
      "durationSeconds": 1680,
      "xpEarned": 57
    }
  }
}
```

**Pace → stroke colour mapping:**

| Pace (min/km) | Colour | Token |
|---|---|---|
| < 4.5 | `#CCFF00` | Elite — Primary |
| 4.5 – 6.0 | `#00E5FF` | Good — Secondary |
| 6.0 – 8.0 | `#8A2BE2` | Steady — Tertiary |
| > 8.0 | `#FF6B6B` | Easy |

**Android (Google Maps GeoJSON layer):**
```kotlin
val layer = GeoJsonLayer(googleMap, JSONObject(response.data.toString()))
layer.addLayerToMap()
// Path styling
layer.features.forEach { feature ->
    if (feature.getProperty("type") == "run_path") {
        layer.defaultLineStringStyle.color = Color.parseColor(feature.getProperty("strokeColor"))
        layer.defaultLineStringStyle.width = 8f
    }
}
```

---

### POST `/map/location`
Update the user's live position (call every 10 s during a run).

```json
{ "latitude": 28.6139, "longitude": 77.2090 }
```

---

### GET `/map/nearby-users?lat=28.6139&lon=77.2090&radiusKm=5`
Find other runners near the current position — for "People Near You" friend suggestions.

```json
{
  "data": {
    "users": [
      {
        "userId": "uuid",
        "username": "NeonRunner42",
        "displayName": "Arjun S.",
        "avatarUrl": "https://...",
        "level": 8,
        "xpPoints": 1240,
        "distanceKm": 1.3,
        "lastSeenAt": "2026-05-19T08:05:00Z",
        "alreadyFriend": false
      }
    ],
    "totalNearby": 3,
    "radiusKm": 5.0,
    "centerLat": 28.6139,
    "centerLon": 77.2090
  }
}
```

> After getting nearby users, call `POST /social/friends/{userId}/request` to add them.

---

### GET `/map/territories/live?lat=28.6139&lon=77.2090&radiusKm=3`
Live territory layer — call every 30 s during a run to update the conquest map.

```json
{
  "data": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [77.2167, 28.6315] },
        "properties": {
          "type": "territory",
          "territoryId": "uuid",
          "name": "Connaught Place",
          "areaSqKm": 1.2,
          "pointValue": 150,
          "captureCount": 5,
          "ownedByMe": true,
          "unclaimed": false,
          "owner": {
            "userId": "uuid",
            "username": "CommanderJax",
            "level": 14
          },
          "fillColor": "#CCFF00",
          "strokeColor": "#CCFF00",
          "fillOpacity": 0.4
        }
      }
    ],
    "meta": {
      "total": 4,
      "mine": 2,
      "unclaimed": 1,
      "contested": 1
    }
  }
}
```

**Colour logic:**
```
ownedByMe  → fill #CCFF00  (your colour — primary)
unclaimed  → fill #050505, stroke #00E5FF
enemy      → fill #8A2BE2  (tertiary)
```

---

## 5. Territories

### GET `/territories/nearby?lat=&lon=&radiusKm=5`
Find territories near a position — used for initial map load.

### POST `/territories/{territoryId}/capture?sessionId=uuid`
Capture a territory at the end of a run. Server awards 50 XP.

### GET `/territories/mine?page=0&size=20`
All territories you own — for profile/stats screen.

---

## 6. Habits

### GET `/habits`
All active habits.

### POST `/habits`
```json
{
  "name": "Hydration",
  "habitType": "HYDRATION",
  "targetValue": 8.0,
  "unit": "glasses",
  "frequency": "DAILY",
  "colorHex": "#00E5FF",
  "icon": "water_drop"
}
```

**Habit types:** `HYDRATION` · `VITAMINS` · `SLEEP` · `FOCUS` · `MEDITATION` · `NUTRITION` · `READING` · `COLD_SHOWER` · `NO_SOCIAL_MEDIA` · `CUSTOM`

**Frequencies:** `DAILY` · `WEEKLY` · `WEEKDAYS` · `WEEKENDS`

### POST `/habits/log`
```json
{
  "habitId": "uuid",
  "logDate": "2026-05-19",
  "completedValue": 8.0,
  "notes": "optional",
  "localId": "client-uuid (for offline sync)"
}
```

> Calling again for the same `(habitId, logDate)` **upserts** — safe to retry.

### GET `/habits/logs?date_str=2026-05-19`
Logs for a specific date (defaults to today).

---

## 7. Social

### GET `/social/feed?page=0&size=20`
Activity feed from friends + self.

**Feed activity types:**
`RUN_COMPLETED` · `TERRITORY_CAPTURED` · `ACHIEVEMENT_EARNED` · `STREAK_MILESTONE` · `LEVEL_UP` · `TEAM_JOINED`

### POST `/social/friends/{friendId}/request`
Send a friend request.

### POST `/social/friends/{friendId}/accept`
Accept an incoming request.

### GET `/social/friends`
All accepted friends.

### GET `/social/friends/pending`
Incoming pending requests.

---

## 8. Leaderboard

### GET `/leaderboard?type=xp&top=50`

```json
{
  "data": [
    { "rank": 1, "userId": "uuid", "username": "NeonRunner", "score": 12450 },
    { "rank": 2, "userId": "uuid", "username": "CommanderJax", "score": 9800 }
  ]
}
```

**Types:** `xp` (default) · `distance`  
**Max top:** 100

---

## 9. Todos

### Todo data model

Every todo returned by the API has this shape:

```json
{
  "id": "uuid",
  "user_id": "uuid",
  "title": "Morning stretching",
  "description": "10 minutes",
  "todo_date": "2026-05-23",
  "category": "FITNESS",
  "status": "PENDING",
  "is_completed": false,
  "completed_at": null,
  "created_at": "2026-05-23T06:00:00Z"
}
```

**Status values:**

| `status` | `is_completed` | Meaning |
|---|---|---|
| `PENDING` | false | Not acted on yet |
| `DONE` | true | Completed |
| `CANCELLED` | false | Skipped / won't do |
| `DEFERRED` | false | Do later |

---

### Circular status button — UI spec

Each todo card has a **circular button on the left**, beside the title/description. Its icon and colour changes with `status`. There are no toast messages — the icon itself is the feedback.

```
┌─────────────────────────────────────────────┐
│  ○  Morning stretching          [···]        │  ← PENDING:  empty/outline circle
│     10 minutes · FITNESS                     │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ✓  Morning stretching          [···]        │  ← DONE:     green filled circle, ✓ inside
│     10 minutes · FITNESS                     │    title has strikethrough
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ✕  Morning stretching          [···]        │  ← CANCELLED: red filled circle, ✕ inside
│     10 minutes · FITNESS                     │    title has strikethrough, card dimmed
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ⏰  Morning stretching         [···]        │  ← DEFERRED: amber filled circle, clock inside
│     10 minutes · FITNESS                     │    card has amber left border accent
└─────────────────────────────────────────────┘
```

**Circle colours:**

| status | Circle fill | Icon | Title |
|---|---|---|---|
| `PENDING` | transparent, border `#444` | none | normal |
| `DONE` | `#CCFF00` (primary) | ✓ white | strikethrough, dimmed |
| `CANCELLED` | `#FF6B6B` (danger) | ✕ white | strikethrough, dimmed |
| `DEFERRED` | `#FFA500` amber | clock white | normal, amber left border on card |

---

### Tapping the circle — status picker

**When status is PENDING**, tapping the circle opens an inline picker (bottom sheet or small pop-up anchored to the card) with 3 choices:

```
┌──────────────────────┐
│  ✓  Done             │
│  ✕  Cancel / Skip    │
│  ⏰  Do Later        │
└──────────────────────┘
```

User taps one → call `PATCH /todos/{id}/status` → icon on the circle updates immediately → picker closes. **No toast.**

**When status is DONE / CANCELLED / DEFERRED**, tapping the circle re-opens the same picker so the user can change their selection. The current selection is highlighted. There is also an "Undo" / "Mark Pending" option to reset back to `PENDING`:

```
┌──────────────────────┐
│  ✓  Done        ← ●  │  (current — highlighted)
│  ✕  Cancel / Skip    │
│  ⏰  Do Later        │
│  ○  Mark Pending     │
└──────────────────────┘
```

---

### API calls for the status picker

All calls go to the single generic endpoint — just change the `status` value:

**Mark Done**
```
PATCH /api/v1/todos/{todoId}/status
{ "status": "DONE" }
```

**Mark Cancelled**
```
PATCH /api/v1/todos/{todoId}/status
{ "status": "CANCELLED" }
```

**Mark Deferred (Do Later)**
```
PATCH /api/v1/todos/{todoId}/status
{ "status": "DEFERRED" }
```

**Undo / Reset to Pending**
```
PATCH /api/v1/todos/{todoId}/status
{ "status": "PENDING" }
```

Response always returns the updated todo row. Update the card UI using the returned `status` field — no need to re-fetch the list.

---

### GET `/todos?todo_date=2026-05-23`

Returns all todos for a date (defaults to today). Use `status` field to render each card's circle icon.

```json
{
  "data": [
    {
      "id": "uuid-1",
      "title": "Morning stretching",
      "description": "10 minutes",
      "todo_date": "2026-05-23",
      "category": "FITNESS",
      "status": "DONE",
      "is_completed": true,
      "completed_at": "2026-05-23T07:15:00Z",
      "created_at": "2026-05-23T06:00:00Z"
    },
    {
      "id": "uuid-2",
      "title": "Evening run",
      "description": null,
      "todo_date": "2026-05-23",
      "category": "GENERAL",
      "status": "PENDING",
      "is_completed": false,
      "completed_at": null,
      "created_at": "2026-05-23T06:01:00Z"
    }
  ]
}
```

---

### POST `/todos`

```json
{
  "title": "Evening run",
  "description": "At least 3 km",
  "todoDate": "2026-05-23",
  "category": "FITNESS"
}
```

New todos always start with `status: "PENDING"`.

---

### PATCH `/todos/{todoId}/status` ← primary action endpoint

```json
{ "status": "DONE" }
```

Accepts: `PENDING` | `DONE` | `CANCELLED` | `DEFERRED`

---

### PATCH `/todos/{todoId}` — edit title/description/category

```json
{
  "title": "Updated title",
  "description": "Updated desc",
  "category": "FITNESS"
}
```

All fields optional.

---

### DELETE `/todos/{todoId}`

Hard delete. No body.

---

### GET `/todos/stats`

```json
{
  "data": {
    "daily":   { "completedCount": 3, "totalCount": 5, "percentage": 60.0 },
    "weekly":  { "completedCount": 18, "totalCount": 30, "percentage": 60.0 },
    "monthly": { "completedCount": 72, "totalCount": 120, "percentage": 60.0 },
    "overallScore": 60.0
  }
}
```

---

## 10. Notifications

### GET `/notifications?page=0&size=30`

```json
{
  "data": {
    "content": [
      {
        "id": "uuid",
        "title": "Don't forget!",
        "body": "Morning stretching is pending",
        "notification_type": "TODO_REMINDER",
        "is_read": false,
        "reference_id": "todo-uuid",
        "deep_link": "runrealm://todos/uuid",
        "created_at": "2026-05-23T08:00:00Z"
      }
    ],
    "totalElements": 5
  }
}
```

---

### Notification bell — todo action UI

When a notification has `notification_type == "TODO_REMINDER"`, show **inline action buttons** directly on the notification card inside the bell panel. Do not navigate away.

```
┌───────────────────────────────────────────────┐
│  🔔  Don't forget!                            │
│      Morning stretching is pending            │
│                                               │
│  [ ✓ Done ]  [ ✕ Skip ]  [ ⏰ Later ]        │
└───────────────────────────────────────────────┘
```

Tapping any button calls:

```
POST /api/v1/notifications/{notificationId}/todo-action
{ "status": "DONE" }          ← or "CANCELLED" or "DEFERRED"
```

This single call does both: updates the todo status **and** marks the notification as read. Response:

```json
{
  "data": {
    "todo": {
      "id": "todo-uuid",
      "title": "Morning stretching",
      "status": "DONE",
      "is_completed": true,
      "completed_at": "2026-05-23T08:05:00Z"
    }
  },
  "message": "Todo marked as DONE"
}
```

After the call, replace the 3 action buttons with the icon that matches the chosen status (same circle icon as the todo card): ✓ green / ✕ red / ⏰ amber. **No toast.**

**Precondition for this to work:** when the server creates a `TODO_REMINDER` notification, it must set `reference_id = <todo_id>`. The `notification_type` must be exactly `"TODO_REMINDER"`.

---

### GET `/notifications/unread-count` → `{ "unreadCount": 3 }`
### POST `/notifications/read-all`
### POST `/notifications/{notificationId}/read`

**Other notification types:**
`STREAK_REMINDER` · `CHALLENGE_INVITE` · `TERRITORY_LOST` · `ACHIEVEMENT_UNLOCKED` · `FRIEND_RUN` · `LEVEL_UP` · `SYNC_COMPLETE` · `SYSTEM`

---

## 11. Profile

### GET `/profile`
Own full profile.

### GET `/profile/{userId}`
Another user's public profile (returns 400 if profile is private).

### PATCH `/profile`
```json
{
  "displayName": "Commander Jax",
  "bio": "Elite runner — New Delhi",
  "city": "New Delhi",
  "avatarUrl": "https://cdn.example.com/avatar.jpg",
  "isPublic": true
}
```
All fields optional — only include what changed.

---

## 12. Offline Sync

### POST `/sync/batch`
Send all offline writes in one batch when connectivity restores. **Idempotent** — safe to retry.

```json
{
  "items": [
    {
      "entityType": "RUN_SESSION",
      "operation": "CREATE",
      "localId": "client-uuid",
      "payload": "{\"activityType\":\"RUN\",\"startTime\":\"2026-05-19T06:00:00Z\",\"localId\":\"client-uuid\"}",
      "occurredAt": "2026-05-19T06:00:00Z"
    },
    {
      "entityType": "HABIT_LOG",
      "operation": "CREATE",
      "localId": "client-uuid-2",
      "payload": "{\"habitId\":\"uuid\",\"logDate\":\"2026-05-19\",\"completedValue\":8.0}",
      "occurredAt": "2026-05-19T08:00:00Z"
    }
  ]
}
```

**Entity types:** `RUN_SESSION` · `HABIT_LOG` · `ROUTE_POINT`

**Response**
```json
{
  "data": {
    "totalReceived": 2,
    "totalSynced": 2,
    "totalFailed": 0,
    "results": [
      { "localId": "client-uuid",   "serverId": "server-uuid-1", "success": true,  "error": null },
      { "localId": "client-uuid-2", "serverId": "server-uuid-2", "success": true,  "error": null }
    ]
  }
}
```

### GET `/sync/pending-count` → `{ "pendingCount": 0 }`

---

## XP & Level System

| Action | XP |
|---|---|
| Running 1 km | 10 XP × streak multiplier |
| Territory capture | 50 XP |
| Habit completed | 5 XP × streak multiplier |
| Challenge win | 100 XP |

**Streak multiplier:** `min(1.0 + streak × 0.1, 3.0)` — capped at 3× at a 20-day streak  
**Level formula:** `level²  × 100 = XP threshold` (level 5 = 2500 XP, level 10 = 10 000 XP)

```kotlin
fun levelFromXp(xp: Int): Int {
    var level = 1
    while (xp >= (level + 1) * (level + 1) * 100) level++
    return level
}
```

---

## Offline-first Flow (Android Room DB)

```
[App goes offline]
  ↓
Every write → Room DB (synced = false, generate localId)
  ↓
[Connectivity restored]
  ↓
Collect all rows WHERE synced = false
  ↓
POST /sync/batch { items: [...] }
  ↓
For success results → UPDATE Room SET serverId = result.serverId, synced = true
For failed results  → keep synced = false, retry next cycle
```

**Key rule:** Always generate `localId` (UUID) on the client before writing locally.  
The server uses `localId` as the deduplication key — calling the same endpoint twice returns the same response.

---

## Running Map — Recommended Polling Intervals

| Action | Interval |
|---|---|
| `POST /map/location` | Every 10 s during active run |
| `GET /map/territories/live` | Every 30 s during active run |
| `POST /sessions/{id}/points` | Batch every 60 s (buffer GPS points locally) |
| `GET /notifications/unread-count` | On app resume |
| `GET /dashboard` | On home screen open |

---

## Error Codes

| HTTP | When |
|---|---|
| 400 | Validation error, business rule violation (duplicate username, session not found…) |
| 401 | Missing or expired JWT |
| 403 | Valid JWT but resource belongs to another user |
| 429 | Supabase Auth rate limit (too many sign-up attempts) |
| 503 | DB tables not yet migrated |
| 500 | Unexpected server error |

---

## Database Tables (Supabase)

| Table | Purpose |
|---|---|
| `user_profiles` | Extended user data (XP, level, streak, location) |
| `run_sessions` | Each activity (RUN/WALK/CYCLE/HIKE) |
| `route_points` | GPS points per session |
| `territories` | Conquestable map zones |
| `territory_captures` | Capture history |
| `habits` | User-defined habits |
| `habit_logs` | Daily habit progress |
| `streaks` | Streak tracking per user |
| `xp_transactions` | XP ledger (audit trail) |
| `activity_feed` | Social feed events |
| `user_friends` | Friend graph (PENDING / ACCEPTED) |
| `notifications` | Push notification inbox |
| `sync_queue` | Offline sync deduplication log |
| `teams` | Team / clan data |
| `team_members` | Team membership |
| `achievements` | Achievement definitions |
| `user_achievements` | User → achievement mapping |

---

*Generated: 2026-05-19 · RunRealm FastAPI v2.0.0*
