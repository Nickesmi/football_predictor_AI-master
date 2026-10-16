"""
Football Predictor AI - API v5.0
Fetches REAL daily matches via API-Football v3. Computes per-match unique predictions
using Poisson (goals) + statistical models (corners, cards).

DATA SOURCE: api-football.com (v3) — all leagues, real logos, transparent disk cache.
COVERS: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, UCL, UEL + more.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.services.system_service import log_baseline_status_at_startup, start_background_tasks
    log_baseline_status_at_startup()
    await start_background_tasks()
    yield

from src.config import APP_ENV, CORS_ALLOW_ORIGINS, validate_security_config  # noqa: E402

_security_problems = validate_security_config()
if _security_problems:
    raise RuntimeError(f"Refusing to start (APP_ENV={APP_ENV}): " + "; ".join(_security_problems))

# An operator hold on the production database stops the app here, before any
# background task (fixture refresh, nightly refit) can start or spend quota.
from src.db.database import DB_PATH as _DB_PATH  # noqa: E402
from src.ops import service_hold as _service_hold  # noqa: E402

_service_hold.check(_DB_PATH)

# ── Instantiate App ──────────────────────────────────────────────────────────
app = FastAPI(title="Football Predictor AI API", version="5.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from src.baseline.state import BaselineFrozenError  # noqa: E402


@app.exception_handler(BaselineFrozenError)
async def _baseline_frozen_handler(request: Request, exc: BaselineFrozenError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# Include routers
from api.routers.analytics import router as analytics_router
app.include_router(analytics_router)
from api.routers.images import router as images_router
app.include_router(images_router)
from api.routers.calibration import router as calibration_router
app.include_router(calibration_router)
from api.routers.performance import router as performance_router
app.include_router(performance_router)
from api.routers.leagues import router as leagues_router
app.include_router(leagues_router)
from api.routers.execution import router as execution_router
app.include_router(execution_router)
from api.routers.live import router as live_router
app.include_router(live_router)
from api.routers.portfolio import router as portfolio_router
app.include_router(portfolio_router)

# Tennis prediction engine router (isolated — no football code modified)
from src.tennis.api.tennis_routes import router as tennis_router
app.include_router(tennis_router)

from api.routers.analysis import router as analysis_router
app.include_router(analysis_router)
from api.routers.fixtures import router as fixtures_router
app.include_router(fixtures_router)
from api.routers.results import router as results_router
app.include_router(results_router)
from api.routers.system import router as system_router
app.include_router(system_router)
from api.routers.debug import router as debug_router
app.include_router(debug_router)

# Re-export core analysis function for legacy script/test compatibility
from api.services.match_analysis_service import _compute_match_analysis
