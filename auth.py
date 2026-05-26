import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client

from cache import cache_get, cache_set
from database import get_db

_bearer = HTTPBearer()

# Cache verified tokens for 5 minutes to avoid a Supabase round-trip on every request.
_AUTH_TTL = 300


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Client = Depends(get_db),
):
    """Extract and verify the Supabase JWT; return the auth User object."""
    token = creds.credentials
    # Use a short hash of the token as the cache key so raw tokens aren't stored in memory.
    cache_key = "auth:" + hashlib.sha256(token.encode()).hexdigest()[:32]

    cached_user = cache_get(cache_key)
    if cached_user is not None:
        return cached_user

    try:
        resp = db.auth.get_user(token)
        if not resp or not resp.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        cache_set(cache_key, resp.user, ttl_seconds=_AUTH_TTL)
        return resp.user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
