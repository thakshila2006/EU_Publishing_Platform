from pathlib import Path

BASE_DIR = Path(".")

DIRS = [
    "app",
    "app/core",
    "app/db",
    "app/models",
    "app/schemas",
    "app/security",
    "app/services",
    "app/api",
    "app/api/v1",
    "app/templates",
    "app/static/css",
    "app/static/js",
    "data",
    "logs",
    "backups",
    "tests",
    ".vscode",
]

for d in DIRS:
    (BASE_DIR / d).mkdir(parents=True, exist_ok=True)

FILES = {}

FILES["requirements.txt"] = """fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
aiosqlite==0.20.0
pydantic==2.10.4
pydantic-settings==2.7.1
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
bcrypt==4.0.1
cryptography==44.0.0
jinja2==3.1.5
python-multipart==0.0.20
pytest==8.3.4
httpx==0.28.1
"""

FILES[".env.example"] = """APP_ENV=development
APP_SECRET_KEY=CHANGE_ME_TO_A_STRONG_64_CHAR_RANDOM_SECRET_IN_PRODUCTION
DATABASE_URL=sqlite+aiosqlite:///./data/eu_platform_dev.db
GEMINI_API_KEY=PENDING_USER_KEY
GEMINI_MONTHLY_BUDGET_USD=30.0
"""

FILES[".gitignore"] = """.venv/
__pycache__/
*.pyc
.env
data/*.db
data/*.db-*
logs/*
backups/*
"""

FILES["app/__init__.py"] = ""
FILES["app/core/__init__.py"] = ""
FILES["app/db/__init__.py"] = ""
FILES["app/models/__init__.py"] = ""
FILES["app/schemas/__init__.py"] = ""
FILES["app/security/__init__.py"] = ""
FILES["app/services/__init__.py"] = ""
FILES["app/api/__init__.py"] = ""
FILES["app/api/v1/__init__.py"] = ""

FILES["app/core/config.py"] = """from pydantic_settings import BaseSettings, SettingsConfigDict


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
"""

FILES["app/db/session.py"] = """from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
"""

FILES["app/models/foundation.py"] = """from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class CountryBlog(Base):
    __tablename__ = "country_blogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    country_name: Mapped[str] = mapped_column(String(64))
    iana_timezone: Mapped[str] = mapped_column(String(64))
    languages_csv: Mapped[str] = mapped_column(String(64))
    rollout_wave: Mapped[int] = mapped_column(Integer, default=1)
    domain_name: Mapped[str] = mapped_column(String(128), default="PENDING_USER_SELECTION")
    blogger_blog_id: Mapped[str] = mapped_column(String(128), default="PENDING_OAUTH")
    warmup_stage: Mapped[int] = mapped_column(Integer, default=1)
    daily_post_cap: Mapped[int] = mapped_column(Integer, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_paused_by_killswitch: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="READ_ONLY")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConfigRegistry(Base):
    __tablename__ = "config_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    config_value: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="GENERAL")
    status: Mapped[str] = mapped_column(String(32), default="APPROVED_BASELINE")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SystemControl(Base):
    __tablename__ = "system_controls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="SYSTEM")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    resource_ref: Mapped[str] = mapped_column(String(128))
    details: Mapped[str] = mapped_column(Text, default="")
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
"""

FILES["app/security/auth.py"] = """from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.foundation import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
ALGORITHM = "HS256"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.APP_SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_roles(*allowed_roles: str):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {current_user.role} lacks required permissions.",
            )
        return current_user
    return role_checker
"""

FILES["app/services/audit.py"] = """import hashlib
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
"""

