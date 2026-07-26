from pathlib import Path

def build_results_service():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # Locate start of Results Verification section
    start_idx = -1
    for i, line in enumerate(lines):
        if "# ── Results Verification ──" in line and start_idx == -1:
            start_idx = i
            break
            
    # Locate end: # PHASE 4: LIVE ADAPTIVE PIPELINE
    end_idx = -1
    for i, line in enumerate(lines):
        if "PHASE 4: LIVE ADAPTIVE PIPELINE" in line and i > start_idx:
            # step back above comments/blank lines before PHASE 4
            end_idx = i - 1
            while end_idx > 0 and (lines[end_idx].strip() == "" or lines[end_idx].strip().startswith("#")):
                end_idx -= 1
            end_idx += 1
            break
            
    print(f"Results section: lines {start_idx+1} to {end_idx}")
    section_lines = lines[start_idx:end_idx]
    
    # Filter and rename for results_service.py:
    # 1. Skip @app.get(...) decorators
    # 2. Skip inline imports that we move to the top: src.db.database, src.db.picks_repo, src.engine.pipeline
    # 3. Skip dead Execution Engine comments/imports if any are included
    # 4. Rename get_results_verification -> get_results_verification_service
    # 5. Rename run_investment_pipeline -> run_investment_pipeline_service
    
    service_body = []
    i = 0
    while i < len(section_lines):
        line = section_lines[i]
        stripped = line.strip()
        
        if stripped.startswith('@app.get("/api/results/{date_str}")') or \
           stripped.startswith('@app.get("/api/pipeline/run/{date_str}")'):
            i += 1
            continue
            
        if "from src.db.database import get_db" in stripped or \
           "from src.db.picks_repo import" in stripped or \
           "from src.engine.pipeline import" in stripped:
            i += 1
            continue
            
        if "Execution Engine — Market-Constrained Betting Agent" in stripped or \
           "from src.engine.execution_engine import" in stripped:
            # We hit dead execution engine imports at the end
            break
            
        if "def get_results_verification(" in line:
            line = line.replace("def get_results_verification(", "def get_results_verification_service(")
        elif "def run_investment_pipeline(" in line:
            line = line.replace("def run_investment_pipeline(", "def run_investment_pipeline_service(")
            
        service_body.append(line)
        i += 1
        
    service_code = [
        '"""',
        'Results & Pipeline Service.',
        '',
        'Provides business logic for result verification, evaluation against historical picks,',
        'and executing the investment pipeline.',
        '"""',
        '',
        'import math',
        'import re',
        'import json',
        'import logging',
        'import urllib.request',
        'import urllib.error',
        'from datetime import datetime, timezone',
        'from typing import Any, Optional',
        'from fastapi import BackgroundTasks, HTTPException',
        'from src.config import logger, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST',
        'from src.db.database import get_db',
        'from src.db.picks_repo import (',
        '    get_picks_by_date,',
        '    get_unsettled_picks,',
        '    settle_pick,',
        '    get_portfolio_summary,',
        '    get_league_pnl,',
        ')',
        'from src.engine.pipeline import run_pipeline as _run_pipeline',
        'from api.services.match_analysis_service import _compute_match_analysis',
        'from api.services.fixtures_service import (',
        '    _read_fixture_cache,',
        '    _fetch_sofascore_fixtures,',
        '    _sofascore_to_fixture,',
        '    _get_istanbul_today,',
        ')',
        '',
    ] + service_body + ['']
    
    Path("api/services/results_service.py").write_text("\n".join(service_code))
    print("Created api/services/results_service.py")
    
    router_code = [
        '"""',
        'Results & Pipeline Router.',
        '',
        'Handles endpoints for result verification and running the investment pipeline.',
        'Strictly delegates business logic to results_service.',
        '"""',
        '',
        'from fastapi import APIRouter, BackgroundTasks',
        'from api.services import results_service',
        '',
        'router = APIRouter(tags=["Results & Pipeline"])',
        '',
        '',
        '@router.get("/api/results/{date_str}")',
        'def get_results_verification(date_str: str, background_tasks: BackgroundTasks):',
        '    return results_service.get_results_verification_service(date_str, background_tasks)',
        '',
        '',
        '@router.get("/api/pipeline/run/{date_str}")',
        'def run_investment_pipeline(date_str: str):',
        '    return results_service.run_investment_pipeline_service(date_str)',
        '',
    ]
    Path("api/routers/results.py").write_text("\n".join(router_code))
    print("Created api/routers/results.py")
    
    # Now let's create scratch/update_main_for_results.py
    update_script = [
        'from pathlib import Path',
        '',
        'def update_main():',
        '    main_path = Path("api/main.py")',
        '    lines = main_path.read_text().splitlines()',
        '    ',
        '    start_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "# ── Results Verification ──" in line and start_idx == -1:',
        '            start_idx = i',
        '            break',
        '            ',
        '    end_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "PHASE 4: LIVE ADAPTIVE PIPELINE" in line and i > start_idx:',
        '            end_idx = i - 1',
        '            while end_idx > 0 and (lines[end_idx].strip() == "" or lines[end_idx].strip().startswith("#")):',
        '                end_idx -= 1',
        '            end_idx += 1',
        '            break',
        '            ',
        '    print(f"Removing lines {start_idx+1} to {end_idx}")',
        '    ',
        '    # Find where app.include_router(fixtures_router) is',
        '    import_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "app.include_router(fixtures_router)" in line:',
        '            import_idx = i + 1',
        '            break',
        '            ',
        '    new_imports = [',
        '        "",',
        '        "# Results Service & Router",',
        '        "from api.routers.results import router as results_router",',
        '        "app.include_router(results_router)",',
        '    ]',
        '    ',
        '    new_lines = []',
        '    for i, line in enumerate(lines):',
        '        if start_idx <= i < end_idx:',
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
    Path("scratch/update_main_for_results.py").write_text("\n".join(update_script))
    print("Created scratch/update_main_for_results.py")

if __name__ == "__main__":
    build_results_service()
