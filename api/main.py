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
    from api.services.system_service import start_background_tasks
    await start_background_tasks()
    yield

# ── Instantiate App ──────────────────────────────────────────────────────────
app = FastAPI(title="Football Predictor AI API", version="5.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
