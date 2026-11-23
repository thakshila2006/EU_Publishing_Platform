import xml.etree.ElementTree as ET
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.content_ai import GeminiUsageLog
from app.models.foundation import ConfigRegistry, CountryBlog, User
from app.models.publishing import PublishJob
from app.security.auth import get_password_hash


def _get_owner_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "owner", "password": "ChangeMeOwner@2026!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _reset_publishing_state():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(PublishJob))
        await db.execute(delete(GeminiUsageLog))

        c_res = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == "ai.gemini_monthly_cap_usd"))
        cfg = c_res.scalar_one_or_none()
        if cfg:
            cfg.config_value = "30.00"

        b_res = await db.execute(select(CountryBlog).where(CountryBlog.country_code == "SE"))
        se_blog = b_res.scalar_one_or_none()
        if se_blog:
            se_blog.warmup_stage = 1
            se_blog.daily_post_cap = 2

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


def test_ymyl_publish_block_idempotency_and_warmup_quota():
    with TestClient(app) as client:
        client.portal.call(_reset_publishing_state)
        headers = _get_owner_headers(client)

        # 1. Create YMYL draft -> cannot publish before human review
        ymyl_res = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "SE",
                "language_code": "sv-SE",
                "niche": "Health & Wellness",
                "title": "Sovrutiner i Norden",
                "body_markdown": "Praktiska tips for battre somn under ljusa sommarnatter.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        )
        assert ymyl_res.status_code == 200
        ymyl_id = ymyl_res.json()["id"]

        blocked_pub = client.post(f"/api/v1/publish/articles/{ymyl_id}", headers=headers)
        assert blocked_pub.status_code == 403

        # 2. Approve YMYL draft -> publishes cleanly and deduplicates on repeat
        rev_login = client.post(
            "/api/v1/auth/token",
            data={"username": "health_reviewer", "password": "ReviewerPass@2026!"},
        )
        rev_headers = {"Authorization": f"Bearer {rev_login.json()['access_token']}"}
        client.post(
            f"/api/v1/articles/{ymyl_id}/ymyl-review",
            json={"decision": "APPROVED", "review_notes": "Approved for Sweden blog."},
            headers=rev_headers,
        )

        pub1 = client.post(f"/api/v1/publish/articles/{ymyl_id}", headers=headers)
        assert pub1.status_code == 200
        pub1_data = pub1.json()
        assert pub1_data["status"] == "PUBLISHED"
        assert pub1_data["deduplicated"] is False
        assert pub1_data["quota_remaining_today"] == 1

        pub1_repeat = client.post(f"/api/v1/publish/articles/{ymyl_id}", headers=headers)
        assert pub1_repeat.status_code == 200
        assert pub1_repeat.json()["deduplicated"] is True
        assert pub1_repeat.json()["blogger_post_id"] == pub1_data["blogger_post_id"]

        # 3. Publish second non-YMYL post -> reaches Stage 1 daily cap (2/2)
        d2 = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "SE",
                "language_code": "sv-SE",
                "niche": "Technology",
                "title": "Svensk Fiberutbyggnad",
                "body_markdown": "Oversikt over bredband och digital infrastruktur i Sverige.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        ).json()["id"]
        pub2 = client.post(f"/api/v1/publish/articles/{d2}", headers=headers)
        assert pub2.status_code == 200
        assert pub2.json()["quota_remaining_today"] == 0

        # 4. Third post same day on Stage 1 hits 429 warm-up quota guard
        d3 = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "SE",
                "language_code": "sv-SE",
                "niche": "Technology",
                "title": "Gront Datacenter i Lulea",
                "body_markdown": "Hallbar serverdrift i norra Sverige.",
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        ).json()["id"]
        pub3_blocked = client.post(f"/api/v1/publish/articles/{d3}", headers=headers)
        assert pub3_blocked.status_code == 429


def test_xml_theme_generation_and_silo_isolation():
    with TestClient(app) as client:
        headers = _get_owner_headers(client)

        xml_res = client.post("/api/v1/blogs/DE/xml-theme?brand_name=Veltora", headers=headers)
        assert xml_res.status_code == 200
        assert "application/xml" in xml_res.headers["content-type"]
        root = ET.fromstring(xml_res.text)
        assert root.tag.endswith("html")
        assert "hreflang='de-DE'" in xml_res.text
        assert "gdpr-consent-banner-slot" in xml_res.text

        # Silo isolation: SE published links must NOT leak into DE internal links
        de_links = client.get("/api/v1/blogs/DE/internal-links?language_code=de-DE", headers=headers)
        assert de_links.status_code == 200
        assert de_links.json()["links"] == []


def test_ascii_slug_transliteration_for_nordic_and_german_titles():
    from app.services.blogger_publisher import _slugify

    assert _slugify("S\u00f6mnhygien och dygnsrytm i norra Europa") == "somnhygien-och-dygnsrytm-i-norra-europa"
    assert _slugify("\u00c4pfel, \u00d6l & \u00dcbungstra\u00dfe") == "apfel-ol-ubungstrasse"

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/token",
            data={"username": "owner", "password": "ChangeMeOwner@2026!"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        draft_res = client.post(
            "/api/v1/articles/drafts",
            json={
                "country_code": "SE",
                "language_code": "sv-SE",
                "niche": "Technology",
                "title": "S\u00f6mnhygien och gr\u00f6n energi i V\u00e4ster\u00e5s",
                "body_markdown": (
                    "En detaljerad genomg\u00e5ng av f\u00f6rnybar energi, h\u00e5llbar teknik och "
                    "modern infrastruktur i Sverige f\u00f6r f\u00f6retag och offentlig sektor."
                ),
                "ai_provider": "google_gemini",
                "simulated_cost_usd": 0.01,
            },
            headers=headers,
        )
        assert draft_res.status_code == 200
        article_id = draft_res.json()["id"]

        warmup_res = client.patch(
            "/api/v1/blogs/SE/warmup-stage",
            json={"warmup_stage": 3},
            headers=headers,
        )
        assert warmup_res.status_code == 200

        pub_res = client.post(f"/api/v1/publish/articles/{article_id}", headers=headers)
        assert pub_res.status_code == 200
        url = pub_res.json()["published_url"]
        assert "?" not in url
        assert url.endswith("/somnhygien-och-gron-energi-i-vasteraas.html")
