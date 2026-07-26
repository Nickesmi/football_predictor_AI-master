from pathlib import Path

def update_main():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    start_idx = -1
    for i, line in enumerate(lines):
        if "# ── Endpoints ──" in line and start_idx == -1:
            start_idx = i
            break
            
    print(f"Removing lines {start_idx+1} to {len(lines)}")
    
    sys_idx = -1
    for i, line in enumerate(lines):
        if "app.include_router(system_router)" in line:
            sys_idx = i + 1
            break
            
    new_imports = [
        "",
        "# Debug & Admin Router",
        "from api.routers.debug import router as debug_router",
        "app.include_router(debug_router)",
        "",
    ]
    
    new_lines = []
    for i, line in enumerate(lines):
        if i >= start_idx:
            break
            
        if "_FIXTURE_CACHE_DIR = Path(\".cache\")" in line or \
           "_PREDICTION_STATUS: dict[str, dict] = {}" in line or \
           "_ANALYSIS_CACHE: dict[str, dict] = {}" in line:
            continue
            
        new_lines.append(line)
        if i == sys_idx - 1:
            new_lines.extend(new_imports)
            
    main_path.write_text("\n".join(new_lines) + "\n")
    print(f"Updated api/main.py. New line count: {len(new_lines)}")

if __name__ == "__main__":
    update_main()