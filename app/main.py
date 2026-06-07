"""EMS Belgium — FastAPI applicatie entry point."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import elia, jobs, optimization, prices

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger    = logging.getLogger(__name__)
_BRUSSELS = ZoneInfo("Europe/Brussels")
_scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Lifespan — start/stop APScheduler voor D+1 prijsdetectie
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.routers.prices import check_tomorrow_prices_task

    # Elke 5 min tussen 12:00–17:00 Brussels time
    _scheduler.add_job(
        check_tomorrow_prices_task,
        CronTrigger(hour="12-17", minute="*/5", timezone="Europe/Brussels"),
        id="d1_price_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("APScheduler gestart — D+1 prijsdetectie actief (12:00–17:00 CET, elke 5 min)")

    # Onmiddellijke check als we binnen het publicatievenster vallen
    if 12 <= datetime.now(_BRUSSELS).hour < 18:
        asyncio.create_task(check_tomorrow_prices_task())
        logger.info("D+1 controle direct gestart (binnen publicatievenster)")

    yield  # ← app draait hier

    _scheduler.shutdown(wait=False)
    logger.info("APScheduler gestopt")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="EMS Belgium API",
    description="Smart Battery & Grid Management — FastAPI backend",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(prices.router, prefix="/api")
app.include_router(elia.router, prefix="/api")
app.include_router(optimization.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}


# ---------------------------------------------------------------------------
# Statische frontend (serveer vanuit /static — wordt later gevuld)
# ---------------------------------------------------------------------------
_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
