from pathlib import Path

BASE_DIR = Path(".")

DIRS = [
    "app/models",
    "app/schemas",
    "app/services",
    "app/api/v1",
    "tests",
]

for d in DIRS:
    (BASE_DIR / d).mkdir(parents=True, exist_ok=True)

FILES = {}

FILES["app/models/content_ai.py"] = """from datetime import datetime, timezone
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
"""

FILES["app/services/gemini_finops.py"] = """from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.foundation import ConfigRegistry, SystemControl
from app.models.content_ai import GeminiUsageLog
from app.services.audit import record_audit_event

YMYL_NICHES = {"Health & Wellness", "Finance"}

TIER_RATES_PER_1K_TOKENS = {
    "fast": {"input": 0.00015, "cached": 0.00004, "output": 0.00060},
    "standard": {"input": 0.00035, "cached": 0.00009, "output": 0.00105},
    "pro": {"input": 0.00125, "cached": 0.00031, "output": 0.00375},
}


def is_ymyl_niche(niche: str) -> bool:
    return niche.strip() in YMYL_NICHES


async def get_monthly_gemini_cap_usd(db: AsyncSession) -> float:
    res = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == "ai.gemini_monthly_cap_usd"))
    cfg = res.scalar_one_or_none()
    if cfg:
        try:
            return float(cfg.config_value)
        except ValueError:
            pass
    return float(settings.GEMINI_MONTHLY_BUDGET_USD)


async def get_current_month_spend_usd(db: AsyncSession) -> float:
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    res = await db.execute(
        select(func.coalesce(func.sum(GeminiUsageLog.estimated_cost_usd), 0.0)).where(
            GeminiUsageLog.created_at >= month_start
        )
    )
    return float(res.scalar_one() or 0.0)


def estimate_gemini_cost_usd(
    model_tier: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> float:
    tier_key = model_tier.lower() if model_tier.lower() in TIER_RATES_PER_1K_TOKENS else "standard"
    rates = TIER_RATES_PER_1K_TOKENS[tier_key]
    billable_input = max(0, input_tokens - cached_tokens)
    cost = (
        (billable_input / 1000.0) * rates["input"]
        + (cached_tokens / 1000.0) * rates["cached"]
        + (output_tokens / 1000.0) * rates["output"]
    )
    return round(cost, 6)


async def record_gemini_usage(
    db: AsyncSession,
    *,
    provider: str,
    agent_name: str,
    model_tier: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    simulated_cost_usd: float | None = None,
) -> dict:
    if provider != "google_gemini":
        await record_audit_event(
            db,
            actor_type="SYSTEM",
            actor_id=agent_name,
            action="BLOCKED_NON_GEMINI_PROVIDER",
            resource_ref="ai:provider_policy",
            details={"attempted_provider": provider, "allowed": "google_gemini"},
        )
        raise PermissionError("Policy violation: only google_gemini is permitted as AI provider.")

    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    if ctrl and ctrl.global_kill_switch:
        raise RuntimeError("System halted by global emergency kill switch.")

    monthly_cap = await get_monthly_gemini_cap_usd(db)
    current_spend = await get_current_month_spend_usd(db)
    call_cost = (
        float(simulated_cost_usd)
        if simulated_cost_usd is not None
        else estimate_gemini_cost_usd(model_tier, input_tokens, output_tokens, cached_tokens)
    )

    projected_spend = round(current_spend + call_cost, 6)
    tripped = projected_spend > monthly_cap

    usage = GeminiUsageLog(
        provider="google_gemini",
        agent_name=agent_name,
        model_tier=model_tier,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=0.0 if tripped else call_cost,
        circuit_breaker_tripped=tripped,
    )
    db.add(usage)
    await db.commit()
    await db.refresh(usage)

    utilization_pct = (projected_spend / monthly_cap * 100.0) if monthly_cap > 0 else 100.0

    if tripped:
        await record_audit_event(
            db,
            actor_type="SYSTEM",
            actor_id="FINOPS_CIRCUIT_BREAKER",
            action="GEMINI_BUDGET_CIRCUIT_BREAKER_TRIPPED",
            resource_ref="finops:gemini_monthly_cap",
            details={"monthly_cap_usd": monthly_cap, "attempted_spend_usd": projected_spend},
        )
        raise RuntimeError(
            f"Gemini FinOps circuit breaker tripped: spend {projected_spend:.4f} exceeds monthly cap {monthly_cap:.2f}."
        )

    alert_triggered = utilization_pct >= 80.0
    return {
        "usage_id": usage.id,
        "provider": "google_gemini",
        "model_tier": model_tier,
        "cost_usd": call_cost,
        "monthly_spend_usd": projected_spend,
        "monthly_cap_usd": monthly_cap,
        "utilization_pct": round(utilization_pct, 2),
        "budget_alert_80pct": alert_triggered,
        "circuit_breaker_tripped": False,
    }
"""

