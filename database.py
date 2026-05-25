import os
import threading
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

_thread_local = threading.local()


def get_db() -> Client:
    if not hasattr(_thread_local, "client"):
        _thread_local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _thread_local.client


async def test_connection() -> None:
    try:
        db = get_db()
        db.auth.get_session()
        db.table("user_profiles").select("id").limit(1).execute()
        print(f"✅  Supabase connected and warmed → {SUPABASE_URL}")
    except Exception as exc:
        if "Invalid API key" in str(exc) or "connection" in str(exc).lower():
            print(f"❌  Supabase connection failed: {exc}")
            raise
        print(f"✅  Supabase connected → {SUPABASE_URL}")
        print(f"⚠️   Run migrations/schema.sql in Supabase SQL Editor first")
