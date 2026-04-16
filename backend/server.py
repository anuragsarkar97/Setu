from contextlib import asynccontextmanager

from fastapi import FastAPI

import faiss_index
from db import DB_NAME, close_client, get_db
from routers import agents, intents, matching, routing


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[startup] server ready | db={DB_NAME}")
    # Build FAISS index from MongoDB — silently skipped if DB is not yet configured
    try:
        count = await faiss_index.build(get_db())
        print(f"[startup] FAISS index ready ({count} vectors)")
    except Exception as e:
        print(f"[startup] FAISS build skipped: {e}")
    yield
    close_client()


app = FastAPI(
    title="Distributed Agent Intent Bulletin",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.include_router(agents.router)
app.include_router(intents.router)
app.include_router(matching.router)
app.include_router(routing.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/ping-db")
async def ping_db():
    from db import get_client
    c = get_client()
    result = await c.admin.command("ping")
    from db import DB_NAME
    return {"mongo_ping": result, "db": DB_NAME}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8001, reload=True)