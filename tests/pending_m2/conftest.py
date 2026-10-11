"""Tests for governed behaviour changes deferred to milestone M2.

They assert the FUTURE behaviour (synthetic-XGB weight 0; hash-fallback teams
report matches_played=0) and therefore fail against current production code.
They are excluded from the default run and executed explicitly with:

    RUN_PENDING_M2=1 python -m pytest tests/pending_m2 -q

The CURRENT behaviour is pinned by tests/golden, so nothing is unguarded in
the meantime. When M2 lands: move these tests back into the main suite,
delete this directory, and regenerate the golden file in the same commit.
"""
import os

if os.getenv("RUN_PENDING_M2") != "1":
    collect_ignore_glob = ["test_*.py"]
