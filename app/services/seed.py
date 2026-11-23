import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.foundation import AuditEvent, ConfigRegistry, CountryBlog, SystemControl, User
from app.security.auth import get_password_hash
from app.services.audit import record_audit_event

SEVEN_COUNTRIES = [
    ("UK", "United Kingdom", "Europe/London", "en-GB", 1),
    ("DE", "Germany", "Europe/Berlin", "de-DE,en", 2),
    ("NL", "Netherlands", "Europe/Amsterdam", "nl-NL,en", 2),
    ("SE", "Sweden", "Europe/Stockholm", "sv-SE,en", 3),
    ("NO", "Norway", "Europe/Oslo", "nb-NO,en", 3),
    ("DK", "Denmark", "Europe/Copenhagen", "da-DK,en", 3),
    ("CH", "Switzerland", "Europe/Zurich", "de-CH,fr-CH,it-CH,en", 4),
]

INITIAL_CONFIGS = [
    ("ai.provider", "google_gemini", "AI_GOVERNANCE", "APPROVED_DECISION_07"),
    ("ai.gemini_monthly_cap_usd", "30.00", "FINOPS", "APPROVED_BASELINE"),
    ("ai.gemini_alert_threshold_pct", "80", "FINOPS", "APPROVED_BASELINE"),
    ("ymyl.mandatory_human_gate", "true", "COMPLIANCE", "APPROVED_DECISION_72"),
    ("ymyl.niches", json.dumps(["Health & Wellness", "Finance"]), "COMPLIANCE", "APPROVED_DECISION_02"),
    ("brand.shortlist", json.dumps(["Veltora", "Veridian", "Elvion"]), "BRANDING", "PENDING_USER_SELECTION"),
    ("brand.domain_strategy", "HYBRID_CCTLD_AND_PREFIXED_COM", "BRANDING", "APPROVED_BASELINE"),
    ("design.primary_color", "#0F2A3C", "VISUAL_IDENTITY", "APPROVED_BASELINE"),
    ("design.accent_color", "#0D7A6E", "VISUAL_IDENTITY", "APPROVED_BASELINE"),
]


async def seed_initial_data(db: AsyncSession):
    ctrl_res = await db.execute(select(SystemControl).limit(1))
    if not ctrl_res.scalar_one_or_none():
        db.add(SystemControl(global_kill_switch=False, kill_switch_reason="Nominal operation", updated_by="SYSTEM"))

    user_res = await db.execute(select(User).where(User.username == "owner"))
    if not user_res.scalar_one_or_none():
        db.add(
            User(
                username="owner",
                email="owner@localhost.local",
                hashed_password=get_password_hash("ChangeMeOwner@2026!"),
                role="OWNER",
                mfa_enabled=False,
                is_active=True,
            )
        )

    for code, name, tz, langs, wave in SEVEN_COUNTRIES:
        exists = await db.execute(select(CountryBlog).where(CountryBlog.country_code == code))
        if not exists.scalar_one_or_none():
            db.add(
                CountryBlog(
                    country_code=code,
                    country_name=name,
                    iana_timezone=tz,
                    languages_csv=langs,
                    rollout_wave=wave,
                    warmup_stage=1,
                    daily_post_cap=2,
                )
            )

    for key, val, cat, st in INITIAL_CONFIGS:
        c_exists = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == key))
        if not c_exists.scalar_one_or_none():
            db.add(ConfigRegistry(config_key=key, config_value=val, category=cat, status=st))

    await db.commit()

    audit_res = await db.execute(select(AuditEvent).limit(1))
    if not audit_res.scalar_one_or_none():
        await record_audit_event(
            db,
            actor_type="SYSTEM",
            actor_id="BOOTSTRAP",
            action="PHASE1_FOUNDATION_INITIALIZED",
            resource_ref="system:core",
            details={"countries": 7, "ai_provider": "google_gemini"},
        )
