import hashlib
import json
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.foundation import AuditEvent


async def record_audit_event(
    db: AsyncSession,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_ref: str,
    details: dict | str = "",
) -> AuditEvent:
    last_res = await db.execute(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(1))
    last_event = last_res.scalar_one_or_none()
    prev_hash = last_event.event_hash if last_event else "0" * 64

    details_str = json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else str(details)
    payload = f"{prev_hash}|{actor_type}|{actor_id}|{action}|{resource_ref}|{details_str}"
    event_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_ref=resource_ref,
        details=details_str,
        prev_hash=prev_hash,
        event_hash=event_hash,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event
