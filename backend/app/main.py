import os

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from fastapi import FastAPI
from app.api.auth import router as auth_router
from app.api.gmail import router as gmail_router
from app.api.webhooks import router as webhooks_router

app = FastAPI(title="Cordelia API", version="0.1.0")

app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(webhooks_router)


@app.get("/health")
def health():
    return {"status": "ok"}
