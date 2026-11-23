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


async def build_telemetry_overview(db: AsyncSession) -> dict:
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    total_drafts = int((await db.execute(select(func.count(ArticleDraft.id)))).scalar_one() or 0)
    total_published = int(
        (await db.execute(select(func.count(PublishJob.id)).where(PublishJob.status == "PUBLISHED"))).scalar_one() or 0
    )
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

    total_revenue_usd = float(
        (await db.execute(select(func.coalesce(func.sum(RevenueLedgerEntry.gross_amount_usd), 0.0)))).scalar_one() or 0.0
    )
    total_pageviews = int(
        (await db.execute(select(func.coalesce(func.sum(RevenueLedgerEntry.pageviews), 0)))).scalar_one() or 0
    )
    gemini_spend_usd = await get_current_month_spend_usd(db)
    gemini_cap_usd = await get_monthly_gemini_cap_usd(db)
    net_margin_usd = round(total_revenue_usd - gemini_spend_usd, 4)
    rpm_usd = round((total_revenue_usd / total_pageviews * 1000.0), 4) if total_pageviews > 0 else 0.0

    active_monetized_blogs = int(
        (
            await db.execute(
                select(func.count(MonetizationProfile.id)).where(MonetizationProfile.readiness_status == "ACTIVE")
            )
        ).scalar_one()
        or 0
    )

    return {
        "system_halted": bool(ctrl.global_kill_switch) if ctrl else False,
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
            "effective_rpm_usd": rpm_usd,
        },
        "finops": {
            "gemini_monthly_spend_usd": round(gemini_spend_usd, 6),
            "gemini_monthly_cap_usd": round(gemini_cap_usd, 2),
            "net_margin_usd": net_margin_usd,
        },
    }