FILES["app/api/v1/content_routes.py"] = """import json
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
"""

FILES["app/main.py"] = """from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.content_routes import router as content_router
from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, Base, engine, get_db
from app.models import content_ai as _content_ai_models  # noqa: F401
from app.models.foundation import CountryBlog, SystemControl
from app.services.seed import seed_initial_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
app.include_router(v1_router, prefix="/api/v1")
app.include_router(content_router, prefix="/api/v1")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    blogs = (await db.execute(select(CountryBlog).order_by(CountryBlog.rollout_wave, CountryBlog.country_code))).scalars().all()
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "blogs": blogs,
            "kill_switch": ctrl.global_kill_switch if ctrl else False,
        },
    )
"""

FILES["tests/test_phase2.py"] = """from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.foundation import ConfigRegistry, User
from app.security.auth import get_password_hash


def _get_owner_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "owner", "password": "ChangeMeOwner@2026!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _ensure_ymyl_reviewer_and_cap(cap_usd: float = 30.0):
    async with AsyncSessionLocal() as db:
        u_res = await db.execute(select(User).where(User.username == "health_reviewer"))
        if not u_res.scalar_one_or_none():
            db.add(
                User(
                    username="health_reviewer",
                    email="health_reviewer@localhost.local",
                    hashed_password=get_password_hash("ReviewerPass@2026!"),
                    role="YMYL_EXPERT_REVIEWER",
                    mfa_enabled=False,
                    is_active=True,
                )
            )
        c_res = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == "ai.gemini_monthly_cap_usd"))
        cfg = c_res.scalar_one_or_none()
        if cfg:
            cfg.config_value = f"{cap_usd:.2f}"
        await db.commit()


def test_gemini_only_policy_and_ymyl_gate():
    with TestClient(app) as client:
        client.portal.call(_ensure_ymyl_reviewer_and_cap, 30.0)
        headers = _get_owner_headers(client)

        bad_provider = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "UK",
                "language_code": "en-GB",
                "niche": "Technology",
                "title": "EU Cloud Trends",
                "body_markdown": "Detailed analysis of European cloud adoption and governance.",
                "ai_provider": "openai",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        )
        assert bad_provider.status_code == 403

        ymyl_draft = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "DE",
                "language_code": "de-DE",
                "niche": "Health & Wellness",
                "title": "Schlafhygiene im Alltag",
                "body_markdown": "Evidenzbasierte Hinweise zu Schlafroutinen und Erholung.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.02,
            },
            headers=headers,
        )
        assert ymyl_draft.status_code == 200
        data = ymyl_draft.json()
        assert data["is_ymyl"] is True
        assert data["workflow_state"] == "AWAITING_YMYL_HUMAN_APPROVAL"
        article_id = data["id"]

        loc = client.post(
            f"/api/v1/articles/{article_id}/localizations",
            json={
                "target_language": "en",
                "localized_title": "Daily Sleep Hygiene",
                "localized_body": "Evidence-informed guidance on healthy sleep routines.",
            },
            headers=headers,
        )
        assert loc.status_code == 200

        rev_login = client.post(
            "/api/v1/auth/token",
            data={"username": "health_reviewer", "password": "ReviewerPass@2026!"},
        )
        assert rev_login.status_code == 200
        rev_headers = {"Authorization": f"Bearer {rev_login.json()['access_token']}"}

        approved = client.post(
            f"/api/v1/articles/{article_id}/ymyl-review",
            json={"decision": "APPROVED", "review_notes": "Verified clinical sources and disclaimers."},
            headers=rev_headers,
        )
        assert approved.status_code == 200
        assert approved.json()["workflow_state"] == "APPROVED_FOR_SCHEDULE"


def test_finops_circuit_breaker_trips_on_overage():
    with TestClient(app) as client:
        client.portal.call(_ensure_ymyl_reviewer_and_cap, 0.05)
        headers = _get_owner_headers(client)

        tripped = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "UK",
                "language_code": "en-GB",
                "niche": "Finance",
                "title": "Budget Cap Test",
                "body_markdown": "Testing FinOps hard cap enforcement above monthly allowance.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.50,
            },
            headers=headers,
        )
        assert tripped.status_code == 429

        status_res = client.get("/api/v1/finops/gemini-status", headers=headers)
        assert status_res.status_code == 200
        assert status_res.json()["circuit_breaker_tripped"] is True
