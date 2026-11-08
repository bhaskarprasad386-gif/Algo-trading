from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVConstituentBasketVersion(Base):
    """Immutable, audited IV constituent basket import; never enables a scanner."""

    __tablename__ = "iv_constituent_basket_versions"
    __table_args__ = (
        Index("uq_iv_basket_version_source_hash", "underlying", "source_sha256", unique=True),
        Index("ix_iv_basket_version_underlying_created", "underlying", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    effective_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    weight_sum: Mapped[float] = mapped_column(Float, nullable=False)
    components_json: Mapped[str] = mapped_column(Text, nullable=False)
    token_mapping_json: Mapped[str] = mapped_column(Text, nullable=False)
    roster_source_name: Mapped[str] = mapped_column(String(256), nullable=False)
    roster_source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    roster_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="VALID")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


@event.listens_for(IVConstituentBasketVersion, "before_update")
def _reject_basket_version_update(_mapper, _connection, _target) -> None:
    raise ValueError("IV constituent basket versions are append-only and cannot be updated")


@event.listens_for(IVConstituentBasketVersion, "before_delete")
def _reject_basket_version_delete(_mapper, _connection, _target) -> None:
    raise ValueError("IV constituent basket versions are append-only and cannot be deleted")
