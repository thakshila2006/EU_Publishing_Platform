from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.compliance import ComplianceEvaluation
from app.models.content_ai import GeminiUsageLog
from app.models.foundation import ConfigRegistry, CountryBlog
from app.models.monetization import MonetizationProfile, RevenueLedgerEntry
from app.models.publishing import PublishJob


def _get_owner_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "owner", "password": "ChangeMeOwner@2026!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _reset_phase5_state():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(RevenueLedgerEntry))
        await db.execute(delete(MonetizationProfile))
        await db.execute(delete(PublishJob))
        await db.execute(delete(ComplianceEvaluation))
        await db.execute(delete(GeminiUsageLog))
        c_res = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == "ai.gemini_monthly_cap_usd"))
        cfg = c_res.scalar_one_or_none()
        if cfg:
            cfg.config_value = "30.00"
        b_res = await db.execute(select(CountryBlog).where(CountryBlog.country_code == "DK"))
        dk_blog = b_res.scalar_one_or_none()
        if dk_blog:
            dk_blog.warmup_stage = 1
            dk_blog.daily_post_cap = 2
        await db.commit()


def test_monetization_gate_revenue_ledger_and_telemetry_overview():
    with TestClient(app) as client:
        client.portal.call(_reset_phase5_state)
        headers = _get_owner_headers(client)

        # 1. Cannot activate AdSense before published + compliant content exists
        premature = client.put(
            "/api/v1/monetization/blogs/DK",
            json={"enable_adsense": True, "enable_affiliate": False, "adsense_slot_id": "ca-pub-001"},
            headers=headers,
        )
        assert premature.status_code == 403

        # 2. Create draft -> pass EU compliance -> publish
        draft_id = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "DK",
                "language_code": "da-DK",
                "niche": "Technology",
                "title": "Gr?n Energi og Cloud Infrastruktur i Danmark",
                "body_markdown": "Detaljeret gennemgang af vedvarende energi, gronne datacentre, cloud-infrastruktur og digitale investeringer i hele Danmark for virksomheder og offentlige aktorer.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.05,
            },
            headers=headers,
        ).json()["id"]

        comp_res = client.post(f"/api/v1/articles/{draft_id}/compliance-check", headers=headers)
        assert comp_res.status_code == 200
        assert comp_res.json()["overall_status"] == "PASSED"

        pub_res = client.post(f"/api/v1/publish/articles/{draft_id}", headers=headers)
        assert pub_res.status_code == 200

        # 3. Activate monetization and record AdSense revenue
        active_mon = client.put(
            "/api/v1/monetization/blogs/DK",
            json={"enable_adsense": True, "enable_affiliate": True, "adsense_slot_id": "ca-pub-dk-9001"},
            headers=headers,
        )
        assert active_mon.status_code == 200
        assert active_mon.json()["readiness_status"] == "ACTIVE"

        rev_res = client.post(
            "/api/v1/monetization/ledger",
            json={
                "country_code": "DK",
                "channel": "ADSENSE",
                "gross_amount_usd": 12.50,
                "pageviews": 2500,
                "clicks": 48,
                "notes": "Initial DK AdSense settlement.",
            },
            headers=headers,
        )
        assert rev_res.status_code == 200

        # 4. Verify executive telemetry & FinOps ROI rollup
        tel_res = client.get("/api/v1/telemetry/overview", headers=headers)
        assert tel_res.status_code == 200
        tel = tel_res.json()
        assert tel["monetization"]["active_monetized_blogs"] == 1
        assert tel["monetization"]["gross_revenue_usd"] == 12.5
        assert tel["monetization"]["effective_rpm_usd"] == 5.0
        assert tel["finops"]["gemini_monthly_spend_usd"] == 0.05
        assert tel["finops"]["net_margin_usd"] == 12.45
