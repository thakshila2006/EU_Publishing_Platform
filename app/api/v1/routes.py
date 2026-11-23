from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.foundation import AuditEvent, ConfigRegistry, CountryBlog, SystemControl, User
from app.security.auth import create_access_token, get_current_user, require_roles, verify_password
from app.services.audit import record_audit_event

router = APIRouter()


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: str = ""


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    return {
        "status": "HALTED_BY_KILL_SWITCH" if (ctrl and ctrl.global_kill_switch) else "OPERATIONAL",
        "phase": "Phase 1 - Foundation",
        "ai_provider_policy": "google_gemini_only",
        "global_kill_switch": ctrl.global_kill_switch if ctrl else False,
    }


@router.post("/auth/token")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(User).where(User.username == form_data.username))
    user = res.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(data={"sub": user.username, "role": user.role})
    await record_audit_event(db, "HUMAN", user.username, "USER_LOGIN", f"user:{user.username}")
    return {"access_token": token, "token_type": "bearer", "role": user.role}


@router.get("/blogs")
async def list_country_blogs(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    res = await db.execute(select(CountryBlog).order_by(CountryBlog.rollout_wave, CountryBlog.country_code))
    return res.scalars().all()


@router.get("/configs")
async def list_configs(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    res = await db.execute(select(ConfigRegistry).order_by(ConfigRegistry.category, ConfigRegistry.config_key))
    return res.scalars().all()


@router.post("/controls/kill-switch")
async def toggle_kill_switch(
    payload: KillSwitchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN")),
):
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one()
    ctrl.global_kill_switch = payload.enabled
    ctrl.kill_switch_reason = payload.reason
    ctrl.updated_by = current_user.username
    await db.commit()
    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=current_user.username,
        action="GLOBAL_KILL_SWITCH_TOGGLED",
        resource_ref="system:kill_switch",
        details={"enabled": payload.enabled, "reason": payload.reason},
    )
    return {"global_kill_switch": ctrl.global_kill_switch, "reason": ctrl.kill_switch_reason}


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("OWNER", "ADMIN", "LEGAL_COMPLIANCE_ADMIN")),
):
    res = await db.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit))
    return res.scalars().all()
