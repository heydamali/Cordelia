from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting

router = APIRouter(prefix="/sources", tags=["sources"])

SOURCE_REGISTRY: dict[str, dict[str, str]] = {
    "gmail": {"display_name": "Gmail", "icon": "mail"},
    "google_calendar": {"display_name": "Google Calendar", "icon": "calendar"},
}


class SourceSettingOut(BaseModel):
    source: str
    enabled: bool
    display_name: str
    icon: str


class SourceToggleIn(BaseModel):
    enabled: bool


@router.get("", response_model=list[SourceSettingOut])
def list_sources(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return all source settings for a user, with display metadata."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    settings = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id)
        .all()
    )
    settings_by_source = {s.source: s for s in settings}

    result: list[SourceSettingOut] = []
    for source_key, meta in SOURCE_REGISTRY.items():
        setting = settings_by_source.get(source_key)
        result.append(SourceSettingOut(
            source=source_key,
            enabled=setting.enabled if setting else False,
            display_name=meta["display_name"],
            icon=meta["icon"],
        ))
    return result


@router.patch("/{source}", response_model=SourceSettingOut)
def toggle_source(
    source: str,
    body: SourceToggleIn,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Enable or disable a source for a user."""
    if source not in SOURCE_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == source)
        .first()
    )
    if setting is None:
        setting = UserSourceSetting(user_id=user_id, source=source, enabled=body.enabled)
        db.add(setting)
    else:
        setting.enabled = body.enabled
        setting.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(setting)

    meta = SOURCE_REGISTRY[source]
    return SourceSettingOut(
        source=setting.source,
        enabled=setting.enabled,
        display_name=meta["display_name"],
        icon=meta["icon"],
    )
