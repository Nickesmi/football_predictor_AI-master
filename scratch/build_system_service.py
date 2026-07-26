from pathlib import Path

def build_system_service():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # Locate ConnectionManager class (around line 66)
    cm_idx = -1
    for i, line in enumerate(lines):
        if "class ConnectionManager:" in line and cm_idx == -1:
            cm_idx = i
            break
            
    # Locate end of nightly retrain loop (before # Include routers)
    retrain_end_idx = -1
    for i, line in enumerate(lines):
        if "# Include routers" in line and i > cm_idx:
            retrain_end_idx = i - 1
            while retrain_end_idx > 0 and lines[retrain_end_idx].strip() == "":
                retrain_end_idx -= 1
            retrain_end_idx += 1
            break
            
    print(f"ConnectionManager & Background Tasks: lines {cm_idx+1} to {retrain_end_idx}")
    cm_lines = lines[cm_idx:retrain_end_idx]
    
    # Locate websocket and health check endpoints (around line 205)
    ws_idx = -1
    for i, line in enumerate(lines):
        if '@app.websocket("/ws/live-scores")' in line and ws_idx == -1:
            # check if previous line is from fastapi.responses import Response
            if i > 0 and "from fastapi.responses import Response" in lines[i-1]:
                ws_idx = i - 1
            else:
                ws_idx = i
            break
            
    health_end_idx = -1
    for i, line in enumerate(lines):
        if 'def health_check():' in line and i >= ws_idx:
            # find end of health_check function
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or lines[j].strip() == ""):
                if lines[j].strip().startswith("@app."):
                    break
                j += 1
            health_end_idx = j
            break
            
    print(f"WebSocket & Health Endpoints: lines {ws_idx+1} to {health_end_idx}")
    
    # Let's build system_service.py
    # In cm_lines, we need to:
    # 1. Remove @app.on_event("startup")
    # 2. Add health_check_service() -> dict
    
    service_body = []
    for line in cm_lines:
        if '@app.on_event("startup")' in line:
            continue
        service_body.append(line)
        
    service_code = [
        '"""',
        'System Service (WebSockets, Background Tasks, Health).',
        '',
        'Manages real-time WebSocket connections, background tasks (live score broadcast and nightly retrain),',
        'and system health diagnostics.',
        '"""',
        '',
        'import time',
        'import asyncio',
        'import logging',
        'from datetime import datetime, timedelta, timezone',
        'from pathlib import Path',
        'from typing import Any',
        'from fastapi import WebSocket, WebSocketDisconnect',
        'from src.config import logger, APIFOOTBALL_API_KEY, TOP_LEAGUES',
        '',
    ] + service_body + [
        '',
        'def health_check_service() -> dict:',
        '    """Return system health diagnostic information."""',
        '    return {',
        '        "status": "ok",',
        '        "data_source": "sofascore",',
        '        "analysis_mode": "live" if APIFOOTBALL_API_KEY else "per_match_poisson",',
        '        "engine": "Hybrid Poisson Goals + Corners + Cards v5.0",',
        '        "leagues": list(TOP_LEAGUES.values()),',
        '    }',
        '',
    ]
    
    Path("api/services/system_service.py").write_text("\n".join(service_code))
    print("Created api/services/system_service.py")
    
    router_code = [
        '"""',
        'System Router.',
        '',
        'Handles system health endpoints and live score WebSocket connections.',
        'Strictly delegates logic to system_service.',
        '"""',
        '',
        'from fastapi import APIRouter, WebSocket, WebSocketDisconnect',
        'from api.services import system_service',
        'from api.services.system_service import manager',
        '',
        'router = APIRouter(tags=["System & WebSockets"])',
        '',
        '',
        '@router.websocket("/ws/live-scores")',
        'async def websocket_live_scores(websocket: WebSocket):',
        '    await manager.connect(websocket)',
        '    try:',
        '        while True:',
        '            # Keep connection alive, wait for client to disconnect',
        '            data = await websocket.receive_text()',
        '    except WebSocketDisconnect:',
        '        manager.disconnect(websocket)',
        '',
        '',
        '@router.get("/api/health")',
        'def health_check():',
        '    return system_service.health_check_service()',
        '',
    ]
    Path("api/routers/system.py").write_text("\n".join(router_code))
    print("Created api/routers/system.py")
    
    # Now build scratch/update_main_for_system.py
    update_script = [
        'from pathlib import Path',
        '',
        'def update_main():',
        '    main_path = Path("api/main.py")',
        '    lines = main_path.read_text().splitlines()',
        '    ',
        '    # Locate Instantiate App',
        '    inst_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "# ── Instantiate App ──" in line and inst_idx == -1:',
        '            inst_idx = i',
        '            break',
        '            ',
        '    # Locate Include routers',
        '    inc_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "# Include routers" in line and i > inst_idx:',
        '            inc_idx = i',
        '            break',
        '            ',
        '    # Locate websocket and health',
        '    ws_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if \'@app.websocket("/ws/live-scores")\' in line and ws_idx == -1:',
        '            if i > 0 and "from fastapi.responses import Response" in lines[i-1]:',
        '                ws_idx = i - 1',
        '            else:',
        '                ws_idx = i',
        '            break',
        '            ',
        '    health_end_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if \'def health_check():\' in line and i >= ws_idx:',
        '            j = i + 1',
        '            while j < len(lines) and (lines[j].startswith("    ") or lines[j].strip() == ""):',
        '                if lines[j].strip().startswith("@app."):',
        '                    break',
        '                j += 1',
        '            health_end_idx = j',
        '            break',
        '            ',
        '    print(f"Replacing Instantiate App block: {inst_idx+1} to {inc_idx}")',
        '    print(f"Removing WebSocket & Health block: {ws_idx+1} to {health_end_idx}")',
        '    ',
        '    # Find where app.include_router(results_router) is',
        '    res_idx = -1',
        '    for i, line in enumerate(lines):',
        '        if "app.include_router(results_router)" in line:',
        '            res_idx = i + 1',
        '            break',
        '            ',
        '    new_app_block = [',
        '        "# ── Lifespan & App Setup ─────────────────────────────────────────────────────",',
        '        "from contextlib import asynccontextmanager",',
        '        "",',
        '        "@asynccontextmanager",',
        '        "async def lifespan(app: FastAPI):",',
        '        "    from api.services.system_service import start_background_tasks",',
        '        "    await start_background_tasks()",',
        '        "    yield",',
        '        "",',
        '        "# ── Instantiate App ──────────────────────────────────────────────────────────",',
        '        \'app = FastAPI(title="Football Predictor AI API", version="5.0", lifespan=lifespan)\',',
        '        "",',
        '        "app.add_middleware(",',
        '        "    CORSMiddleware,",',
        '        \'    allow_origins=["*"],\',',
        '        "    allow_credentials=True,",',
        '        \'    allow_methods=["*"],\',',
        '        \'    allow_headers=["*"],\',',
        '        ")",',
        '        "",',
        '    ]',
        '    ',
        '    new_imports = [',
        '        "",',
        '        "# System Service & Router",',
        '        "from api.routers.system import router as system_router",',
        '        "app.include_router(system_router)",',
        '    ]',
        '    ',
        '    new_lines = []',
        '    for i, line in enumerate(lines):',
        '        if inst_idx <= i < inc_idx:',
        '            if i == inst_idx:',
        '                new_lines.extend(new_app_block)',
        '            continue',
        '            ',
        '        if ws_idx <= i < health_end_idx:',
        '            continue',
        '            ',
        '        new_lines.append(line)',
        '        if i == res_idx - 1:',
        '            new_lines.extend(new_imports)',
        '            ',
        '    main_path.write_text("\\n".join(new_lines) + "\\n")',
        '    print(f"Updated api/main.py. New line count: {len(new_lines)}")',
        '',
        'if __name__ == "__main__":',
        '    update_main()',
    ]
    Path("scratch/update_main_for_system.py").write_text("\n".join(update_script))
    print("Created scratch/update_main_for_system.py")

if __name__ == "__main__":
    build_system_service()
