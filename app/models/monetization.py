from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class MonetizationProfile(Base):
    __tablename__ = "monetization_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    adsense_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    affiliate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    adsense_slot_id: Mapped[str] = mapped_column(String(64), default="")
    readiness_status: Mapped[str] = mapped_column(String(32), default="PENDING_COMPLIANCE")
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RevenueLedgerEntry(Base):
    __tablename__ = "revenue_ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)  # ADSENSE | AFFILIATE | SPONSORSHIP
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    gross_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pageviews: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(String(64), default="telemetry_agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
