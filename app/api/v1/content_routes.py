import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.foundation import CountryBlog, SystemControl, User
from app.models.content_ai import ArticleDraft, ArticleLocalization, GeminiUsageLog, YmylReview
from app.security.auth import get_current_user, require_roles
from app.services.audit import record_audit_event
from app.services.gemini_finops import (
    get_current_month_spend_usd,
    get_monthly_gemini_cap_usd,
    is_ymyl_niche,
    record_gemini_usage,
)

router = APIRouter(tags=["Phase 2 - AI and Content"])


class DraftCreateRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=8)
    language_code: str = Field(..., min_length=2, max_length=16)
    niche: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=3, max_length=255)
    body_markdown: str = Field(..., min_length=10)
    citations: list[str] = Field(default_factory=list)
    disclosure_text: str = "AI-assisted editorial draft; informational purposes only."
    model_tier: str = "standard"
    ai_provider: str = "google_gemini"
    input_tokens: int = Field(default=500, ge=0)
    output_tokens: int = Field(default=800, ge=0)
    simulated_cost_usd: float | None = Field(default=None, ge=0.0)


class LocalizationCreateRequest(BaseModel):
    target_language: str = Field(..., min_length=2, max_length=16)
    localized_title: str = Field(..., min_length=3, max_length=255)
    localized_body: str = Field(..., min_length=10)
    locale_notes: str = ""


class YmylDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    review_notes: str = ""


@router.post("/articles/drafts")
async def create_article_draft(
    payload: DraftCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "EDITOR")),
):
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    if ctrl and ctrl.global_kill_switch:
        raise HTTPException(status_code=503, detail="System halted by global emergency kill switch.")

    blog_res = await db.execute(select(CountryBlog).where(CountryBlog.country_code == payload.country_code.upper()))
    blog = blog_res.scalar_one_or_none()
    if not blog:
        raise HTTPException(status_code=404, detail=f"Country blog {payload.country_code} not found.")

    try:
        finops_info = await record_gemini_usage(
            db,
            provider=payload.ai_provider,
            agent_name="writing_agent",
            model_tier=payload.model_tier,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            simulated_cost_usd=payload.simulated_cost_usd,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    ymyl_flag = is_ymyl_niche(payload.niche)
    initial_state = "AWAITING_YMYL_HUMAN_APPROVAL" if ymyl_flag else "READY_FOR_QA"

    draft = ArticleDraft(
        country_code=blog.country_code,
        language_code=payload.language_code,
        niche=payload.niche,
        is_ymyl=ymyl_flag,
        title=payload.title,
        body_markdown=payload.body_markdown,
        citations_json=json.dumps(payload.citations, ensure_ascii=False),
        disclosure_text=payload.disclosure_text,
        ai_provider="google_gemini",
        model_tier=payload.model_tier,
        workflow_state=initial_state,
        created_by=current_user.username,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    if ymyl_flag:
        review = YmylReview(
            article_id=draft.id,
            niche=payload.niche,
            decision="PENDING",
        )
        db.add(review)
        await db.commit()

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=current_user.username,
        action="ARTICLE_DRAFT_CREATED",
        resource_ref=f"article:{draft.id}",
        details={"country": draft.country_code, "niche": draft.niche, "is_ymyl": ymyl_flag, "state": initial_state},
    )

    return {
        "id": draft.id,
        "country_code": draft.country_code,
        "language_code": draft.language_code,
        "niche": draft.niche,
        "is_ymyl": draft.is_ymyl,
        "workflow_state": draft.workflow_state,
        "finops": finops_info,
    }


@router.get("/articles/drafts")
async def list_article_drafts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    res = await db.execute(select(ArticleDraft).order_by(ArticleDraft.id.desc()))
    return res.scalars().all()


@router.post("/articles/{article_id}/localizations")
async def add_article_localization(
    article_id: int,
    payload: LocalizationCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "EDITOR")),
):
    draft = (await db.execute(select(ArticleDraft).where(ArticleDraft.id == article_id))).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Article draft not found.")

    loc = ArticleLocalization(
        article_id=article_id,
        target_language=payload.target_language,
        localized_title=payload.localized_title,
        localized_body=payload.localized_body,
        locale_notes=payload.locale_notes,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=current_user.username,
        action="ARTICLE_LOCALIZATION_ADDED",
        resource_ref=f"article:{article_id}:loc:{loc.id}",
        details={"target_language": payload.target_language},
    )
    return {"id": loc.id, "article_id": article_id, "target_language": loc.target_language}


@router.post("/articles/{article_id}/ymyl-review")
async def review_ymyl_article(
    article_id: int,
    payload: YmylDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "YMYL_EXPERT_REVIEWER")),
):
    draft = (await db.execute(select(ArticleDraft).where(ArticleDraft.id == article_id))).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Article draft not found.")
    if not draft.is_ymyl:
        raise HTTPException(status_code=400, detail="Article is not flagged as YMYL.")

    review = (await db.execute(select(YmylReview).where(YmylReview.article_id == article_id))).scalar_one_or_none()
    if not review:
        review = YmylReview(article_id=article_id, niche=draft.niche)
        db.add(review)

    review.decision = payload.decision
    review.reviewer_username = current_user.username
    review.review_notes = payload.review_notes
    review.reviewed_at = datetime.now(timezone.utc)

    draft.workflow_state = "APPROVED_FOR_SCHEDULE" if payload.decision == "APPROVED" else "YMYL_REJECTED"
    draft.version += 1
    await db.commit()

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=current_user.username,
        action=f"YMYL_REVIEW_{payload.decision}",
        resource_ref=f"article:{article_id}",
        details={"niche": draft.niche, "reviewer": current_user.username, "notes": payload.review_notes},
    )

    return {
        "article_id": article_id,
        "decision": review.decision,
        "reviewer": review.reviewer_username,
        "workflow_state": draft.workflow_state,
    }


@router.get("/finops/gemini-status")
async def get_gemini_finops_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "ANALYST")),
):
    spend = await get_current_month_spend_usd(db)
    cap = await get_monthly_gemini_cap_usd(db)
    utilization = (spend / cap * 100.0) if cap > 0 else 100.0
    tripped_count_res = await db.execute(
        select(GeminiUsageLog).where(GeminiUsageLog.circuit_breaker_tripped.is_(True))
    )
    tripped_logs = tripped_count_res.scalars().all()
    return {
        "provider": "google_gemini",
        "monthly_spend_usd": round(spend, 6),
        "monthly_cap_usd": round(cap, 2),
        "utilization_pct": round(utilization, 2),
        "alert_80pct_active": utilization >= 80.0,
        "circuit_breaker_tripped": len(tripped_logs) > 0 or spend >= cap,
    }
