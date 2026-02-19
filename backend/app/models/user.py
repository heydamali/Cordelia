import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.config import settings

_fernet = Fernet(settings.ENCRYPTION_KEY.encode())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    gmail_history_id: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    gmail_watch_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    def set_refresh_token(self, plain_token: str) -> None:
        self.encrypted_refresh_token = _fernet.encrypt(plain_token.encode()).decode()

    def get_refresh_token(self) -> str | None:
        if self.encrypted_refresh_token is None:
            return None
        return _fernet.decrypt(self.encrypted_refresh_token.encode()).decode()
