from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from postgrest.exceptions import APIError as PostgRESTError

from database import test_connection
from routers import (
    auth,
    content,
    dashboard,
    habits,
    leagues,
    leaderboard,
    map,
    notifications,
    profile,
    sessions,
    social,
    sync,
    territories,
    todos,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await test_connection()
    yield


app = FastAPI(
    title="RunRealm API",
    version="2.0.0",
    description="RunRealm fitness backend — FastAPI + Supabase",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PostgRESTError)
async def postgrest_exception_handler(request: Request, exc: PostgRESTError):
    raw = exc.args[0] if exc.args else {}
    if isinstance(raw, dict):
        msg = raw.get("message", str(exc))
    else:
        msg = str(raw)
    status = 503 if "schema cache" in msg else 400
    friendly = (
        "Database tables not set up — run migrations/schema.sql in Supabase SQL Editor"
        if "schema cache" in msg
        else msg
    )
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "message": friendly,
            "data": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = errors[0].get("msg", "Validation error") if errors else "Validation error"
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": msg,
            "data": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": str(detail),
            "data": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


app.include_router(auth.router,          prefix="/api/v1/auth",          tags=["Auth"])
app.include_router(dashboard.router,     prefix="/api/v1/dashboard",     tags=["Dashboard"])
app.include_router(sessions.router,      prefix="/api/v1/sessions",      tags=["Sessions"])
app.include_router(territories.router,   prefix="/api/v1/territories",   tags=["Territories"])
app.include_router(habits.router,        prefix="/api/v1/habits",        tags=["Habits"])
app.include_router(sync.router,          prefix="/api/v1/sync",          tags=["Sync"])
app.include_router(social.router,        prefix="/api/v1/social",        tags=["Social"])
app.include_router(leagues.router,       prefix="/api/v1/leagues",       tags=["Leagues"])
app.include_router(leaderboard.router,   prefix="/api/v1/leaderboard",   tags=["Leaderboard"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(profile.router,       prefix="/api/v1/profile",       tags=["Profile"])
app.include_router(map.router,           prefix="/api/v1/map",           tags=["Map"])
app.include_router(todos.router,         prefix="/api/v1/todos",         tags=["Todos"])
app.include_router(content.router,       prefix="/api/v1/content",       tags=["Content"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
