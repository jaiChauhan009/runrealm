from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ── response wrapper ──────────────────────────────────────────────────────────

def ok(data: Any = None, message: str | None = None) -> dict:
    return {
        "success": True,
        "message": message,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    displayName: Optional[str] = None
    deviceId: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    deviceId: Optional[str] = None
    fcmToken: Optional[str] = None


# ── sessions ──────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    activityType: str
    startTime: datetime
    localId: str


class EndSessionRequest(BaseModel):
    endTime: datetime
    distanceKm: float = 0.0
    avgPaceMinPerKm: Optional[float] = None
    maxSpeedKmh: Optional[float] = None
    caloriesBurned: Optional[int] = None
    elevationGainM: Optional[float] = None
    routeGeoJson: Optional[str] = None


class RoutePointRequest(BaseModel):
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speedKmh: Optional[float] = None
    accuracyM: Optional[float] = None
    sequenceNumber: Optional[int] = None
    recordedAt: datetime
    localId: Optional[str] = None


# ── habits ────────────────────────────────────────────────────────────────────

class HabitCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    habitType: str
    targetValue: Optional[float] = None
    unit: Optional[str] = None
    frequency: str = "DAILY"
    icon: Optional[str] = None
    colorHex: Optional[str] = None


class HabitLogRequest(BaseModel):
    habitId: str
    logDate: date
    completedValue: float = 0.0
    notes: Optional[str] = None
    localId: Optional[str] = None


# ── sync ──────────────────────────────────────────────────────────────────────

class SyncItem(BaseModel):
    entityType: str
    operation: str
    localId: str
    payload: str
    occurredAt: datetime


class SyncBatchRequest(BaseModel):
    items: List[SyncItem]


# ── profile ───────────────────────────────────────────────────────────────────

class ProfileUpdateRequest(BaseModel):
    displayName: Optional[str] = None
    bio: Optional[str] = None
    city: Optional[str] = None
    avatarUrl: Optional[str] = None
    isPublic: Optional[bool] = None
    instagramHandle: Optional[str] = None
    twitterHandle: Optional[str] = None
    stravaUrl: Optional[str] = None
    linkedinUrl: Optional[str] = None


# ── todos ─────────────────────────────────────────────────────────────────────

class TodoCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    todoDate: Optional[date] = None
    category: Optional[str] = "GENERAL"
    scheduledAt: Optional[str] = None  # "HH:MM" time string sent by the app


class TodoUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = None
    scheduledAt: Optional[str] = None  # "HH:MM" — used by "Do Later" to persist new reminder time


class TodoStatusRequest(BaseModel):
    status: Literal["PENDING", "DONE", "CANCELLED", "DEFERRED"]


class NotificationTodoActionRequest(BaseModel):
    status: Literal["DONE", "CANCELLED", "DEFERRED"]


# ── leagues ───────────────────────────────────────────────────────────────────

class SocialLinkItem(BaseModel):
    platform: str
    url: str
    label: Optional[str] = None


class LeagueCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    scope: Literal["GLOBAL", "COUNTRY", "STATE", "LOCAL"] = "GLOBAL"
    socialLinks: Optional[List[SocialLinkItem]] = None


# ── territories ───────────────────────────────────────────────────────────────

class TerritoryClaimRequest(BaseModel):
    sessionId: str
