"""Guards against committing credentials or private data."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)

SENSITIVE_PATHS = [
    ".env", ".env.bak", ".env.local", ".env.production", "frontend/.env.local",
    ".streamlit/secrets.toml", "server.pem", "deploy.key", "cert.p12",
    "backups/engine.db", "data/engine.db", "data/engine.db-wal", "data/real_historical/predictions.db",
    "notes.sqlite", "PROJECT_MASTER_REPORT.pdf", ".venv311/bin/python",
]

# Assignments of a credential-like name to a long literal value.
SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b[A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*['"]?[ \t]*"""
    r"""(?:[:=][ \t]*['"][A-Za-z0-9_\-]{20,}['"]|=[A-Za-z0-9_\-]{20,})"""
)
PLACEHOLDER = re.compile(r"(?i)your_|example|placeholder|changeme|xxx")


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("path", SENSITIVE_PATHS)
def test_sensitive_paths_are_ignored(path):
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    assert result.returncode == 0, f"{path} is not git-ignored"


def test_env_example_stays_tracked():
    assert ".env.example" in _git("ls-files", ".env.example").split()


def test_no_tracked_file_contains_a_literal_credential():
    hits = []
    for rel in _git("ls-files").splitlines():
        path = ROOT / rel
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in SECRET_ASSIGNMENT.finditer(text):
            if not PLACEHOLDER.search(match.group(0)):
                hits.append(f"{rel}: {match.group(0)[:40]}...")
    assert hits == []
