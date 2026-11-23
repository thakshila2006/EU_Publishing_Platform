from datetime import datetime, timezone
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
