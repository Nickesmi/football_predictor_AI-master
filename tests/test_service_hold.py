"""M3 Phase E: the operator hold blocks the production database only."""
import json

import pytest

from src.ops import service_hold
from src.ops.service_hold import ServiceOnHold


@pytest.fixture
def fake_production(tmp_path, monkeypatch):
    db = tmp_path / "data" / "engine.db"
    db.parent.mkdir()
    monkeypatch.setattr(service_hold, "PRODUCTION_DB", db.resolve())
    monkeypatch.delenv(service_hold.RELEASE_ENV, raising=False)
    return db


def _hold(db, **data):
    (db.parent / service_hold.HOLD_FILENAME).write_text(json.dumps({"hold_id": "h-1", "reason": "m3", **data}))


def test_no_hold_allows_production(fake_production):
    service_hold.check(fake_production)


def test_hold_blocks_production(fake_production):
    _hold(fake_production)
    with pytest.raises(ServiceOnHold, match="on hold"):
        service_hold.check(fake_production)


def test_matching_release_token_allows_start(fake_production, monkeypatch):
    _hold(fake_production)
    monkeypatch.setenv(service_hold.RELEASE_ENV, "h-1")
    service_hold.check(fake_production)
    monkeypatch.setenv(service_hold.RELEASE_ENV, "wrong")
    with pytest.raises(ServiceOnHold):
        service_hold.check(fake_production)


def test_unreadable_hold_still_blocks(fake_production):
    (fake_production.parent / service_hold.HOLD_FILENAME).write_text("{not json")
    with pytest.raises(ServiceOnHold):
        service_hold.check(fake_production)


def test_other_databases_are_never_blocked(fake_production, tmp_path):
    _hold(fake_production)
    other = tmp_path / "copy" / "engine.db"
    other.parent.mkdir()
    (other.parent / service_hold.HOLD_FILENAME).write_text("{}")
    service_hold.check(other)


def test_get_db_checks_the_hold_before_opening(monkeypatch, tmp_path):
    from src.db import database

    calls = []
    monkeypatch.setattr(database, "_connection", None)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "x.db")
    monkeypatch.setattr(service_hold, "check", lambda p: (calls.append(p), (_ for _ in ()).throw(ServiceOnHold("held")))[1])
    with pytest.raises(ServiceOnHold):
        database.get_db()
    assert calls == [tmp_path / "x.db"] and not (tmp_path / "x.db").exists()
