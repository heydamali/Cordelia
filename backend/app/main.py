import os

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from fastapi import FastAPI
from app.api.auth import router as auth_router

app = FastAPI(title="EA Agent API", version="0.1.0")

app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}
