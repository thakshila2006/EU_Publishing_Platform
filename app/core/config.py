from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "EU Multi-Country AI Publishing & Monetization Platform"
    APP_VERSION: str = "1.0.0-phase1"
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = "dev-only-change-in-production-64-chars-minimum-secret-key"
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/eu_platform_dev.db"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    APPROVED_AI_PROVIDER: str = "google_gemini"
    GEMINI_API_KEY: str = "PENDING_USER_KEY"
    GEMINI_MONTHLY_BUDGET_USD: float = 30.0

    MASTER_BRAND_SHORTLIST: list[str] = ["Veltora", "Veridian", "Elvion"]
    ACTIVE_MASTER_BRAND: str = "PENDING_USER_SELECTION"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
