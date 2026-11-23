from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.compliance_routes import router as compliance_router
from app.api.v1.content_routes import router as content_router
from app.api.v1.monetization_routes import router as monetization_router
from app.api.v1.publishing_routes import router as publishing_router
from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, Base, engine, get_db
from app.models import compliance as _compliance_models  # noqa: F401
from app.models import content_ai as _content_ai_models  # noqa: F401
from app.models import monetization as _monetization_models  # noqa: F401
from app.models import publishing as _publishing_models  # noqa: F401
from app.models.foundation import CountryBlog, SystemControl
from app.services.seed import seed_initial_data
from app.services.telemetry_monetization import build_telemetry_overview


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
app.include_router(v1_router, prefix="/api/v1")
app.include_router(content_router, prefix="/api/v1")
app.include_router(publishing_router, prefix="/api/v1")
app.include_router(compliance_router, prefix="/api/v1")
app.include_router(monetization_router, prefix="/api/v1")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    blogs = (await db.execute(select(CountryBlog).order_by(CountryBlog.rollout_wave, CountryBlog.country_code))).scalars().all()
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    telemetry = await build_telemetry_overview(db)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "blogs": blogs,
            "kill_switch": ctrl.global_kill_switch if ctrl else False,
            "telemetry": telemetry,
        },
    )
