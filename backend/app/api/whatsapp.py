from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LinkStartRequest(BaseModel):
    phone_number: str


class LinkStartResponse(BaseModel):
    pairing_code: str
    expires_in: int = 60


class LinkStatusResponse(BaseModel):
    status: str  # "disconnected" | "pairing" | "connected"
    phone_number: str | None = None


class WebhookConnectedRequest(BaseModel):
    user_id: str
    phone_number: str


class WebhookDisconnectedRequest(BaseModel):
    user_id: str
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def _validate_phone(phone: str) -> str:
    cleaned = phone.replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Invalid phone number format")
    return cleaned


def _require_service_key(x_service_key: str = Header(...)) -> None:
    if x_service_key != settings.WHATSAPP_SERVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid service key")


def _get_or_create_setting(db: Session, user_id: str) -> UserSourceSetting:
    setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == "whatsapp")
        .first()
    )
    if setting is None:
        setting = UserSourceSetting(user_id=user_id, source="whatsapp", enabled=False)
        db.add(setting)
        db.flush()
    return setting


# ---------------------------------------------------------------------------
# User-facing endpoints (JWT auth)
# ---------------------------------------------------------------------------

@router.post("/link/start", response_model=LinkStartResponse)
async def link_start(
    body: LinkStartRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Request a pairing code to link WhatsApp."""
    phone = _validate_phone(body.phone_number)
    user_id = str(user.id)

    _get_or_create_setting(db, user_id)
    db.commit()

    # Proxy to Node.js WhatsApp service
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{settings.WHATSAPP_SERVICE_URL}/sessions/start",
                json={"userId": user_id, "phoneNumber": phone},
                headers={"X-Service-Key": settings.WHATSAPP_SERVICE_API_KEY},
            )
        except httpx.RequestError as exc:
            logger.error("WhatsApp service unreachable: %s", exc)
            raise HTTPException(status_code=502, detail="WhatsApp service unavailable")

    if resp.status_code != 200:
        detail = resp.json().get("error", "Failed to start linking")
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    return LinkStartResponse(pairing_code=data["pairingCode"], expires_in=60)


@router.get("/link/status", response_model=LinkStatusResponse)
async def link_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check current WhatsApp linking status."""
    user_id = str(user.id)

    # Check DB first for connected state
    setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == "whatsapp")
        .first()
    )
    if setting and setting.enabled and setting.sync_cursor:
        cursor = json.loads(setting.sync_cursor)
        return LinkStatusResponse(status="connected", phone_number=cursor.get("phone_number"))

    # Proxy to Node service for real-time status
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{settings.WHATSAPP_SERVICE_URL}/sessions/{user_id}/status",
                headers={"X-Service-Key": settings.WHATSAPP_SERVICE_API_KEY},
            )
        except httpx.RequestError:
            return LinkStatusResponse(status="disconnected")

    if resp.status_code != 200:
        return LinkStatusResponse(status="disconnected")

    data = resp.json()
    return LinkStatusResponse(status=data["status"], phone_number=data.get("phoneNumber"))


@router.post("/unlink")
async def unlink(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unlink WhatsApp and disconnect the session."""
    user_id = str(user.id)

    setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == "whatsapp")
        .first()
    )
    if setting:
        setting.enabled = False
        setting.sync_cursor = None
        setting.updated_at = datetime.now(timezone.utc)
        db.commit()

    # Proxy DELETE to Node service
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.delete(
                f"{settings.WHATSAPP_SERVICE_URL}/sessions/{user_id}",
                headers={"X-Service-Key": settings.WHATSAPP_SERVICE_API_KEY},
            )
        except httpx.RequestError:
            pass  # Best effort — DB state is already cleared

    return {"ok": True}


# ---------------------------------------------------------------------------
# Webhook endpoints (service key auth — called by Node.js service)
# ---------------------------------------------------------------------------

@router.post("/webhook/connected")
def webhook_connected(
    body: WebhookConnectedRequest,
    _: None = Depends(_require_service_key),
    db: Session = Depends(get_db),
):
    """Node.js service notifies that WhatsApp is connected."""
    setting = _get_or_create_setting(db, body.user_id)
    setting.enabled = True
    setting.sync_cursor = json.dumps({
        "phone_number": body.phone_number,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    })
    setting.updated_at = datetime.now(timezone.utc)
    db.commit()

    logger.info("WhatsApp connected for user %s (phone: %s)", body.user_id, body.phone_number)
    return {"ok": True}


@router.post("/webhook/disconnected")
def webhook_disconnected(
    body: WebhookDisconnectedRequest,
    _: None = Depends(_require_service_key),
    db: Session = Depends(get_db),
):
    """Node.js service notifies that WhatsApp was disconnected."""
    setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == body.user_id, UserSourceSetting.source == "whatsapp")
        .first()
    )
    if setting:
        setting.enabled = False
        setting.updated_at = datetime.now(timezone.utc)
        db.commit()

    logger.info("WhatsApp disconnected for user %s (reason: %s)", body.user_id, body.reason)
    return {"ok": True}
