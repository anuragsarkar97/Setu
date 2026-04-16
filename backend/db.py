import os

from dotenv import load_dotenv
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConfigurationError, ServerSelectionTimeoutError

load_dotenv()

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

# Lazily initialized — won't DNS-resolve until first use
client: AsyncIOMotorClient = None
db = None


def get_client() -> AsyncIOMotorClient:
    global client, db
    if client is None:
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        print(f"[db] lazy-connected | db={DB_NAME}")
    return client


def get_db():
    try:
        get_client()
    except (ConfigurationError, ServerSelectionTimeoutError) as e:
        raise HTTPException(
            status_code=503,
            detail=f"DB not reachable — check MONGO_URL in .env: {e}",
        )
    return db


async def ensure_indexes():
    """Create indexes once after first real DB connection."""
    try:
        await get_db().agents.create_index("agent_id", unique=True)
        await get_db().intents.create_index("intent_id", unique=True)
        print("[db] indexes ensured")
    except Exception as e:
        print(f"[db] index creation skipped: {e}")


def close_client():
    global client
    if client is not None:
        client.close()
        print("[shutdown] MongoDB connection closed")
