"""M3.2: reviewed team-identity registry and fallback-statistics regressions."""
import json

import pytest

from src.identity import registry
from src.identity.competitions import competition_for_fixture
from src.identity.inputs import UnresolvedTeamIdentity, resolve_probability_inputs_v2
from src.identity.teams import (AMBIGUOUS, CONTAMINATED, NO_HISTORY, RESOLVED, STALE_MAPPING, canonical_team_id,
                                resolve_team)

LALIGA = competition_for_fixture("api_football", 140)
EPL = competition_for_fixture("api_football", 39)


def _state(conn, name, league, played):
    conn.execute("INSERT INTO team_state (team_name, team_name_lower, league, venue, rolling_scored, "
                 "rolling_conceded, matches_played, form_last5) VALUES (?, ?, ?, 'overall', 1.4, 1.1, ?, 0.5)",
                 (name, name.lower(), league, played))


def _fixture(conn, fid, home, label, league_id):
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                 "VALUES (?, '2026-09-01', ?, 'Opp', ?, ?, 'FT', 'api_football')", (fid, home, label, league_id))


def _atletico(conn):
    _state(conn, "Atletico Madrid", "La Liga", 4)
    _state(conn, "Atlético Madrid", "LaLiga", 1)
    _fixture(conn, "f1", "Atletico Madrid", "La Liga", 140)


def _registry(tmp_path, status="approved", **over):
    entry = {"id": "ESP-ATLETICO", "competition_key": "spain-la-liga", "provider": "api_football",
             "provider_team_name": "Atletico Madrid", "provider_team_id": None, "decision": "map",
             "team_state": {"team_name_lower": "atletico madrid", "history_league": "La Liga"},
             "evidence": {}, "reason": "test", "review": {"status": status}}
    entry.update(over)
    path = tmp_path / "reg.json"
    path.write_text(json.dumps({"version": "test-registry", "entries": [entry]}))
    return path


class TestShippedRegistry:
    def test_every_entry_is_a_reviewed_proposal_with_evidence(self):
        data = json.loads(registry.REGISTRY_FILE.read_text())
        assert data["version"] == "team-registry-v1" and len(data["entries"]) == 31
        for e in data["entries"]:
            # M3.3: approved under the owner-delegated evidence criteria, owner confirmation pending
            assert e["review"]["status"] == "approved" and e["review"]["owner_confirmation"] == "pending"
            assert e["review"]["approval_basis"] and e["audit_m3_3"]["rh1_status"] == "RESOLVED"
            assert e["evidence"] and e["reason"] and e["decision"] in ("map", "exclude")
            if e["decision"] == "map":
                assert e["audit_m3_3"]["team_state_v1_with_entry"] == "RESOLVED"
            if e["decision"] == "map":
                assert set(e["team_state"]) == {"team_name_lower", "history_league"}
        assert sum(e["decision"] == "map" for e in data["entries"]) == 18

    def test_no_unverified_alias_is_mapped(self):
        data = json.loads(registry.REGISTRY_FILE.read_text())
        excluded = {e["provider_team_name"] for e in data["entries"] if e["decision"] == "exclude"}
        assert {"Lens", "Monaco", "Strasbourg", "Marseille", "Stade Brestois 29", "Rangers", "Dundee Utd",
                "Arsenal"} <= excluded

    def test_approved_entries_are_active_by_default(self, engine_db):
        _atletico(engine_db)
        r = resolve_team(engine_db, LALIGA, "Atletico Madrid")
        assert (r.status, r.source) == (RESOLVED, "registry:ESP-ATLETICO")


