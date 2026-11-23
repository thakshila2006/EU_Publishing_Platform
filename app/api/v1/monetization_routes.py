from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.foundation import User
from app.models.monetization import RevenueLedgerEntry
from app.security.auth import get_current_user, require_roles
from app.services.telemetry_monetization import (
    build_telemetry_overview,
    evaluate_and_update_monetization_readiness,
    record_revenue_entry,
)

router = APIRouter(tags=["Phase 5 - Monetization & Telemetry"])


class MonetizationConfigRequest(BaseModel):
    enable_adsense: bool = True
    enable_affiliate: bool = False
    adsense_slot_id: str = ""


class RevenueEntryRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=8)
    channel: str = Field(..., pattern="^(ADSENSE|AFFILIATE|SPONSORSHIP)$")
    gross_amount_usd: float = Field(..., ge=0.0)
    pageviews: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    notes: str = ""


@router.put("/monetization/blogs/{country_code}")
async def configure_blog_monetization(
    country_code: str,
    payload: MonetizationConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN")),
):
    try:
        return await evaluate_and_update_monetization_readiness(
            db,
            country_code=country_code,
            enable_adsense=payload.enable_adsense,
            enable_affiliate=payload.enable_affiliate,
            adsense_slot_id=payload.adsense_slot_id,
            actor_username=current_user.username,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/monetization/ledger")
async def add_revenue_ledger_entry(
    payload: RevenueEntryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "ANALYST")),
):
    try:
        entry = await record_revenue_entry(
            db,
            country_code=payload.country_code,
            channel=payload.channel,
            gross_amount_usd=payload.gross_amount_usd,
            pageviews=payload.pageviews,
            clicks=payload.clicks,
            notes=payload.notes,
            actor_username=current_user.username,
        )
        return {
            "id": entry.id,
            "country_code": entry.country_code,
            "channel": entry.channel,
            "gross_amount_usd": entry.gross_amount_usd,
            "pageviews": entry.pageviews,
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/monetization/ledger")
async def list_revenue_ledger(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    res = await db.execute(select(RevenueLedgerEntry).order_by(RevenueLedgerEntry.id.desc()).limit(limit))
    return res.scalars().all()


@router.get("/telemetry/overview")
async def get_telemetry_overview(
    country_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await build_telemetry_overview(db, country_code=country_code)
