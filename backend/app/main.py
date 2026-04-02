import os

if os.getenv("ENVIRONMENT") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.sentry import init_sentry

init_sentry()
from app.api.auth import router as auth_router
from app.api.gmail import router as gmail_router
from app.api.ingest import router as ingest_router
from app.api.sources import router as sources_router
from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.api.webhooks import router as webhooks_router
from app.api.whatsapp import router as whatsapp_router

app = FastAPI(title="Delia API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://api.usedelia.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "PATCH", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(ingest_router)
app.include_router(sources_router)
app.include_router(tasks_router)
app.include_router(users_router)
app.include_router(webhooks_router)
app.include_router(whatsapp_router)


@app.get("/health")
def health():
    return {"status": "ok"}
