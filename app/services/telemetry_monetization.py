from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceEvaluation
from app.models.content_ai import ArticleDraft, GeminiUsageLog
from app.models.foundation import CountryBlog, SystemControl
from app.models.monetization import MonetizationProfile, RevenueLedgerEntry
from app.models.publishing import PublishJob
from app.services.audit import record_audit_event
from app.services.gemini_finops import get_current_month_spend_usd, get_monthly_gemini_cap_usd


async def evaluate_and_update_monetization_readiness(
    db: AsyncSession,
    *,
    country_code: str,
    enable_adsense: bool,
    enable_affiliate: bool,
    adsense_slot_id: str,
    actor_username: str,
) -> dict:
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    if ctrl and ctrl.global_kill_switch:
        raise RuntimeError("System halted by global emergency kill switch.")

    code = country_code.upper()
    blog = (await db.execute(select(CountryBlog).where(CountryBlog.country_code == code))).scalar_one_or_none()
    if not blog:
        raise LookupError(f"Country blog {code} not found.")

    published_count = int(
        (
            await db.execute(
                select(func.count(PublishJob.id)).where(
                    PublishJob.country_code == code,
                    PublishJob.status == "PUBLISHED",
                )
            )
        ).scalar_one()
        or 0
    )

    passed_compliance_count = int(
        (
            await db.execute(
                select(func.count(ComplianceEvaluation.id)).where(
                    ComplianceEvaluation.country_code == code,
                    ComplianceEvaluation.overall_status == "PASSED",
                )
            )
        ).scalar_one()
        or 0
    )

    blocked_compliance_count = int(
        (
            await db.execute(
                select(func.count(ComplianceEvaluation.id)).where(
                    ComplianceEvaluation.country_code == code,
                    ComplianceEvaluation.overall_status == "BLOCKED",
                )
            )
        ).scalar_one()
        or 0
    )

    eligible = published_count >= 1 and passed_compliance_count >= 1
    if (enable_adsense or enable_affiliate) and not eligible:
        raise PermissionError(
            f"Monetization gate block for {code}: requires at least 1 published post and 1 PASSED EU compliance evaluation."
        )

    profile = (
        await db.execute(select(MonetizationProfile).where(MonetizationProfile.country_code == code))
    ).scalar_one_or_none()
    if not profile:
        profile = MonetizationProfile(country_code=code)
        db.add(profile)

    profile.adsense_enabled = enable_adsense
    profile.affiliate_enabled = enable_affiliate
    profile.adsense_slot_id = adsense_slot_id
    profile.readiness_status = "ACTIVE" if (enable_adsense or enable_affiliate) else ("ELIGIBLE" if eligible else "PENDING_COMPLIANCE")
    profile.updated_by = actor_username
    await db.commit()
    await db.refresh(profile)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=actor_username,
        action="MONETIZATION_PROFILE_UPDATED",
        resource_ref=f"blog:{code}:monetization",
        details={
            "adsense_enabled": enable_adsense,
            "affiliate_enabled": enable_affiliate,
            "readiness_status": profile.readiness_status,
            "published_count": published_count,
            "passed_compliance_count": passed_compliance_count,
        },
    )

    return {
        "country_code": code,
        "adsense_enabled": profile.adsense_enabled,
        "affiliate_enabled": profile.affiliate_enabled,
        "adsense_slot_id": profile.adsense_slot_id,
        "readiness_status": profile.readiness_status,
        "metrics": {
            "published_posts": published_count,
            "passed_compliance_checks": passed_compliance_count,
            "blocked_compliance_checks": blocked_compliance_count,
        },
    }


async def record_revenue_entry(
    db: AsyncSession,
    *,
    country_code: str,
    channel: str,
    gross_amount_usd: float,
    pageviews: int,
    clicks: int,
    notes: str,
    actor_username: str,
) -> RevenueLedgerEntry:
    code = country_code.upper()
    blog = (await db.execute(select(CountryBlog).where(CountryBlog.country_code == code))).scalar_one_or_none()
    if not blog:
        raise LookupError(f"Country blog {code} not found.")

    profile = (
        await db.execute(select(MonetizationProfile).where(MonetizationProfile.country_code == code))
    ).scalar_one_or_none()
    if not profile or not (profile.adsense_enabled or profile.affiliate_enabled):
        raise PermissionError(f"Monetization is not active for country blog {code}.")

    entry = RevenueLedgerEntry(
        country_code=code,
        channel=channel.upper(),
        currency="USD",
        gross_amount_usd=round(gross_amount_usd, 4),
        pageviews=pageviews,
        clicks=clicks,
        notes=notes,
        recorded_by=actor_username,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=actor_username,
        action="REVENUE_LEDGER_RECORDED",
        resource_ref=f"blog:{code}:revenue:{entry.id}",
        details={"channel": entry.channel, "gross_amount_usd": entry.gross_amount_usd, "pageviews": pageviews},
    )
    return entry

