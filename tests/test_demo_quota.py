"""Quota-controlled demo: hard cap, persistence, uncounted-path block, no background tasks (subprocess-isolated)."""
import json
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _py(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT)}, timeout=120)


GUARD = """
import http.client, socket, sys
from pathlib import Path
from src.ops import demo_quota
demo_quota.install(Path(sys.argv[0] if False else {db!r}), {cap})
"""


def test_cap_counts_before_sending_and_persists_across_restarts(tmp_path):
    db = str(tmp_path / "usage.db")
    run = GUARD.format(db=db, cap=3) + """
ok = refused = 0
for i in range(4):
    c = http.client.HTTPSConnection("v3.football.api-sports.io")
    try:
        c.putrequest("GET", f"/status?i={i}")   # counted before anything is sent; no connection opened
        ok += 1
    except demo_quota.DemoQuotaExceeded:
        refused += 1
print(ok, refused, demo_quota.used())
"""
    first = _py(run, tmp_path)
    assert first.stdout.split() == ["3", "1", "3"], first.stderr
    second = _py(run, tmp_path)                      # a "restart": same usage file
    assert second.stdout.split() == ["0", "4", "3"], second.stderr
    rows = sqlite3.connect(db).execute("SELECT allowed, COUNT(*) FROM demo_api_usage GROUP BY 1").fetchall()
    assert dict(rows) == {0: 5, 1: 3}


def test_uncounted_paths_cannot_reach_api_football(tmp_path):
    run = GUARD.format(db=str(tmp_path / "u.db"), cap=5) + """
try:
    socket.getaddrinfo("v3.football.api-sports.io", 443)
    print("LEAKED")
except socket.gaierror as e:
    print("BLOCKED")
c = http.client.HTTPConnection("example.invalid")       # other hosts are not counted
c.putrequest("GET", "/")
print(demo_quota.used())
"""
    r = _py(run, tmp_path)
    assert r.stdout.split() == ["BLOCKED", "0"], r.stderr


def test_logo_cdn_is_not_counted(tmp_path):
    run = GUARD.format(db=str(tmp_path / "u.db"), cap=0) + """
c = http.client.HTTPSConnection("media.api-sports.io")
c.putrequest("GET", "/football/teams/1.png")
print(demo_quota.used())
"""
    r = _py(run, tmp_path)
    assert r.stdout.strip() == "0", r.stderr


def test_cap_above_five_is_refused(tmp_path):
    r = _py(GUARD.format(db=str(tmp_path / "u.db"), cap=6), tmp_path)
    assert r.returncode != 0 and "between 0 and 5" in r.stderr


def test_launcher_refuses_the_production_database(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_server.py"), "--db",
                        str(ROOT / "data" / "engine.db"), "--workdir", str(tmp_path / "w")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "never uses data/engine.db" in r.stderr


def test_each_date_is_fetched_at_most_once(tmp_path):
    db = tmp_path / "copy.db"
    sqlite3.connect(db).close()
    code = f"""
import sys
sys.argv = ["x"]
sys.path.insert(0, {str(ROOT)!r})
import importlib.util
spec = importlib.util.spec_from_file_location("demo", {str(ROOT / 'scripts' / 'demo_server.py')!r})
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)
import api.services.fixtures_service as fs
calls = []
fs._fetch_api_football_fixtures = lambda d: calls.append(d) or []
demo.prepare(__import__("pathlib").Path({str(db)!r}), __import__("pathlib").Path({str(tmp_path / 'w')!r}), 5)
for d in ("2026-10-18", "2026-10-18", "2026-10-19", "2026-10-18"):
    fs._fetch_api_football_fixtures(d)
import asyncio, api.services.system_service as ss
asyncio.run(ss.start_background_tasks())
print(calls)
"""
    r = _py(code, tmp_path)
    assert r.stdout.strip().splitlines()[-1] == "['2026-10-18', '2026-10-19']", r.stdout + r.stderr


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_end_to_end_demo_server_with_cap_zero_spends_nothing(tmp_path):
    """Real server, real fixture endpoint, cap 0: the API request is counted as refused and never sent."""
    db = tmp_path / "copy.db"
    from src.db.database import init_db

    conn = sqlite3.connect(db)
    init_db(conn)
    conn.close()
    port = _free_port()
    work = tmp_path / "w"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "API_FOOTBALL_KEY": "dummy-key-for-test",
           "APIFOOTBALL_API_KEY": "dummy-key-for-test"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "demo_server.py"), "--db", str(db), "--workdir",
                             str(work), "--port", str(port), "--cap", "0"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/fixtures/2030-01-04", timeout=60).read()
        assert isinstance(json.loads(body), (list, dict))
    finally:
        proc.terminate()
        out = proc.communicate(timeout=30)[0]
    rows = sqlite3.connect(work / "demo_api_usage.db").execute(
        "SELECT allowed, COUNT(*) FROM demo_api_usage GROUP BY 1").fetchall()
    assert dict(rows).get(1, 0) == 0                      # nothing sent
    assert dict(rows).get(0, 0) >= 1                      # the attempt was counted and refused
    assert "background tasks disabled" in out
    assert (work / ".cache").is_dir()                     # the demo's own cache, not the repository's
    assert not (ROOT / ".cache" / "fixtures-2030-01-04.json").exists()
