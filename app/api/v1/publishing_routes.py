from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.content_ai import ArticleDraft
from app.models.foundation import CountryBlog, User
from app.models.publishing import PublishJob
from app.security.auth import get_current_user, require_roles
from app.services.audit import record_audit_event
from app.services.blogger_publisher import (
    WARMUP_STAGE_CAPS,
    enqueue_and_publish_article,
    get_stage_daily_cap,
    save_theme_snapshot,
)

router = APIRouter(tags=["Phase 3 - Blogger Publishing"])


class PublishRequest(BaseModel):
    target_language: str | None = None


class WarmupStageUpdateRequest(BaseModel):
    warmup_stage: int = Field(..., ge=1, le=3)


@router.post("/publish/articles/{article_id}")
async def publish_article_endpoint(
    article_id: int,
    payload: PublishRequest = PublishRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "EDITOR")),
):
    try:
        return await enqueue_and_publish_article(
            db,
            article_id=article_id,
            actor_username=current_user.username,
            target_language=payload.target_language,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except OverflowError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/publish/jobs")
async def list_publish_jobs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    res = await db.execute(select(PublishJob).order_by(PublishJob.id.desc()))
    return res.scalars().all()


@router.patch("/blogs/{country_code}/warmup-stage")
async def update_blog_warmup_stage(
    country_code: str,
    payload: WarmupStageUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN")),
):
    blog = (
        await db.execute(select(CountryBlog).where(CountryBlog.country_code == country_code.upper()))
    ).scalar_one_or_none()
    if not blog:
        raise HTTPException(status_code=404, detail=f"Country blog '{country_code}' not found.")

    blog.warmup_stage = payload.warmup_stage
    blog.daily_post_cap = get_stage_daily_cap(payload.warmup_stage)
    await db.commit()
    await db.refresh(blog)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=current_user.username,
        action="BLOG_WARMUP_STAGE_UPDATED",
        resource_ref=f"blog:{blog.country_code}",
        details={"warmup_stage": blog.warmup_stage, "daily_post_cap": blog.daily_post_cap},
    )
    return {
        "country_code": blog.country_code,
        "warmup_stage": blog.warmup_stage,
        "daily_post_cap": blog.daily_post_cap,
        "stage_policy": WARMUP_STAGE_CAPS,
    }


@router.get("/blogs/{country_code}/internal-links")
async def get_isolated_internal_links(
    country_code: str,
    language_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Decision 71: Strict country and language silo isolation for internal linking
    res = await db.execute(
        select(PublishJob, ArticleDraft)
        .join(ArticleDraft, PublishJob.article_id == ArticleDraft.id)
        .where(
            PublishJob.country_code == country_code.upper(),
            PublishJob.language_code == language_code,
            PublishJob.status == "PUBLISHED",
        )
        .order_by(PublishJob.id.desc())
        .limit(10)
    )
    links = [
        {
            "article_id": draft.id,
            "title": draft.title,
            "url": job.published_url,
            "country_code": job.country_code,
            "language_code": job.language_code,
        }
        for job, draft in res.all()
    ]
    return {"country_code": country_code.upper(), "language_code": language_code, "links": links}


@router.post("/blogs/{country_code}/xml-theme")
async def generate_blog_xml_theme_endpoint(
    country_code: str,
    brand_name: str = "Veltora",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN")),
):
    try:
        snap = await save_theme_snapshot(
            db, country_code=country_code, actor_username=current_user.username, brand_name=brand_name
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return Response(content=snap.xml_payload, media_type="application/xml")
