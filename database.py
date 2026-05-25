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
        # Prime both Auth and PostgREST connections so the first real request
        # doesn't pay the connection-establishment cost.
        supabase.auth.get_session()
        supabase.table("user_profiles").select("id").limit(1).execute()
        print(f"✅  Supabase connected and warmed → {SUPABASE_URL}")
    except Exception as exc:
        if "Invalid API key" in str(exc) or "connection" in str(exc).lower():
            print(f"❌  Supabase connection failed: {exc}")
            raise
        print(f"✅  Supabase connected → {SUPABASE_URL}")
        print(f"⚠️   Run migrations/schema.sql in Supabase SQL Editor first")
