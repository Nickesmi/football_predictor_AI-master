import re
from pathlib import Path

def build_fixtures_service():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # Locate Block 1: from _FIXTURE_CACHE_DIR = Path(".cache") up to def _fetch_sofascore_fixtures (first one)
    start_b1 = -1
    end_b1 = -1
    for i, line in enumerate(lines):
        if "# ─── Fixture cache ─────────────────────────────────────────────────────────" in line and start_b1 == -1:
            start_b1 = i
        if "def _fetch_sofascore_fixtures" in line and start_b1 != -1 and end_b1 == -1:
            end_b1 = i
            break
            
    print(f"Block 1: lines {start_b1+1} to {end_b1}")
    block1_lines = lines[start_b1:end_b1]
    
    # Locate Block 2: from def _fetch_sofascore_fixtures (second one around line 509) or @app.get("/api/fixtures/today")
    # Let's find @app.get("/api/fixtures/today")
    start_b2 = -1
    end_b2 = -1
    for i, line in enumerate(lines):
        if '@app.get("/api/fixtures/today")' in line and start_b2 == -1:
            start_b2 = i
        if 'return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})' in line and start_b2 != -1:
            end_b2 = i + 1
            break
            
    print(f"Block 2: lines {start_b2+1} to {end_b2}")
    block2_lines = lines[start_b2:end_b2]
    
    # Process Block 2 lines:
    # 1. Rename get_today_fixtures -> get_today_fixtures_service and remove @app.get
    # 2. Rename get_fixtures_by_date -> get_fixtures_by_date_service and remove @app.get
    # 3. Rename precompute_predictions -> precompute_predictions_service and remove @app.get
    # 4. Rename get_prediction_status -> get_prediction_status_service and remove @app.get
    # 5. In _sofascore_to_fixture, change signature to requested_date: str = ""
    
    processed_b2 = []
    i = 0
    while i < len(block2_lines):
        line = block2_lines[i]
        if line.strip().startswith('@app.get("/api/fixtures/today")') or \
           line.strip().startswith('@app.get("/api/fixtures/{date_str}")') or \
           line.strip().startswith('@app.get("/api/precompute-predictions")') or \
           line.strip().startswith('@app.get("/api/prediction-status/{fixture_id}")'):
            i += 1
            continue
            
        if "def get_today_fixtures(" in line:
            line = line.replace("def get_today_fixtures(", "def get_today_fixtures_service(")
            # also inside get_today_fixtures_service, replace get_fixtures_by_date( with get_fixtures_by_date_service(
        elif "return get_fixtures_by_date(" in line:
            line = line.replace("return get_fixtures_by_date(", "return get_fixtures_by_date_service(")
        elif "def get_fixtures_by_date(" in line:
            line = line.replace("def get_fixtures_by_date(", "def get_fixtures_by_date_service(")
        elif "def precompute_predictions(" in line:
            line = line.replace("def precompute_predictions(", "def precompute_predictions_service(")
        elif "def get_prediction_status(" in line:
            line = line.replace("def get_prediction_status(", "def get_prediction_status_service(")
        elif "def _sofascore_to_fixture(ev: dict, requested_date: str)" in line:
            line = line.replace("requested_date: str)", "requested_date: str = \"\")")
            
        processed_b2.append(line)
        i += 1
        
    # Also in _sofascore_to_fixture inside processed_b2, let's make sure if not requested_date: date_str = dt...
    # Let's check where date_str = requested_date is
    final_b2 = []
    for line in processed_b2:
        if "date_str = requested_date" in line:
            final_b2.append("    date_str = requested_date or \"\"")
        else:
            final_b2.append(line)
            
    service_code = [
        '"""',
        'Fixtures & Precomputation Service.',
        '',
        'Provides business logic and caching for daily fixtures, live scoreline merging,',
        'and background prediction pre-warm.',
        '"""',
        '',
        'import os',
        'import sys',
        'import time',
        'import json',
        'import logging',
        'import zoneinfo',
        'import urllib.request',
        'import urllib.parse',
        'import urllib.error',
        'from datetime import datetime, timezone, timedelta',
        'from pathlib import Path',
        'from typing import Any, Optional',
        'from fastapi import BackgroundTasks, HTTPException',
        'from src.config import logger, TOP_LEAGUES, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST',
        'from src.db.database import get_db',
        'from api.services.logo_service import resolve_team_logo as _resolve_team_logo',
        'from api.services.match_analysis_service import _compute_match_analysis, _ANALYSIS_CACHE',
        'from src.data.sofascore_fetcher import fetch_fixtures_by_date',
        'from src.data.live_score_provider import fetch_live_scores',
        '',
        '_PREDICTION_STATUS: dict[str, dict] = {}',
        '',
    ] + block1_lines + [''] + final_b2 + ['']
    
    Path("api/services/fixtures_service.py").write_text("\n".join(service_code))
    print("Created api/services/fixtures_service.py")
    
    router_code = [
        '"""',
        'Fixtures Router.',
        '',
        'Handles endpoints for daily fixtures, date-based fixtures, prediction precomputation,',
        'and prediction precomputation status. Strictly delegates business logic to fixtures_service.',
        '"""',
        '',
        'from fastapi import APIRouter, BackgroundTasks',
        'from api.services import fixtures_service',
        '',
        'router = APIRouter(tags=["Fixtures & Precomputation"])',
        '',
        '',
        '@router.get("/api/fixtures/today")',
        'def get_today_fixtures(background_tasks: BackgroundTasks):',
        '    return fixtures_service.get_today_fixtures_service(background_tasks)',
        '',
        '',
        '@router.get("/api/precompute-predictions")',
        'def precompute_predictions(date_str: str, background_tasks: BackgroundTasks):',
        '    return fixtures_service.precompute_predictions_service(date_str, background_tasks)',
        '',
        '',
        '@router.get("/api/prediction-status/{fixture_id}")',
        'def get_prediction_status(fixture_id: str):',
        '    return fixtures_service.get_prediction_status_service(fixture_id)',
        '',
        '',
        '@router.get("/api/fixtures/{date_str}")',
        'def get_fixtures_by_date(date_str: str, background_tasks: BackgroundTasks, force_refresh: bool = False):',
        '    return fixtures_service.get_fixtures_by_date_service(date_str, background_tasks, force_refresh=force_refresh)',
        '',
    ]
    Path("api/routers/fixtures.py").write_text("\n".join(router_code))
    print("Created api/routers/fixtures.py")
    
    # Now let's create scratch/update_main_for_fixtures.py
    update_script = [
        'from pathlib import Path',
        '',
        'def update_main():',
        '    main_path = Path("api/main.py")',
        '    lines = main_path.read_text().splitlines()',
        '    ',
        '    # We need to remove Block 1 (from `# ─── Fixture cache` up to `# ── Endpoints`)',
        '    # and Block 2 (from `@app.get("/api/fixtures/today")` up to `return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})`)',
        '    ',
        '    start_b1 = -1',
        '    end_b1 = -1',
        '    for i, line in enumerate(lines):',
        '        if "# ─── Fixture cache ─────────────────────────────────────────────────────────" in line and start_b1 == -1:',
        '            start_b1 = i',
        '        if "# ── Endpoints ──────────────────────────" in line and start_b1 != -1 and end_b1 == -1:',
        '            end_b1 = i',
        '            break',
        '            ',
        '    start_b2 = -1',
        '    end_b2 = -1',
        '    for i, line in enumerate(lines):',
        '        if \'@app.get("/api/fixtures/today")\' in line and start_b2 == -1:',
        '            start_b2 = i',
        '        if \'return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})\' in line and start_b2 != -1:',
        '            end_b2 = i + 1',
        '            break',
        '            ',
        '    print(f"Removing Block 1: {start_b1+1} to {end_b1}")',
        '    print(f"Removing Block 2: {start_b2+1} to {end_b2}")',
        '    ',
        '    # Also in import section (around line 180), add imports from fixtures_service and include router',
        '    # Let\'s find where app.include_router(analysis_router) is',
        '    import_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "app.include_router(analysis_router)" in line:',
        '            import_idx = i + 1',
        '            break',
        '            ',
        '    new_imports = [',
        '        "",',
        '        "# Fixtures Service & Router",',
        '        "from api.services.fixtures_service import (",',
        '        "    _read_fixture_cache,",',
        '        "    _write_fixture_cache,",',
        '        "    _fetch_api_football_fixtures,",',
        '        "    _fetch_sofascore_fixtures,",',
        '        "    _get_istanbul_today,",',
        '        "    _PREDICTION_STATUS,",',
        '        "    _precompute_predictions_for_date,",',
        '        "    _categorize_competition,",',
        '        "    _sofascore_to_fixture,",',
        '        ")",',
        '        "from api.routers.fixtures import router as fixtures_router",',
        '        "app.include_router(fixtures_router)",',
        '    ]',
        '    ',
        '    # Construct new lines: up to start_b1, then skip to end_b1, then up to start_b2, then skip to end_b2',
        '    # Note: we also insert new_imports after import_idx.',
        '    # Let\'s do it carefully by line index.',
        '    ',
        '    new_lines = []',
        '    for i, line in enumerate(lines):',
        '        if start_b1 <= i < end_b1:',
        '            continue',
        '        if start_b2 <= i < end_b2:',
        '            continue',
        '        new_lines.append(line)',
        '        if i == import_idx - 1:',
        '            new_lines.extend(new_imports)',
        '            ',
        '    main_path.write_text("\\n".join(new_lines) + "\\n")',
        '    print(f"Updated api/main.py. New line count: {len(new_lines)}")',
        '',
        'if __name__ == "__main__":',
        '    update_main()',
    ]
    Path("scratch/update_main_for_fixtures.py").write_text("\n".join(update_script))
    print("Created scratch/update_main_for_fixtures.py")

if __name__ == "__main__":
    build_fixtures_service()
