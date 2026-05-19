from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok

router = APIRouter()


@router.get("")
def leaderboard(
    type: str = "xp",
    top: int = 50,
    user=Depends(get_current_user),
    db: Client = Depends(get_db),
):
    if type not in ("xp", "distance"):
        raise HTTPException(400, "type must be 'xp' or 'distance'")

    top = min(top, 100)

    if type == "xp":
        res = (
            db.table("user_profiles")
            .select("user_id, username, xp_points")
            .order("xp_points", desc=True)
            .limit(top)
            .execute()
        )
        entries = [
            {
                "rank": i + 1,
                "userId": r["user_id"],
                "username": r["username"],
                "score": r["xp_points"] or 0,
            }
            for i, r in enumerate(res.data or [])
        ]
    else:
        # Sum distance per user from completed sessions
        res = (
            db.table("run_sessions")
            .select("user_id, distance_km")
            .eq("status", "COMPLETED")
            .execute()
        )
        totals: dict[str, float] = {}
        for row in (res.data or []):
            uid = row["user_id"]
            totals[uid] = totals.get(uid, 0) + (row.get("distance_km") or 0)

        # Fetch usernames
        if totals:
            profiles = (
                db.table("user_profiles")
                .select("user_id, username")
                .in_("user_id", list(totals.keys()))
                .execute()
            )
            name_map = {p["user_id"]: p["username"] for p in (profiles.data or [])}
        else:
            name_map = {}

        sorted_entries = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top]
        entries = [
            {
                "rank": i + 1,
                "userId": uid,
                "username": name_map.get(uid, "Unknown"),
                "score": round(dist, 2),
            }
            for i, (uid, dist) in enumerate(sorted_entries)
        ]

    return ok(entries)
