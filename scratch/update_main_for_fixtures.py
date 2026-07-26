from pathlib import Path

def update_main():
    main_path = Path("api/main.py")
    lines = main_path.read_text().splitlines()
    
    # We need to remove Block 1 (from `# ─── Fixture cache` up to `# ── Endpoints`)
    # and Block 2 (from `@app.get("/api/fixtures/today")` up to `return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})`)
    
    start_b1 = -1
    end_b1 = -1
    for i, line in enumerate(lines):
        if "# ─── Fixture cache ─────────────────────────────────────────────────────────" in line and start_b1 == -1:
            start_b1 = i
        if "# ── Endpoints ──────────────────────────" in line and start_b1 != -1 and end_b1 == -1:
            end_b1 = i
            break
            
    start_b2 = -1
    end_b2 = -1
    for i, line in enumerate(lines):
        if '@app.get("/api/fixtures/today")' in line and start_b2 == -1:
            start_b2 = i
        if 'return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})' in line and start_b2 != -1:
            end_b2 = i + 1
            break
            
    print(f"Removing Block 1: {start_b1+1} to {end_b1}")
    print(f"Removing Block 2: {start_b2+1} to {end_b2}")
    
    # Also in import section (around line 180), add imports from fixtures_service and include router
    # Let's find where app.include_router(analysis_router) is
    import_idx = -1
    for i, line in enumerate(lines):
        if "app.include_router(analysis_router)" in line:
            import_idx = i + 1
            break
            
    new_imports = [
        "",
        "# Fixtures Service & Router",
        "from api.services.fixtures_service import (",
        "    _read_fixture_cache,",
        "    _write_fixture_cache,",
        "    _fetch_api_football_fixtures,",
        "    _fetch_sofascore_fixtures,",
        "    _get_istanbul_today,",
        "    _PREDICTION_STATUS,",
        "    _precompute_predictions_for_date,",
        "    _categorize_competition,",
        "    _sofascore_to_fixture,",
        ")",
        "from api.routers.fixtures import router as fixtures_router",
        "app.include_router(fixtures_router)",
    ]
    
    # Construct new lines: up to start_b1, then skip to end_b1, then up to start_b2, then skip to end_b2
    # Note: we also insert new_imports after import_idx.
    # Let's do it carefully by line index.
    
    new_lines = []
    for i, line in enumerate(lines):
        if start_b1 <= i < end_b1:
            continue
        if start_b2 <= i < end_b2:
            continue
        new_lines.append(line)
        if i == import_idx - 1:
            new_lines.extend(new_imports)
            
    main_path.write_text("\n".join(new_lines) + "\n")
    print(f"Updated api/main.py. New line count: {len(new_lines)}")

if __name__ == "__main__":
    update_main()