"""
desktop.py — Production-grade desktop launcher for Football Predictor AI

Starts FastAPI + React as a native desktop window using PyWebView.
Works both in development and as a frozen PyInstaller executable.
"""

import os
import sys
import threading
import time
import socket
import urllib.request
import logging

# ── Logging setup (before any imports that use it) ───────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("desktop")


def get_base_path() -> str:
    """Return the root path of the app — works both frozen and dev."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def is_port_free(port: int) -> bool:
    """Check if a port is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def find_free_port(start: int = 8000, end: int = 8100) -> int:
    """Find first free port in range."""
    for port in range(start, end):
        if is_port_free(port):
            return port
    raise RuntimeError("No free ports available in range 8000–8100")


def wait_for_server(url: str, timeout: int = 30) -> bool:
    """Poll until server is responding or timeout reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def start_server(port: int) -> None:
    """Start the FastAPI server (blocks — run in thread)."""
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=port,
        log_level="error",
    )


def start_scheduler() -> None:
    """Lightweight background scheduler for future cron tasks."""
    log.info("Background scheduler started.")
    while True:
        try:
            time.sleep(300)  # 5-minute heartbeat
            log.debug("Scheduler heartbeat.")
        except Exception as e:
            log.error(f"Scheduler error: {e}")
            time.sleep(60)


# ── Mount the pre-built React frontend on the FastAPI app ───────────────────
def _mount_frontend(port: int) -> None:
    """Mount the static React build onto FastAPI so PyWebView can load it."""
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from api.main import app

    frontend_dist = os.path.join(get_base_path(), "frontend", "dist")
    if not os.path.isdir(frontend_dist):
        log.warning(f"Frontend dist not found at {frontend_dist}. UI will be blank.")
        return

    # Serve index.html at /  (catch-all for React Router)
    @app.get("/", include_in_schema=False)
    def _root():
        return FileResponse(os.path.join(frontend_dist, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        file_path = os.path.join(frontend_dist, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

    # Mount assets directory for JS/CSS/images
    assets_path = os.path.join(frontend_dist, "assets")
    if os.path.isdir(assets_path):
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    log.info(f"Frontend mounted from {frontend_dist}")


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import webview

    port = find_free_port()
    server_url = f"http://127.0.0.1:{port}"

    log.info(f"Starting Football Predictor AI on {server_url}")

    # Mount frontend before server starts
    _mount_frontend(port)

    # Start FastAPI in background thread
    server_thread = threading.Thread(
        target=lambda: start_server(port),
        daemon=True,
        name="fastapi-server",
    )
    server_thread.start()

    # Start scheduler in background thread
    scheduler_thread = threading.Thread(
        target=start_scheduler,
        daemon=True,
        name="scheduler",
    )
    scheduler_thread.start()

    # Wait until server is actually ready (max 30s)
    log.info("Waiting for server to be ready...")
    if not wait_for_server(f"{server_url}/api/health"):
        log.error("Server did not start in time. Exiting.")
        sys.exit(1)

    log.info("Server ready — launching window.")

    # Create native window
    window = webview.create_window(
        title="Football Predictor AI",
        url=server_url,
        width=1400,
        height=900,
        min_size=(1024, 700),
        resizable=True,
    )
    webview.start(debug=False)
