"""M3.4.1 Task 3: shadow DB preparation never uses the production database and never overwrites a directory."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "prepare_shadow_db.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


def test_production_database_is_refused_as_source(tmp_path):
    r = _run("--source", str(ROOT / "data" / "engine.db"), "--out", str(tmp_path / "out"))
    assert r.returncode == 2 and "production database" in r.stderr
    assert not (tmp_path / "out").exists()


def test_non_empty_output_directory_is_refused(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("x")
    r = _run("--source", str(tmp_path / "candidate.db"), "--out", str(tmp_path / "out"))
    assert r.returncode == 2 and "not empty" in r.stderr
    assert (tmp_path / "out" / "keep.txt").read_text() == "x"
