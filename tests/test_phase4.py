from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.compliance import ComplianceEvaluation
from app.models.content_ai import GeminiUsageLog
from app.models.foundation import ConfigRegistry, User
from app.security.auth import get_password_hash


def _get_owner_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "owner", "password": "ChangeMeOwner@2026!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _reset_compliance_state():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ComplianceEvaluation))
        await db.execute(delete(GeminiUsageLog))
        c_res = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == "ai.gemini_monthly_cap_usd"))
        cfg = c_res.scalar_one_or_none()
        if cfg:
            cfg.config_value = "30.00"
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
        await db.commit()


def test_ymyl_blocked_until_approved_then_passes_compliance_and_publishes():
    with TestClient(app) as client:
        client.portal.call(_reset_compliance_state)
        headers = _get_owner_headers(client)

        draft_res = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "NL",
                "language_code": "nl-NL",
                "niche": "Finance",
                "title": "Europese Spaarrente en Inflatie Gids",
                "body_markdown": "Uitgebreide analyse van spaarrentes, koopkracht en risicospreiding binnen de Europese Unie voor particuliere spaarders onder actuele centrale bank richtlijnen.",
                "citations": ["https://www.ecb.europa.eu"],
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        )
        assert draft_res.status_code == 200
        article_id = draft_res.json()["id"]

        blocked_eval = client.post(f"/api/v1/articles/{article_id}/compliance-check", headers=headers)
        assert blocked_eval.status_code == 200
        assert blocked_eval.json()["overall_status"] == "BLOCKED"
        assert blocked_eval.json()["ymyl_gate_satisfied"] is False

        rev_token = client.post(
            "/api/v1/auth/token",
            data={"username": "health_reviewer", "password": "ReviewerPass@2026!"},
        ).json()["access_token"]
        rev_headers = {"Authorization": f"Bearer {rev_token}"}
        rev_res = client.post(
            f"/api/v1/articles/{article_id}/ymyl-review",
            json={"decision": "APPROVED", "review_notes": "Sources and EU disclosures verified."},
            headers=rev_headers,
        )
        assert rev_res.status_code == 200

        passed_eval = client.post(f"/api/v1/articles/{article_id}/compliance-check", headers=headers)
        assert passed_eval.status_code == 200
        assert passed_eval.json()["overall_status"] == "PASSED"
        assert passed_eval.json()["gdpr_dsa_compliant"] is True
        assert passed_eval.json()["seo_score"] >= 60

        pub_res = client.post(f"/api/v1/publish/articles/{article_id}", headers=headers)
        assert pub_res.status_code == 200
        assert pub_res.json()["status"] == "PUBLISHED"

        reports_res = client.get("/api/v1/compliance/reports", headers=headers)
        assert reports_res.status_code == 200
        assert len(reports_res.json()) >= 2
