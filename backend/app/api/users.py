from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


class PushTokenUpdateSchema(BaseModel):
    user_id: str
    push_token: str


@router.post("/push-token", status_code=200)
def register_push_token(body: PushTokenUpdateSchema, db: Session = Depends(get_db)):
    """Register or update a device push token for APNs notifications."""
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.push_token = body.push_token
    db.commit()
    return {"status": "ok", "user_id": user.id}
