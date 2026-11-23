from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article_drafts.id"), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    language_code: Mapped[str] = mapped_column(String(16), index=True)
    scheduled_local_date: Mapped[str] = mapped_column(String(16), index=True)
    scheduled_utc_iso: Mapped[str] = mapped_column(String(48))
    jitter_minutes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    blogger_post_id: Mapped[str] = mapped_column(String(128), default="")
    published_url: Mapped[str] = mapped_column(String(255), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="publishing_agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BloggerThemeSnapshot(Base):
    __tablename__ = "blogger_theme_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    theme_version: Mapped[str] = mapped_column(String(32), default="v1.0-eu-compliant")
    xml_payload: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
