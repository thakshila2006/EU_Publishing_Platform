from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class ArticleDraft(Base):
    __tablename__ = "article_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    language_code: Mapped[str] = mapped_column(String(16), index=True)
    niche: Mapped[str] = mapped_column(String(64), index=True)
    is_ymyl: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(255))
    body_markdown: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    disclosure_text: Mapped[str] = mapped_column(Text, default="")
    ai_provider: Mapped[str] = mapped_column(String(32), default="google_gemini")
    model_tier: Mapped[str] = mapped_column(String(32), default="standard")
    workflow_state: Mapped[str] = mapped_column(String(48), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64), default="writing_agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ArticleLocalization(Base):
    __tablename__ = "article_localizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article_drafts.id"), index=True)
    target_language: Mapped[str] = mapped_column(String(16), index=True)
    localized_title: Mapped[str] = mapped_column(String(255))
    localized_body: Mapped[str] = mapped_column(Text)
    locale_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class YmylReview(Base):
    __tablename__ = "ymyl_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article_drafts.id"), unique=True, index=True)
    niche: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32), default="PENDING")
    reviewer_username: Mapped[str] = mapped_column(String(64), default="")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeminiUsageLog(Base):
    __tablename__ = "gemini_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="google_gemini")
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    model_tier: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    circuit_breaker_tripped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