class TestRegistryBehaviour:
    def test_approved_map_resolves_with_verification_and_ids(self, engine_db, tmp_path):
        _atletico(engine_db)
        reg = registry.load(_registry(tmp_path))
        r = resolve_team(engine_db, LALIGA, "Atletico Madrid", "api_football", 530, remember=True, registry=reg)
        assert (r.status, r.source, r.history_league) == (RESOLVED, "registry:ESP-ATLETICO", "La Liga")
        assert r.canonical_team_id == canonical_team_id("atletico madrid", "La Liga")
        assert r.competition_ids == {"api_football": 140, "sofascore": 8}
        row = engine_db.execute("SELECT source, mapping_version FROM team_identity_map").fetchone()
        assert tuple(row) == ("registry:ESP-ATLETICO", "test-registry")

    def test_proposed_only_with_allow_proposed(self, engine_db, tmp_path):
        _atletico(engine_db)
        path = _registry(tmp_path, status="proposed")
        assert resolve_team(engine_db, LALIGA, "Atletico Madrid", registry=registry.load(path)).status == AMBIGUOUS
        assert resolve_team(engine_db, LALIGA, "Atletico Madrid",
                            registry=registry.load(path, allow_proposed=True)).status == RESOLVED

    def test_mapped_record_must_still_verify(self, engine_db, tmp_path):
        _state(engine_db, "Atletico Madrid", "La Liga", 4)
        _state(engine_db, "Atlético Madrid", "LaLiga", 1)
        _fixture(engine_db, "f1", "Atletico Madrid", "La Liga", 140)
        _fixture(engine_db, "f2", "Atletico Madrid", "La Liga", 999)   # contamination appears later
        r = resolve_team(engine_db, LALIGA, "Atletico Madrid", registry=registry.load(_registry(tmp_path)))
        assert r.status == CONTAMINATED

    def test_provider_id_binding_mismatch_fails_closed(self, engine_db, tmp_path):
        _atletico(engine_db)
        reg = registry.load(_registry(tmp_path, provider_team_id="530"))
        assert resolve_team(engine_db, LALIGA, "Atletico Madrid", "api_football", 531, registry=reg).status == AMBIGUOUS
        assert resolve_team(engine_db, LALIGA, "Atletico Madrid", "api_football", 530, registry=reg).status == RESOLVED

    def test_exclude_keeps_team_fail_closed_with_reason(self, engine_db, tmp_path):
        reg = registry.load(_registry(tmp_path, provider_team_name="Lens", competition_key="france-ligue-1",
                                      decision="exclude", reason="unverified alias"))
        r = resolve_team(engine_db, competition_for_fixture("api_football", 61), "Lens", registry=reg)
        assert r.status == NO_HISTORY and r.evidence["registry"]["decision"] == "exclude"

    def test_missing_record_is_stale(self, engine_db, tmp_path):
        _state(engine_db, "Atletico Madrid", "LaLiga", 1)
        _state(engine_db, "Atlético Madrid", "LaLiga", 1)
        r = resolve_team(engine_db, LALIGA, "Atletico Madrid", registry=registry.load(_registry(tmp_path)))
        assert r.status == STALE_MAPPING

    def test_duplicate_entries_are_rejected(self, tmp_path):
        path = tmp_path / "dup.json"
        e = {"id": "a", "competition_key": "x", "provider_team_name": "FC Ajax"}
        path.write_text(json.dumps({"version": "v", "entries": [e, {**e, "id": "b", "provider_team_name": "Ajax"}]}))
        with pytest.raises(ValueError, match="duplicate"):
            registry.load(path)


class TestFallbackStatisticsRegressions:
    def test_identity_inputs_never_use_static_tables_substring_or_hash(self, engine_db, monkeypatch):
        import src.ml.team_stats_db as tsd

        def boom(*a, **k):
            raise AssertionError("legacy team_stats_db lookup used")

        monkeypatch.setattr(tsd, "get_team_stats", boom)
        monkeypatch.setattr(tsd, "_lookup_in_db", boom)
        _state(engine_db, "Everton", "Premier League", 6)
        _state(engine_db, "Chelsea", "Premier League", 6)
        _fixture(engine_db, "e1", "Everton", "Premier League", 39)
        _fixture(engine_db, "c1", "Chelsea", "Premier League", 39)
        m = {"home_team": "Everton", "away_team": "Chelsea", "league_name": "Premier League", "league_id": 39,
             "provider": "api_football"}
        inputs = resolve_probability_inputs_v2(engine_db, m)
        assert inputs["home_stats"]["matches_played"] == 6
        with pytest.raises(UnresolvedTeamIdentity):  # unknown team: no hash statistics, no static table
            resolve_probability_inputs_v2(engine_db, {**m, "away_team": "Zzyzx United"})

    @pytest.mark.parametrize("name, wrong", [("Rangers", "Angers"), ("Gent", "Argentina"), ("Lillestrom", "Lille"),
                                             ("Inter Turku", "Inter"), ("Malisheva", "Mali")])
    def test_no_cross_team_matching(self, engine_db, name, wrong):
        _state(engine_db, wrong, "Premier League", 10)
        _fixture(engine_db, f"w-{wrong}", wrong, "Premier League", 39)
        assert resolve_team(engine_db, EPL, name).status == NO_HISTORY

    def test_contaminated_label_is_never_used(self, engine_db):
        _state(engine_db, "Arsenal", "Premier League", 9)
        for i in range(7):
            _fixture(engine_db, f"by{i}", "Arsenal", "Premier League", 116)
        _fixture(engine_db, "en1", "Arsenal", "Premier League", 39)
        r = resolve_team(engine_db, EPL, "Arsenal")
        assert r.status == CONTAMINATED and r.canonical_team_id is None


def test_identity_inputs_never_use_the_like_substring_fallback(engine_db):
    """get_team_state would return 'Internacional' (Brazil) for 'Inter' under the shared 'Serie A' label."""
    from src.db.team_state import get_team_state

    _state(engine_db, "Internacional", "Serie A", 11)
    _fixture(engine_db, "br1", "Internacional", "Serie A", 71)
    assert get_team_state(engine_db, "Inter", "Serie A", "overall").team_name == "Internacional"  # legacy hazard
    r = resolve_team(engine_db, competition_for_fixture("api_football", 135), "Inter")
    assert r.status == NO_HISTORY
