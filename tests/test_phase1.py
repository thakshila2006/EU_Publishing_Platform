from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.foundation import AuditEvent, User
from app.security.auth import get_password_hash


async def _ensure_reader_user():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.username == "reader_test"))
        if not res.scalar_one_or_none():
            db.add(
                User(
                    username="reader_test",
                    email="reader_test@localhost.local",
                    hashed_password=get_password_hash("ReaderPass@2026!"),
                    role="READ_ONLY",
                    mfa_enabled=False,
                    is_active=True,
                )
            )
            await db.commit()


def test_health_and_rbac():
    with TestClient(app) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["ai_provider_policy"] == "google_gemini_only"

        r_unauth = client.get("/api/v1/blogs")
        assert r_unauth.status_code == 401

        login = client.post(
            "/api/v1/auth/token",
            data={"username": "owner", "password": "ChangeMeOwner@2026!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        blogs = client.get("/api/v1/blogs", headers=headers)
        assert blogs.status_code == 200
        assert len(blogs.json()) == 7


def test_role_authorization_403():
    with TestClient(app) as client:
        client.portal.call(_ensure_reader_user)

        reader_login = client.post(
            "/api/v1/auth/token",
            data={"username": "reader_test", "password": "ReaderPass@2026!"},
        )
        assert reader_login.status_code == 200
        reader_headers = {"Authorization": f"Bearer {reader_login.json()['access_token']}"}

        forbidden_ks = client.post(
            "/api/v1/controls/kill-switch",
            json={"enabled": True, "reason": "Unauthorized attempt"},
            headers=reader_headers,
        )
        assert forbidden_ks.status_code == 403

        forbidden_audit = client.get("/api/v1/audit-logs", headers=reader_headers)
        assert forbidden_audit.status_code == 403


def test_kill_switch_and_hash_chained_audit():
    with TestClient(app) as client:
        owner_login = client.post(
            "/api/v1/auth/token",
            data={"username": "owner", "password": "ChangeMeOwner@2026!"},
        )
        assert owner_login.status_code == 200
        owner_headers = {"Authorization": f"Bearer {owner_login.json()['access_token']}"}

        ks_on = client.post(
            "/api/v1/controls/kill-switch",
            json={"enabled": True, "reason": "Phase 1 verification drill"},
            headers=owner_headers,
        )
        assert ks_on.status_code == 200
        assert ks_on.json()["global_kill_switch"] is True

        health_halted = client.get("/api/v1/health")
        assert health_halted.status_code == 200
        assert health_halted.json()["status"] == "HALTED_BY_KILL_SWITCH"

        ks_off = client.post(
            "/api/v1/controls/kill-switch",
            json={"enabled": False, "reason": "Drill complete"},
            headers=owner_headers,
        )
        assert ks_off.status_code == 200
        assert ks_off.json()["global_kill_switch"] is False

        audit_res = client.get("/api/v1/audit-logs?limit=50", headers=owner_headers)
        assert audit_res.status_code == 200
        events = list(reversed(audit_res.json()))
        assert len(events) >= 2

        for idx in range(1, len(events)):
            prev_ev = events[idx - 1]
            curr_ev = events[idx]
            assert curr_ev["prev_hash"] == prev_ev["event_hash"]
            assert len(curr_ev["event_hash"]) == 64
