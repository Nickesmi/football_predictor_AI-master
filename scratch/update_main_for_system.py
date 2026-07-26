from pathlib import Path

def update_main():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # Locate Instantiate App
    inst_idx = -1
    for i, line in enumerate(lines):
        if "# ── Instantiate App ──" in line and inst_idx == -1:
            inst_idx = i
            break
            
    # Locate Include routers
    inc_idx = -1
    for i, line in enumerate(lines):
        if "# Include routers" in line and i > inst_idx:
            inc_idx = i
            break
            
    # Locate websocket and health
    ws_idx = -1
    for i, line in enumerate(lines):
        if '@app.websocket("/ws/live-scores")' in line and ws_idx == -1:
            if i > 0 and "from fastapi.responses import Response" in lines[i-1]:
                ws_idx = i - 1
            else:
                ws_idx = i
            break
            
    health_end_idx = -1
    for i, line in enumerate(lines):
        if 'def health_check():' in line and i >= ws_idx:
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or lines[j].strip() == ""):
                if lines[j].strip().startswith("@app."):
                    break
                j += 1
            health_end_idx = j
            break
            
    print(f"Replacing Instantiate App block: {inst_idx+1} to {inc_idx}")
    print(f"Removing WebSocket & Health block: {ws_idx+1} to {health_end_idx}")
    
    # Find where app.include_router(results_router) is
    res_idx = -1
    for i, line in enumerate(lines):
        if "app.include_router(results_router)" in line:
            res_idx = i + 1
            break
            
    new_app_block = [
        "# ── Lifespan & App Setup ─────────────────────────────────────────────────────",
        "from contextlib import asynccontextmanager",
        "",
        "@asynccontextmanager",
        "async def lifespan(app: FastAPI):",
        "    from api.services.system_service import start_background_tasks",
        "    await start_background_tasks()",
        "    yield",
        "",
        "# ── Instantiate App ──────────────────────────────────────────────────────────",
        'app = FastAPI(title="Football Predictor AI API", version="5.0", lifespan=lifespan)',
        "",
        "app.add_middleware(",
        "    CORSMiddleware,",
        '    allow_origins=["*"],',
        "    allow_credentials=True,",
        '    allow_methods=["*"],',
        '    allow_headers=["*"],',
        ")",
        "",
    ]
    
    new_imports = [
        "",
        "# System Service & Router",
        "from api.routers.system import router as system_router",
        "app.include_router(system_router)",
    ]
    
    new_lines = []
    for i, line in enumerate(lines):
        if inst_idx <= i < inc_idx:
            if i == inst_idx:
                new_lines.extend(new_app_block)
            continue
            
        if ws_idx <= i < health_end_idx:
            continue
            
        new_lines.append(line)
        if i == res_idx - 1:
            new_lines.extend(new_imports)
            
    main_path.write_text("\n".join(new_lines) + "\n")
    print(f"Updated api/main.py. New line count: {len(new_lines)}")

if __name__ == "__main__":
    update_main()