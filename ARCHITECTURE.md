# Veltora EU Publishing Platform - Architecture & API Reference

**Location:** `C:\EU_Publishing_Platform`  
**Stack:** FastAPI, AsyncSQLAlchemy, Pydantic, Jinja2, Pytest (Python 3.12)

---

## 1. System Architecture & Module Layout

| Phase | Domain Module | Models (`app/models/`) | Service Engine (`app/services/`) | API Router (`app/api/v1/`) |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | Foundation, RBAC & Audit | `foundation.py` (`User`, `CountryBlog`, `ConfigRegistry`, `SystemControl`, `AuditEvent`) | `audit.py`, `seed.py` | `routes.py` |
| **Phase 2** | Content & Gemini FinOps | `content_ai.py` (`ArticleDraft`, `ArticleLocalization`, `YmylReview`, `GeminiUsageLog`) | `gemini_finops.py` | `content_routes.py` |
| **Phase 3** | Blogger Publishing & Theme | `publishing.py` (`PublishJob`, `BloggerThemeSnapshot`) | `blogger_publisher.py` | `publishing_routes.py` |
| **Phase 4** | SEO, QA & EU Compliance | `compliance.py` (`ComplianceEvaluation`) | `compliance_qa.py` | `compliance_routes.py` |
| **Phase 5** | Monetization & Telemetry | `monetization.py` (`MonetizationProfile`, `RevenueLedgerEntry`) | `telemetry_monetization.py` | `monetization_routes.py` |

---

## 2. Core Pipeline & Governance Controls

* **RBAC & Immutable Audit Trail (Phase 1):** JWT bearer authentication enforces role boundaries (`OWNER`, `ADMIN`, `EDITOR`, `YMYL_EXPERT_REVIEWER`, `ANALYST`). Every state mutation writes a SHA-256 hash-chained `AuditEvent`, while `SystemControl.global_kill_switch` immediately halts AI generation, publishing, and monetization changes.
* **AI Policy & FinOps Circuit Breaker (Phase 2):** Restricts generation to `google_gemini`, flags YMYL niches (`Health & Wellness`, `Finance`) into `AWAITING_YMYL_HUMAN_APPROVAL`, raises an 80% budget warning alert, and trips a `429` circuit breaker when monthly spend exceeds `ai.gemini_monthly_cap_usd`.
* **Idempotent Warm-Up Publishing (Phase 3):** Generates deterministic SHA-256 idempotency keys per `(article_id, country_code, language_code)`, resolves local IANA timezones with jitter windows, enforces stage-based daily posting caps (`Stage 1: 2/day`, `Stage 2: 5/day`, `Stage 3: 8/day`), isolates internal link graphs by country/language silo, and outputs GDPR/DSA-ready Blogger XML v1 themes.
* **SEO, QA & EU Legal Gate (Phase 4):** Evaluates title/body thresholds, structured citations, EU country scope (`UK`, `DE`, `NL`, `SE`, `NO`, `DK`, `CH`), DSA AI transparency disclosures, and human YMYL approval before allowing schedule promotion.
* **Monetization Readiness & ROI Telemetry (Phase 5):** Blocks AdSense/Affiliate activation until a country blog has at least 1 published post and 1 `PASSED` compliance evaluation, logs revenue entries (`ADSENSE`, `AFFILIATE`, `SPONSORSHIP`), and computes live RPM and net margin (`revenue - Gemini spend`).

---

## 3. REST API Endpoint Reference (`/api/v1`)

| Phase | Method & Path | Required Role(s) | Purpose |
| :--- | :--- | :--- | :--- |
| **P1** | `POST /api/v1/auth/token` | Public | Authenticate user and issue JWT access token |
| **P1** | `GET /api/v1/health` | Public | Verify service health and database connectivity |
| **P1** | `POST /api/v1/system/kill-switch` | `OWNER`, `ADMIN` | Toggle global emergency kill switch |
| **P1** | `GET /api/v1/audit/verify` | Authenticated | Verify integrity of SHA-256 hash-chained audit ledger |
| **P2** | `POST /api/v1/articles/drafts` | `OWNER`, `ADMIN`, `EDITOR` | Create article draft with Gemini-only & FinOps budget checks |
| **P2** | `POST /api/v1/articles/{id}/localizations` | `OWNER`, `ADMIN`, `EDITOR` | Attach localized title/body variants to an article |
| **P2** | `POST /api/v1/articles/{id}/ymyl-review` | `YMYL_EXPERT_REVIEWER`, `OWNER` | Submit human `APPROVED` or `REJECTED` decision for YMYL drafts |
| **P2** | `GET /api/v1/finops/gemini-status` | Authenticated | Inspect current monthly Gemini spend, cap, 80% alert, and breaker state |
| **P3** | `POST /api/v1/publish/articles/{id}` | `OWNER`, `ADMIN`, `EDITOR` | Publish eligible article with idempotency deduplication & warm-up quota guard |
| **P3** | `GET /api/v1/publish/jobs` | Authenticated | List queued and published Blogger jobs |
| **P3** | `PATCH /api/v1/blogs/{country_code}/warmup-stage` | `OWNER`, `ADMIN` | Update country blog warm-up stage (`1` to `3`) and daily post cap |
| **P3** | `GET /api/v1/blogs/{country_code}/internal-links` | Authenticated | Retrieve country- and language-isolated internal link targets |
| **P3** | `POST /api/v1/blogs/{country_code}/xml-theme` | `OWNER`, `ADMIN` | Generate and snapshot EU-compliant Blogger XML theme (`application/xml`) |
| **P4** | `POST /api/v1/articles/{id}/compliance-check` | `OWNER`, `ADMIN`, `EDITOR`, `YMYL_EXPERT_REVIEWER` | Run SEO, QA, GDPR/DSA disclosure, and YMYL approval checks |
| **P4** | `GET /api/v1/compliance/reports` | Authenticated | Retrieve historical compliance evaluation reports |
| **P5** | `PUT /api/v1/monetization/blogs/{country_code}` | `OWNER`, `ADMIN` | Evaluate readiness and enable AdSense/Affiliate monetization slots |
| **P5** | `POST /api/v1/monetization/ledger` | `OWNER`, `ADMIN`, `ANALYST` | Record gross revenue, pageviews, and clicks per country channel |
| **P5** | `GET /api/v1/monetization/ledger` | Authenticated | List revenue ledger entries |
| **P5** | `GET /api/v1/telemetry/overview` | Authenticated | Retrieve unified pipeline, compliance pass rate, RPM, and FinOps net margin |

* **Operational Dashboard:** `GET /` (`http://127.0.0.1:8000/`)
* **Interactive OpenAPI Docs:** `GET /docs` (`http://127.0.0.1:8000/docs`)
