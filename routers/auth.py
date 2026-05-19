from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from database import get_db
from schemas import LoginRequest, RegisterRequest, ok

router = APIRouter()


def _profile_row(db: Client, user_id: str) -> dict:
    res = db.table("user_profiles").select("*").eq("user_id", user_id).single().execute()
    return res.data or {}


@router.post("/register")
def register(body: RegisterRequest, db: Client = Depends(get_db)):
    # Check username uniqueness
    taken = db.table("user_profiles").select("id").eq("username", body.username).execute()
    if taken.data:
        raise HTTPException(400, "Username already taken")

    # Create Supabase Auth user
    try:
        auth_res = db.auth.sign_up({"email": body.email, "password": body.password})
    except Exception as exc:
        raise HTTPException(400, str(exc))

    if not auth_res.user:
        raise HTTPException(400, "Registration failed — email may already be registered")

    uid = auth_res.user.id
    display = body.displayName or body.username

    # Create profile row (ignore if already exists from a previous attempt)
    existing = db.table("user_profiles").select("id").eq("user_id", uid).execute()
    if not existing.data:
        db.table("user_profiles").insert({
            "user_id": uid,
            "username": body.username,
            "display_name": display,
            "device_id": body.deviceId,
            "level": 1,
            "xp_points": 0,
        }).execute()
        db.table("streaks").insert({
            "user_id": uid,
            "current_streak": 0,
            "best_streak": 0,
        }).execute()

    # Email confirmation is ON — no session yet, user must verify first
    if not auth_res.session:
        return ok({
            "accessToken": None,
            "refreshToken": None,
            "tokenType": "Bearer",
            "expiresIn": 0,
            "userId": uid,
            "username": body.username,
            "email": body.email,
            "level": 1,
            "xpPoints": 0,
            "emailConfirmationRequired": True,
            "message": "Account created — check your email to verify before logging in.",
        })

    session = auth_res.session
    return ok({
        "accessToken": session.access_token,
        "refreshToken": session.refresh_token,
        "tokenType": "Bearer",
        "expiresIn": 3600,
        "userId": uid,
        "username": body.username,
        "email": body.email,
        "level": 1,
        "xpPoints": 0,
        "emailConfirmationRequired": False,
    })


@router.post("/login")
def login(body: LoginRequest, db: Client = Depends(get_db)):
    try:
        auth_res = db.auth.sign_in_with_password({"email": body.email, "password": body.password})
    except Exception as exc:
        raise HTTPException(401, "Invalid credentials")

    if not auth_res.user or not auth_res.session:
        raise HTTPException(401, "Invalid credentials")

    uid = auth_res.user.id

    # Update device / FCM token if provided
    update: dict = {}
    if body.deviceId:
        update["device_id"] = body.deviceId
    if body.fcmToken:
        update["fcm_token"] = body.fcmToken
    if update:
        db.table("user_profiles").update(update).eq("user_id", uid).execute()

    profile = _profile_row(db, uid)

    return ok({
        "accessToken": auth_res.session.access_token,
        "refreshToken": auth_res.session.refresh_token,
        "tokenType": "Bearer",
        "expiresIn": 3600,
        "userId": uid,
        "username": profile.get("username", ""),
        "email": body.email,
        "level": profile.get("level", 1),
        "xpPoints": profile.get("xp_points", 0),
    })


@router.post("/refresh")
def refresh_token(refresh_token: str, db: Client = Depends(get_db)):
    try:
        res = db.auth.refresh_session(refresh_token)
    except Exception:
        raise HTTPException(401, "Invalid refresh token")

    if not res.session:
        raise HTTPException(401, "Invalid refresh token")

    return ok({
        "accessToken": res.session.access_token,
        "refreshToken": res.session.refresh_token,
        "tokenType": "Bearer",
        "expiresIn": 3600,
    })
