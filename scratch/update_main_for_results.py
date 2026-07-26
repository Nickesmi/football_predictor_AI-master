from pathlib import Path

def update_main():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    start_idx = -1
    for i, line in enumerate(lines):
        if "# ── Results Verification ──" in line and start_idx == -1:
            start_idx = i
            break
            
    end_idx = -1
    for i, line in enumerate(lines):
        if "PHASE 4: LIVE ADAPTIVE PIPELINE" in line and i > start_idx:
            end_idx = i - 1
            while end_idx > 0 and (lines[end_idx].strip() == "" or lines[end_idx].strip().startswith("#")):
                end_idx -= 1
            end_idx += 1
            break
            
    print(f"Removing lines {start_idx+1} to {end_idx}")
    
    # Find where app.include_router(fixtures_router) is
    import_idx = -1
    for i, line in enumerate(lines):
        if "app.include_router(fixtures_router)" in line:
            import_idx = i + 1
            break
            
    new_imports = [
        "",
        "# Results Service & Router",
        "from api.routers.results import router as results_router",
        "app.include_router(results_router)",
    ]
    
    new_lines = []
    for i, line in enumerate(lines):
        if start_idx <= i < end_idx:
            continue
        new_lines.append(line)
        if i == import_idx - 1:
            new_lines.extend(new_imports)
            
    main_path.write_text("\n".join(new_lines) + "\n")
    print(f"Updated api/main.py. New line count: {len(new_lines)}")

if __name__ == "__main__":
    update_main()