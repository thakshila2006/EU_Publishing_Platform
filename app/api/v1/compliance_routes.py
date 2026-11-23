from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.compliance import ComplianceEvaluation
from app.models.foundation import User
from app.security.auth import get_current_user, require_roles
from app.services.compliance_qa import evaluate_article_compliance

router = APIRouter(tags=["Phase 4 - SEO, QA & EU Compliance"])


@router.post("/articles/{article_id}/compliance-check")
async def run_compliance_check(
    article_id: int,
    promote_if_passed: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "EDITOR", "YMYL_EXPERT_REVIEWER")),
):
    try:
        return await evaluate_article_compliance(
            db,
            article_id=article_id,
            actor_username=current_user.username,
            promote_if_passed=promote_if_passed,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/compliance/reports")
async def list_compliance_reports(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(ComplianceEvaluation).order_by(ComplianceEvaluation.id.desc()).limit(limit)
    )
    return res.scalars().all()
