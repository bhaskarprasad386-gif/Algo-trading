from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVScannerControl(Base):
    """Persist the singleton IV Scanner runner switch, isolated from other scanners."""

    __tablename__ = "iv_scanner_control"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class IVScannerControlMigration(Base):
    """Durable marker for one-time default-ON rollout migrations."""

    __tablename__ = "iv_scanner_control_migrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    migration_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
