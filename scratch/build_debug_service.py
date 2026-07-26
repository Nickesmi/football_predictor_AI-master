import re
from pathlib import Path

def build_debug_service():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # Locate # ── Endpoints ── (around line 128)
    start_idx = -1
    for i, line in enumerate(lines):
        if "# ── Endpoints ──" in line and start_idx == -1:
            start_idx = i
            break
            
    print(f"Debug section starts at line {start_idx+1}")
    section_lines = lines[start_idx:]
    
    service_lines = []
    router_lines = [
        '"""',
        'Debug & Admin Router.',
        '',
        'Exposes endpoints for system diagnostics, calibration inspection, provider health,',
        'and data warehouse validation.',
        'Strictly delegates business logic to debug_service.',
        '"""',
        '',
        'from typing import Optional',
        'from fastapi import APIRouter, Depends',
        'from api.dependencies import verify_admin_key',
        'from api.services import debug_service',
        '',
        'router = APIRouter(tags=["Debug & Admin"])',
        '',
    ]
    
    i = 0
    while i < len(section_lines):
        line = section_lines[i]
        stripped = line.strip()
        
        if stripped.startswith("debug_router = APIRouter(") or \
           stripped == "from fastapi import APIRouter" or \
           stripped == "app.include_router(debug_router)":
            i += 1
            continue
            
        if stripped.startswith("@app.get(") or stripped.startswith("@debug_router.get("):
            decorator = line
            if stripped.startswith("@debug_router.get("):
                path_match = re.search(r'@debug_router\.get\("([^"]+)"', stripped)
                if path_match:
                    subpath = path_match.group(1)
                    if not subpath.startswith("/api/debug"):
                        subpath = "/api/debug" + subpath
                    decorator = f'@router.get("{subpath}")'
            else:
                decorator = line.replace("@app.get(", "@router.get(")
                
            i += 1
            while i < len(section_lines) and section_lines[i].strip() == "":
                i += 1
                
            # Collect def lines (handling multi-line definitions)
            def_lines = []
            while i < len(section_lines):
                def_lines.append(section_lines[i])
                if section_lines[i].rstrip().endswith(":") and ("def " in def_lines[0] or len(def_lines) > 1):
                    full_tmp = " ".join(l.strip() for l in def_lines)
                    if full_tmp.count("(") <= full_tmp.count(")") and full_tmp.endswith(":"):
                        break
                i += 1
                
            full_def_str = " ".join(l.strip() for l in def_lines)
            m = re.match(r'\s*def\s+([a-zA-Z0-9_]+)\s*\((.*?)\)\s*(?:->\s*.*?)?:', full_def_str)
            if not m:
                print(f"ERROR: Could not parse def lines: {def_lines}")
                i += 1
                continue
                
            func_name = m.group(1)
            args_str = m.group(2)
            
            arg_names = []
            if args_str.strip():
                parts = args_str.split(",")
                for p in parts:
                    p_strip = p.strip()
                    if not p_strip:
                        continue
                    arg_name = re.split(r'[:=\s]', p_strip)[0]
                    if arg_name and arg_name not in ("self",):
                        arg_names.append(arg_name)
                        
            call_args = ", ".join(f"{name}={name}" for name in arg_names)
            
            router_lines.append("")
            router_lines.append(decorator)
            for dl in def_lines:
                router_lines.append(dl)
            router_lines.append(f"    return debug_service.{func_name}_service({call_args})")
            
            service_lines.append("")
            for idx, dl in enumerate(def_lines):
                if idx == 0:
                    service_lines.append(dl.replace(f"def {func_name}(", f"def {func_name}_service("))
                else:
                    service_lines.append(dl)
            
            i += 1
            continue
            
        service_lines.append(line)
        i += 1
        
    service_code = [
        '"""',
        'Debug & Admin Service.',
        '',
        'Provides business logic for all debug, diagnostic, and admin reporting endpoints.',
        '"""',
        '',
        'import json',
        'import math',
        'import os',
        'import re',
        'import ssl',
        'import certifi',
        'import time',
        'import unicodedata',
        'import urllib.request',
        'import asyncio',
        'import requests',
        'from datetime import date, datetime, timedelta, timezone',
        'from pathlib import Path',
        'from typing import Optional, Union, Any',
        '',
        'from fastapi import HTTPException, BackgroundTasks, Depends',
        'from src.config import logger, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST, TOP_LEAGUES, ADMIN_API_KEY',
        'from src.db.database import get_db',
        'from src.processing.pattern_analyzer import PatternAnalyzer',
        'from src.processing.factor_analyzer import FactorAnalyzer',
        'from src.reporting.report_formatter import ReportFormatter',
        'from src.processing.value_detector import ValueDetector',
        'from src.ml.predictor import XGBoostPredictor',
        'from src.ml.poisson_model import PoissonGoalModel',
        'from src.ml.team_stats_db import get_team_stats',
        'from src.ml.feature_builder import TeamProfile',
        'from src.engine.odds_scanner import scan_live_odds',
        'from src.db.competition_tracker import upsert_competition, get_competition_stats, list_competitions',
        '',
        'from api.services.match_analysis_service import _compute_match_analysis, _ANALYSIS_CACHE',
        'from api.services.fixtures_service import (',
        '    _read_fixture_cache,',
        '    _write_fixture_cache,',
        '    _fetch_api_football_fixtures,',
        '    _fetch_sofascore_fixtures,',
        '    _get_istanbul_today,',
        '    _PREDICTION_STATUS,',
        '    _precompute_predictions_for_date,',
        '    _categorize_competition,',
        '    _sofascore_to_fixture,',
        ')',
        'from api.services.logo_service import resolve_team_logo',
        '',
    ] + service_lines + ['']
    
    Path("api/services/debug_service.py").write_text("\n".join(service_code))
    print("Created api/services/debug_service.py")
    
    Path("api/routers/debug.py").write_text("\n".join(router_lines))
    print("Created api/routers/debug.py")
    
    update_script = [
        'from pathlib import Path',
        '',
        'def update_main():',
        '    main_path = Path("api/main.py")',
        '    lines = main_path.read_text().splitlines()',
        '    ',
        '    start_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "# ── Endpoints ──" in line and start_idx == -1:',
        '            start_idx = i',
        '            break',
        '            ',
        '    print(f"Removing lines {start_idx+1} to {len(lines)}")',
        '    ',
        '    sys_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "app.include_router(system_router)" in line:',
        '            sys_idx = i + 1',
        '            break',
        '            ',
        '    new_imports = [',
        '        "",',
        '        "# Debug & Admin Router",',
        '        "from api.routers.debug import router as debug_router",',
        '        "app.include_router(debug_router)",',
        '        "",',
        '    ]',
        '    ',
        '    new_lines = []',
        '    for i, line in enumerate(lines):',
        '        if i >= start_idx:',
        '            break',
        '            ',
        '        if "_FIXTURE_CACHE_DIR = Path(\\".cache\\")" in line or \\',
        '           "_PREDICTION_STATUS: dict[str, dict] = {}" in line or \\',
        '           "_ANALYSIS_CACHE: dict[str, dict] = {}" in line:',
        '            continue',
        '            ',
        '        new_lines.append(line)',
        '        if i == sys_idx - 1:',
        '            new_lines.extend(new_imports)',
        '            ',
        '    main_path.write_text("\\n".join(new_lines) + "\\n")',
        '    print(f"Updated api/main.py. New line count: {len(new_lines)}")',
        '',
        'if __name__ == "__main__":',
        '    update_main()',
    ]
    Path("scratch/update_main_for_debug.py").write_text("\n".join(update_script))
    print("Created scratch/update_main_for_debug.py")

if __name__ == "__main__":
    build_debug_service()