FILES["app/services/seed.py"] = """import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.foundation import AuditEvent, ConfigRegistry, CountryBlog, SystemControl, User
from app.security.auth import get_password_hash
from app.services.audit import record_audit_event

SEVEN_COUNTRIES = [
    ("UK", "United Kingdom", "Europe/London", "en-GB", 1),
    ("DE", "Germany", "Europe/Berlin", "de-DE,en", 2),
    ("NL", "Netherlands", "Europe/Amsterdam", "nl-NL,en", 2),
    ("SE", "Sweden", "Europe/Stockholm", "sv-SE,en", 3),
    ("NO", "Norway", "Europe/Oslo", "nb-NO,en", 3),
    ("DK", "Denmark", "Europe/Copenhagen", "da-DK,en", 3),
    ("CH", "Switzerland", "Europe/Zurich", "de-CH,fr-CH,it-CH,en", 4),
]

INITIAL_CONFIGS = [
    ("ai.provider", "google_gemini", "AI_GOVERNANCE", "APPROVED_DECISION_07"),
    ("ai.gemini_monthly_cap_usd", "30.00", "FINOPS", "APPROVED_BASELINE"),
    ("ai.gemini_alert_threshold_pct", "80", "FINOPS", "APPROVED_BASELINE"),
    ("ymyl.mandatory_human_gate", "true", "COMPLIANCE", "APPROVED_DECISION_72"),
    ("ymyl.niches", json.dumps(["Health & Wellness", "Finance"]), "COMPLIANCE", "APPROVED_DECISION_02"),
    ("brand.shortlist", json.dumps(["Veltora", "Veridian", "Elvion"]), "BRANDING", "PENDING_USER_SELECTION"),
    ("brand.domain_strategy", "HYBRID_CCTLD_AND_PREFIXED_COM", "BRANDING", "APPROVED_BASELINE"),
    ("design.primary_color", "#0F2A3C", "VISUAL_IDENTITY", "APPROVED_BASELINE"),
    ("design.accent_color", "#0D7A6E", "VISUAL_IDENTITY", "APPROVED_BASELINE"),
]


async def seed_initial_data(db: AsyncSession):
    ctrl_res = await db.execute(select(SystemControl).limit(1))
    if not ctrl_res.scalar_one_or_none():
        db.add(SystemControl(global_kill_switch=False, kill_switch_reason="Nominal operation", updated_by="SYSTEM"))

    user_res = await db.execute(select(User).where(User.username == "owner"))
    if not user_res.scalar_one_or_none():
        db.add(
            User(
                username="owner",
                email="owner@localhost.local",
                hashed_password=get_password_hash("ChangeMeOwner@2026!"),
                role="OWNER",
                mfa_enabled=False,
                is_active=True,
            )
        )

    for code, name, tz, langs, wave in SEVEN_COUNTRIES:
        exists = await db.execute(select(CountryBlog).where(CountryBlog.country_code == code))
        if not exists.scalar_one_or_none():
            db.add(
                CountryBlog(
                    country_code=code,
                    country_name=name,
                    iana_timezone=tz,
                    languages_csv=langs,
                    rollout_wave=wave,
                    warmup_stage=1,
                    daily_post_cap=2,
                )
            )

    for key, val, cat, st in INITIAL_CONFIGS:
        c_exists = await db.execute(select(ConfigRegistry).where(ConfigRegistry.config_key == key))
        if not c_exists.scalar_one_or_none():
            db.add(ConfigRegistry(config_key=key, config_value=val, category=cat, status=st))

    await db.commit()

    audit_res = await db.execute(select(AuditEvent).limit(1))
    if not audit_res.scalar_one_or_none():
        await record_audit_event(
            db,
            actor_type="SYSTEM",
            actor_id="BOOTSTRAP",
            action="PHASE1_FOUNDATION_INITIALIZED",
            resource_ref="system:core",
            details={"countries": 7, "ai_provider": "google_gemini"},
        )
"""

FILES["app/api/v1/routes.py"] = """from fastapi import APIRouter, Depends, HTTPException
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
"""

