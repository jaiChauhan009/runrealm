import json

from fastapi import APIRouter, Depends
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import SyncBatchRequest, ok

router = APIRouter()


def _dispatch(db: Client, uid: str, item) -> str:
    """Process one sync item and return the server UUID."""
    entity = item.entityType
    payload = json.loads(item.payload)

    if entity == "RUN_SESSION":
        existing = (
            db.table("run_sessions")
            .select("id")
            .eq("local_id", item.localId)
            .eq("user_id", uid)
            .execute()
        )
        if existing.data:
            return existing.data[0]["id"]
        res = db.table("run_sessions").insert({
            "user_id": uid,
            "local_id": item.localId,
            "activity_type": payload.get("activityType", "RUN"),
            "start_time": payload.get("startTime"),
            "status": "ACTIVE",
            "distance_km": 0.0,
            "synced": True,
        }).execute()
        return res.data[0]["id"]

    elif entity == "HABIT_LOG":
        habit_id = payload.get("habitId")
        log_date = payload.get("logDate")
        existing = (
            db.table("habit_logs")
            .select("id")
            .eq("habit_id", habit_id)
            .eq("log_date", log_date)
            .execute()
        )
        if existing.data:
            return existing.data[0]["id"]
        res = db.table("habit_logs").insert({
            "user_id": uid,
            "habit_id": habit_id,
            "log_date": log_date,
            "completed_value": payload.get("completedValue", 0),
            "is_completed": False,
            "xp_earned": 0,
            "local_id": item.localId,
            "synced": True,
        }).execute()
        return res.data[0]["id"]

    elif entity == "ROUTE_POINT":
        existing = (
            db.table("route_points")
            .select("id")
            .eq("local_id", item.localId)
            .execute()
        )
        if existing.data:
            return existing.data[0]["id"]
        res = db.table("route_points").insert({
            "user_id": uid,
            "local_id": item.localId,
            "session_id": payload.get("sessionId"),
            "latitude": payload.get("latitude", 0),
            "longitude": payload.get("longitude", 0),
            "altitude": payload.get("altitude"),
            "speed_kmh": payload.get("speedKmh"),
            "accuracy_m": payload.get("accuracyM"),
            "sequence_number": payload.get("sequenceNumber"),
            "recorded_at": payload.get("recordedAt"),
        }).execute()
        return res.data[0]["id"]

    else:
        raise ValueError(f"No sync handler for {entity}")


@router.post("/batch")
def sync_batch(body: SyncBatchRequest, user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    results = []
    synced = 0
    failed = 0

    if not body.items:
        return ok({"totalReceived": 0, "totalSynced": 0, "totalFailed": 0, "results": []})

    # Pre-fetch all idempotency records in one query (was N per-item queries)
    local_ids = [item.localId for item in body.items]
    existing_res = (
        db.table("sync_queue")
        .select("local_id,server_id,status")
        .eq("user_id", uid)
        .in_("local_id", local_ids)
        .execute()
    )
    already_synced = {
        r["local_id"]: r for r in (existing_res.data or [])
        if r.get("status") == "SYNCED"
    }

    for item in body.items:
        if item.localId in already_synced:
            results.append({
                "localId": item.localId,
                "serverId": already_synced[item.localId]["server_id"],
                "success": True,
                "error": None,
            })
            synced += 1
            continue

        # Insert/update queue entry
        queue_res = db.table("sync_queue").upsert({
            "user_id": uid,
            "entity_type": item.entityType,
            "operation": item.operation,
            "local_id": item.localId,
            "payload": item.payload,
            "status": "SYNCING",
            "occurred_at": item.occurredAt.isoformat(),
        }).execute()

        try:
            server_id = _dispatch(db, uid, item)
            db.table("sync_queue").update({
                "server_id": server_id,
                "status": "SYNCED",
            }).eq("user_id", uid).eq("local_id", item.localId).execute()
            results.append({"localId": item.localId, "serverId": server_id, "success": True, "error": None})
            synced += 1
        except Exception as exc:
            db.table("sync_queue").update({
                "status": "FAILED",
                "error_message": str(exc),
            }).eq("user_id", uid).eq("local_id", item.localId).execute()
            results.append({"localId": item.localId, "serverId": None, "success": False, "error": str(exc)})
            failed += 1

    return ok({
        "totalReceived": len(body.items),
        "totalSynced": synced,
        "totalFailed": failed,
        "results": results,
    })


@router.get("/pending-count")
def pending_count(user=Depends(get_current_user), db: Client = Depends(get_db)):
    uid = user.id
    res = db.table("sync_queue").select("id", count="exact").eq("user_id", uid).eq("status", "PENDING").execute()
    return ok({"pendingCount": res.count or 0})
