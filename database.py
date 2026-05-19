import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_db() -> Client:
    return supabase


async def test_connection() -> None:
    try:
        # Lightweight auth ping — works even before tables are created
        supabase.auth.get_session()
        print(f"✅  Supabase connected → {SUPABASE_URL}")
    except Exception as exc:
        # Connection-level failure (bad URL / key)
        if "Invalid API key" in str(exc) or "connection" in str(exc).lower():
            print(f"❌  Supabase connection failed: {exc}")
            raise
        # Table-not-found etc. — DB reachable, schema just not created yet
        print(f"✅  Supabase connected → {SUPABASE_URL}")
        print(f"⚠️   Run migrations/schema.sql in Supabase SQL Editor first")