FILES["app/templates/dashboard.html"] = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{{ app_name }} - Command Center</title>
  <style>
    :root {
      --bg: #F8FAFC;
      --surface: #FFFFFF;
      --text: #0F172A;
      --muted: #475569;
      --primary: #0F2A3C;
      --accent: #0D7A6E;
      --warning: #B45309;
      --danger: #B91C1C;
      --border: #E2E8F0;
    }
    [data-theme="dark"] {
      --bg: #0B1120;
      --surface: #111827;
      --text: #F8FAFC;
      --muted: #94A3B8;
      --primary: #1E3A5F;
      --accent: #14B8A6;
      --border: #1F2937;
    }
    body {
      margin: 0;
      font-family: Inter, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
    }
    header {
      background: var(--primary);
      color: #FFFFFF;
      padding: 1rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .container { max-width: 1200px; margin: 1.5rem auto; padding: 0 1.5rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.25rem;
    }
    .badge {
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      color: #FFF;
      background: var(--accent);
    }
    .badge.warn { background: var(--warning); }
    .badge.danger { background: var(--danger); }
    table { width: 100%; border-collapse: collapse; margin-top: 0.75rem; }
    th, td { text-align: left; padding: 0.65rem; border-bottom: 1px solid var(--border); font-size: 0.92rem; }
    button {
      background: var(--accent);
      color: #FFF;
      border: none;
      padding: 0.5rem 1rem;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 600;
    }
  </style>
</head>
<body>
  <header>
    <div>
      <strong>EU Multi-Country AI Publishing Platform</strong>
      <div style="font-size:0.82rem;opacity:0.85;">Phase 1: Foundation and Governance Core (Decisions 01-75)</div>
    </div>
    <div>
      <button type="button" onclick="toggleTheme()" aria-label="Toggle Light or Dark Theme">Toggle Light/Dark</button>
    </div>
  </header>

  <main class="container">
    <div class="grid">
      <div class="card">
        <div style="color:var(--muted);font-size:0.85rem;">AI Provider Policy (Decision 07)</div>
        <h3 style="margin:0.3rem 0;">Google Gemini Only</h3>
        <span class="badge">ENFORCED</span>
      </div>
      <div class="card">
        <div style="color:var(--muted);font-size:0.85rem;">YMYL Safety Gate (Decision 72)</div>
        <h3 style="margin:0.3rem 0;">Health and Finance Hold</h3>
        <span class="badge warn">MANDATORY HUMAN GATE</span>
      </div>
      <div class="card">
        <div style="color:var(--muted);font-size:0.85rem;">System Kill Switch (Decision 38)</div>
        <h3 style="margin:0.3rem 0;">{{ "HALTED" if kill_switch else "NOMINAL (ACTIVE)" }}</h3>
        <span class="badge {{ 'danger' if kill_switch else '' }}">{{ "EMERGENCY STOP" if kill_switch else "READY" }}</span>
      </div>
      <div class="card">
        <div style="color:var(--muted);font-size:0.85rem;">Master Brand (Pending Register)</div>
        <h3 style="margin:0.3rem 0;">Veltora / Veridian / Elvion</h3>
        <span class="badge warn">SHORTLISTED</span>
      </div>
    </div>

    <div class="card">
      <h2 style="margin-top:0;">7-Country Blogger Network Registry (Decisions 03, 04, 05, 11, 39, 68)</h2>
      <table>
        <thead>
          <tr>
            <th>Wave</th>
            <th>Country</th>
            <th>IANA Time Zone</th>
            <th>Languages</th>
            <th>Warm-Up Stage</th>
            <th>Daily Cap</th>
            <th>Domain Status</th>
          </tr>
        </thead>
        <tbody>
          {% for b in blogs %}
          <tr>
            <td>Wave {{ b.rollout_wave }}</td>
            <td><strong>{{ b.country_name }} ({{ b.country_code }})</strong></td>
            <td><code>{{ b.iana_timezone }}</code></td>
            <td>{{ b.languages_csv }}</td>
            <td>Stage {{ b.warmup_stage }}</td>
            <td>{{ b.daily_post_cap }} posts/day</td>
            <td>{{ b.domain_name }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </main>

  <script>
    function toggleTheme() {
      const root = document.documentElement;
      root.setAttribute('data-theme', root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    }
  </script>
</body>
</html>
"""

FILES["app/main.py"] = """from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, Base, engine, get_db
from app.models.foundation import CountryBlog, SystemControl
from app.services.seed import seed_initial_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
app.include_router(v1_router, prefix="/api/v1")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    blogs = (await db.execute(select(CountryBlog).order_by(CountryBlog.rollout_wave, CountryBlog.country_code))).scalars().all()
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "blogs": blogs,
            "kill_switch": ctrl.global_kill_switch if ctrl else False,
        },
    )
"""

FILES["tests/test_phase1.py"] = """from fastapi.testclient import TestClient
from app.main import app


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
"""

for rel_path, content in FILES.items():
    target = BASE_DIR / rel_path
    target.write_text(content.strip() + "\n", encoding="utf-8")

print("SUCCESS: Phase 1 Foundation structure and core files generated!")