async def build_telemetry_overview(
    db: AsyncSession,
    *,
    country_code: str | None = None,
) -> dict:
    normalized_country = country_code.strip().upper() if country_code else None

    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    total_drafts = int((await db.execute(select(func.count(ArticleDraft.id)))).scalar_one() or 0)

    published_stmt = select(func.count(PublishJob.id)).where(PublishJob.status == "PUBLISHED")
    if normalized_country:
        published_stmt = published_stmt.where(PublishJob.country_code == normalized_country)
    total_published = int((await db.execute(published_stmt)).scalar_one() or 0)

    total_evals = int((await db.execute(select(func.count(ComplianceEvaluation.id)))).scalar_one() or 0)
    passed_evals = int(
        (
            await db.execute(
                select(func.count(ComplianceEvaluation.id)).where(ComplianceEvaluation.overall_status == "PASSED")
            )
        ).scalar_one()
        or 0
    )
    compliance_pass_rate = round((passed_evals / total_evals * 100.0), 2) if total_evals > 0 else 100.0

    ledger_filters = []
    if normalized_country:
        ledger_filters.append(RevenueLedgerEntry.country_code == normalized_country)

    totals_stmt = select(
        func.coalesce(func.sum(RevenueLedgerEntry.gross_amount_usd), 0.0),
        func.coalesce(func.sum(RevenueLedgerEntry.pageviews), 0),
        func.coalesce(func.sum(RevenueLedgerEntry.clicks), 0),
    )
    if ledger_filters:
        totals_stmt = totals_stmt.where(*ledger_filters)

    raw_revenue, raw_pageviews, raw_clicks = (await db.execute(totals_stmt)).one()
    total_revenue_usd = float(raw_revenue or 0.0)
    total_pageviews = int(raw_pageviews or 0)
    total_clicks = int(raw_clicks or 0)

    gemini_spend_usd = await get_current_month_spend_usd(db)
    gemini_cap_usd = await get_monthly_gemini_cap_usd(db)
    net_margin_usd = round(total_revenue_usd - gemini_spend_usd, 4)
    rpm_usd = round((total_revenue_usd / total_pageviews * 1000.0), 4) if total_pageviews > 0 else 0.0
    ctr_pct = round((total_clicks / total_pageviews * 100.0), 2) if total_pageviews > 0 else 0.0
    utilization_pct = round((gemini_spend_usd / gemini_cap_usd * 100.0), 2) if gemini_cap_usd > 0 else 0.0
    remaining_cap_usd = round(max(gemini_cap_usd - gemini_spend_usd, 0.0), 4)

    active_stmt = select(func.count(MonetizationProfile.id)).where(MonetizationProfile.readiness_status == "ACTIVE")
    if normalized_country:
        active_stmt = active_stmt.where(MonetizationProfile.country_code == normalized_country)
    active_monetized_blogs = int((await db.execute(active_stmt)).scalar_one() or 0)

    country_stmt = (
        select(
            RevenueLedgerEntry.country_code,
            func.coalesce(func.sum(RevenueLedgerEntry.gross_amount_usd), 0.0).label("gross_revenue_usd"),
            func.coalesce(func.sum(RevenueLedgerEntry.pageviews), 0).label("pageviews"),
            func.coalesce(func.sum(RevenueLedgerEntry.clicks), 0).label("clicks"),
        )
        .group_by(RevenueLedgerEntry.country_code)
        .order_by(func.sum(RevenueLedgerEntry.gross_amount_usd).desc())
    )
    if ledger_filters:
        country_stmt = country_stmt.where(*ledger_filters)

    country_rows = (await db.execute(country_stmt)).all()
    by_country = []
    for row in country_rows:
        c_rev = float(row.gross_revenue_usd or 0.0)
        c_pv = int(row.pageviews or 0)
        c_clk = int(row.clicks or 0)
        by_country.append(
            {
                "country_code": row.country_code,
                "gross_revenue_usd": round(c_rev, 4),
                "pageviews": c_pv,
                "clicks": c_clk,
                "ctr_pct": round((c_clk / c_pv * 100.0), 2) if c_pv > 0 else 0.0,
                "effective_rpm_usd": round((c_rev / c_pv * 1000.0), 4) if c_pv > 0 else 0.0,
            }
        )

    return {
        "system_halted": bool(ctrl.global_kill_switch) if ctrl else False,
        "country_filter": normalized_country,
        "content_pipeline": {
            "total_drafts": total_drafts,
            "total_published": total_published,
            "compliance_evaluations": total_evals,
            "compliance_pass_rate_pct": compliance_pass_rate,
        },
        "monetization": {
            "active_monetized_blogs": active_monetized_blogs,
            "gross_revenue_usd": round(total_revenue_usd, 4),
            "total_pageviews": total_pageviews,
            "total_clicks": total_clicks,
            "ctr_pct": ctr_pct,
            "effective_rpm_usd": rpm_usd,
            "by_country": by_country,
        },
        "finops": {
            "gemini_monthly_spend_usd": round(gemini_spend_usd, 6),
            "gemini_monthly_cap_usd": round(gemini_cap_usd, 2),
            "remaining_cap_usd": remaining_cap_usd,
            "utilization_pct": utilization_pct,
            "alert_80pct_active": utilization_pct >= 80.0,
            "circuit_breaker_tripped": gemini_spend_usd >= gemini_cap_usd if gemini_cap_usd > 0 else False,
            "net_margin_usd": net_margin_usd,
        },
    }
