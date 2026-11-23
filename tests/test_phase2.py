from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
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


async def _reset_usage_and_configure(cap_usd: float = 30.0):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(GeminiUsageLog))

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
        client.portal.call(_reset_usage_and_configure, 30.0)
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


def test_ymyl_expert_rejection():
    with TestClient(app) as client:
        client.portal.call(_reset_usage_and_configure, 30.0)
        headers = _get_owner_headers(client)

        finance_draft = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "CH",
                "language_code": "de-CH",
                "niche": "Finance",
                "title": "Unverified High-Yield Claims",
                "body_markdown": "Draft containing unverified return projections requiring expert review.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.02,
            },
            headers=headers,
        )
        assert finance_draft.status_code == 200
        assert finance_draft.json()["workflow_state"] == "AWAITING_YMYL_HUMAN_APPROVAL"
        article_id = finance_draft.json()["id"]

        rev_login = client.post(
            "/api/v1/auth/token",
            data={"username": "health_reviewer", "password": "ReviewerPass@2026!"},
        )
        assert rev_login.status_code == 200
        rev_headers = {"Authorization": f"Bearer {rev_login.json()['access_token']}"}

        rejected = client.post(
            f"/api/v1/articles/{article_id}/ymyl-review",
            json={"decision": "REJECTED", "review_notes": "Rejected: missing regulatory risk disclosures."},
            headers=rev_headers,
        )
        assert rejected.status_code == 200
        assert rejected.json()["decision"] == "REJECTED"
        assert rejected.json()["workflow_state"] == "YMYL_REJECTED"


def test_finops_80pct_budget_warning_alert():
    with TestClient(app) as client:
        client.portal.call(_reset_usage_and_configure, 1.00)
        headers = _get_owner_headers(client)

        draft_res = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "NL",
                "language_code": "nl-NL",
                "niche": "Technology",
                "title": "Dutch Fiber Infrastructure Overview",
                "body_markdown": "Technical overview of broadband rollout across Dutch provinces.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.85,
            },
            headers=headers,
        )
        assert draft_res.status_code == 200
        finops = draft_res.json()["finops"]
        assert finops["utilization_pct"] == 85.0
        assert finops["budget_alert_80pct"] is True
        assert finops["circuit_breaker_tripped"] is False

        status_res = client.get("/api/v1/finops/gemini-status", headers=headers)
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["utilization_pct"] == 85.0
        assert status_data["alert_80pct_active"] is True
        assert status_data["circuit_breaker_tripped"] is False


def test_finops_circuit_breaker_trips_on_overage():
    with TestClient(app) as client:
        client.portal.call(_reset_usage_and_configure, 0.05)
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
