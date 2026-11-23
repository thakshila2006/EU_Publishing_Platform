from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class ComplianceEvaluation(Base):
    __tablename__ = "compliance_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article_drafts.id"), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    language_code: Mapped[str] = mapped_column(String(16), index=True)
    seo_score: Mapped[int] = mapped_column(Integer, default=0)
    qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    gdpr_dsa_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    ymyl_gate_satisfied: Mapped[bool] = mapped_column(Boolean, default=False)
    overall_status: Mapped[str] = mapped_column(String(32), default="BLOCKED")  # PASSED | BLOCKED
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    evaluated_by: Mapped[str] = mapped_column(String(64), default="compliance_engine")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
