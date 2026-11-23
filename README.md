# EU Publishing Platform

FastAPI, SQLite, and Uvicorn backend for the 5-phase EU Publishing Platform (Foundation RBAC & Audit, Gemini/YMYL Content Gating, Blogger Publishing & XML Themes, SEO/GDPR/DSA Compliance, and Monetization/FinOps Telemetry).

See [ARCHITECTURE.md](./ARCHITECTURE.md) for system design, data models, and phase details.

## Local Setup

1. Navigate to the repository:
   `Set-Location -Path "C:\EU_Publishing_Platform"`
2. Install dependencies:
   `python -m pip install --upgrade pip`
   `python -m pip install -r requirements.txt`
3. Start the Uvicorn server (initializes SQLite at `data\eu_platform_dev.db` and seeds baseline data on startup):
   `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info`

## Running Tests (`pytest`)

Run the full test suite from the repository root:

`python -m pytest -v`

## Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) — Architecture overview, compliance gates, publishing pipeline, and FinOps/telemetry specifications.