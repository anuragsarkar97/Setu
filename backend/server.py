import uvicorn
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI

import store
from routers import agents, chat, intent_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    store._load()
    print(f"[startup] store loaded — {len(store._DATA['agents'])} agents, {len(store._DATA['intents'])} intents")
    yield


app = FastAPI(
    title="Distributed Agent Intent Bulletin",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.include_router(agents.router)
app.include_router(intent_router.router)
app.include_router(intent_router.intents_router)
app.include_router(chat.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)