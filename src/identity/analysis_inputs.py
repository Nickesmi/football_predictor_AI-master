"""Identity-checked team inputs for match analysis (2026-11-01; K43 option (b), demo only).

The legacy analysis lookup (src/ml/team_stats_db.get_team_stats) reads team history by the Poisson
profile key and the raw provider name, so it misses split labels ("La Liga" vs "LaLiga": Real Madrid's
stale 1-match "LaLiga" row instead of its current "La Liga" row), misses spellings ("Real Madrid CF"),
and substitutes other teams by substring ("Brisbane Roar FC W" -> the men's "Brisbane Roar").

This module resolves each side with team-identity-v1 (src/identity/teams.py: explicit mapping, then exact
normalised name inside the competition's own history labels, history provenance checked against the
competition's league ids, ambiguity never merged) and returns the resolved record's statistics with the
same venue transform as the probability inputs (src/identity/inputs._venue_stats). The competition is:
  * API-Football: the tracked competition for the league id, otherwise that league id with its own label;
  * football-data.org: the tracked competition for its competition code (no other leagues);
  * anything else: None, and the caller keeps its legacy behaviour.
An unresolved side means no forecast: the reason is reported and no statistics are substituted.

Cross-competition (2026-11-03): in a continental club competition a club with fewer than MIN_MATCHES verified
matches in that competition is read from its verified domestic-league record, linked by provider team id
(src/identity/club_membership.py). The Poisson profile, venue transform and models are unchanged.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from src.identity.club_membership import domestic_league, is_continental
from src.identity.competitions import COMPETITIONS, Competition, competition_for_fixture
from src.identity.inputs import _venue_stats
from src.identity.teams import TEAM_IDENTITY_VERSION, resolve_team

FOOTBALL_DATA_CODES = {"PL": "england-premier-league", "ELC": "england-championship", "PD": "spain-la-liga",
                       "SA": "italy-serie-a", "BL1": "germany-bundesliga", "FL1": "france-ligue-1",
                       "DED": "netherlands-eredivisie", "PPL": "portugal-primeira-liga",
                       "CL": "uefa-champions-league"}
MIN_MATCHES = 5
STALE_DAYS = 21


def competition_for(provider: str | None, league_id, league_name: str = "", country: str = "",
                    league_code: str | None = None) -> Competition | None:
    if provider == "api_football":
        tracked = competition_for_fixture("api_football", league_id)
        if tracked is not None:
            return tracked
        try:
            lid = int(league_id)
        except (TypeError, ValueError):
            return None
        # Any other league: its own provider id and label; provenance is still checked against that id.
        return Competition(f"api-football-{lid}", country or "", lid, -1, (league_name,)) if league_name else None
    if provider == "football_data":
        key = FOOTBALL_DATA_CODES.get(league_code or "")
        return next((c for c in COMPETITIONS if c.key == key), None)
    return None


def _last_match(conn, res) -> str | None:
    row = conn.execute("SELECT last_match_date FROM team_state WHERE team_name_lower = ? AND league = ? "
                       "AND venue = 'overall'", (res.team_name_lower, res.history_league)).fetchone()
    return row[0] if row else None


def _side(conn, competition: Competition, continental: bool, name: str, provider, tid, match_date, registry):
    """(selected resolution or None, own-competition resolution, domestic details or None).

    Own competition first (unchanged team-identity-v1). In a continental club competition, when the club has
    fewer than MIN_MATCHES verified matches there, its verified domestic-league record is used instead
    (found by provider team id, src/identity/club_membership.py), as the legacy UCL feature definition does
    (src/ml/team_stats_db.py: domestic statistics as the UCL base). The two records are never averaged."""
    own = resolve_team(conn, competition, name, provider, tid, False, registry)
    if not continental or (own.resolved and (own.matches_played or 0) >= MIN_MATCHES):
        return (own if own.resolved else None), own, None
    dl = domestic_league(conn, provider, tid, match_date)
    if not dl.found:
        return (own if own.resolved else None), own, {"status": "NOT_FOUND", "reason": dl.reason,
                                                      "evidence": dl.evidence}
    dom = resolve_team(conn, dl.competition, dl.team_name, provider, tid, False, registry)
    details = {"status": dom.status, "reason": dom.reason, "competition": dl.competition.key,
               "league_id": dl.competition.api_football_id, "provider_name": dl.team_name,
               "resolution": dom.to_dict(), "membership": dl.evidence}
    if dom.resolved and (not own.resolved or (dom.matches_played or 0) > (own.matches_played or 0)):
        return dom, own, details
    return (own if own.resolved else None), own, details


def _gap_days(last: str | None, match_date: str | None) -> int | None:
    if not last or not match_date:
        return None
    try:
        return (date.fromisoformat(match_date[:10]) - date.fromisoformat(last[:10])).days
    except ValueError:
        return None


def resolve_for_analysis(conn: sqlite3.Connection, *, provider: str | None, league_id, league_name: str,
                         country: str = "", league_code: str | None = None, home: str, away: str,
                         home_id=None, away_id=None, profile_key: str, match_date: str | None = None,
                         registry=None, db_key: str = "") -> dict | None:
    """None when the fixture's competition cannot be identified (legacy path). Otherwise
    {available, identity, home_stats, away_stats, blocking, warnings, basis, confidence, league_profile}.

    Each side (2026-11-10, src/identity/fallback_chain.py):
      * "record": its identity-resolved record has FULL_HISTORY+ matches and none on or after the kick-off
        date; the record is used as stored (the model path is unchanged), and corners/cards come from the
        chain unless the team has FULL_HISTORY+ results carrying them;
      * "chain": otherwise its own results before the kick-off date (all competitions, linked by provider team
        id) are blended with the first baseline level that has enough results (league, country, competition
        type, all results). A record that already contains the match (or later results) is never read; its
        rows before the kick-off date are.
    Unavailable only when no stored result exists at any level."""
    from src.identity import fallback_chain as fc
    from src.ml.poisson_model import LEAGUE_PROFILES
    from src.ml.team_stats_db import TeamVenueStats, is_neutral_tournament

    competition = competition_for(provider, league_id, league_name, country, league_code)
    if competition is None:
        return None
    continental = is_continental(competition, country)
    before = (match_date or date.today().isoformat())[:10]
    idx = fc.history_index(conn, db_key)
    ctype = fc.classify(league_name, country or competition.country)
    league_keys = [(p, i) for p, i in (("api_football", competition.api_football_id),
                                       ("sofascore", competition.sofascore_id)) if i is not None and i >= 0]
    base = fc.goal_baseline(idx, league_keys, league_name, country or competition.country, ctype, before)
    events = {k: fc.event_baseline(idx, league_keys, league_name, country or competition.country, ctype, before, k)
              for k in ("corners", "cards")}
    neutral = is_neutral_tournament(profile_key)
    sides = {"home": (home, home_id), "away": (away, away_id)}
    blocking, warnings, stats, ident, basis, recents = [], [], {}, {}, {}, {}
    stale = False
    if base is None:
        blocking.append(f"no stored results before {before} at any level of the fallback chain for goals "
                        f"({ctype.label()})")
    no_events = [k for k, v in events.items() if v is None]
    for k in no_events:
        warnings.append(f"no stored {k} data before {before} at any level of the fallback chain; "
                        f"{k} markets are not shown")
    for side, (name, tid) in sides.items():
        label = f"{side}_team '{name}'"
        chosen, own, domestic = _side(conn, competition, continental, name, provider, tid, match_date, registry)
        source = "domestic_league" if chosen is not None and chosen is not own else "competition"
        ident[side] = own.to_dict()
        ident[f"{side}_history"] = {"source": source, "record": None if chosen is None else {
            "team_name_lower": chosen.team_name_lower, "history_league": chosen.history_league,
            "matches_played": chosen.matches_played, "competition": chosen.competition_key,
            "canonical_team_id": chosen.canonical_team_id}, "domestic": domestic}
        last = _last_match(conn, chosen) if chosen is not None else None
        leak = bool(chosen is not None and last and last[:10] >= before)
        keys = fc.team_record_keys(conn, provider, tid, (chosen.team_name_lower, chosen.history_league)
                                   if chosen is not None else None)
        recent = fc.team_recent(idx, keys, before)
        recents[side] = recent
        if blocking:
            continue
        info = {"team": name, "own_matches": recent.n, "records": [f"{n} | {lg}" for lg, n in keys]}
        if chosen is None:
            info["identity"] = f"{own.status}: {own.reason}"
        if leak:
            info["record_not_used"] = (f"record '{chosen.history_league}' already includes results dated "
                                       f"{last[:10]}, on or after the kick-off date; its earlier results are used")
        if chosen is not None and not leak and (chosen.matches_played or 0) >= fc.FULL_HISTORY:
            st = _venue_stats(conn, chosen, side, profile_key)
            info.update(path="record", own_matches=chosen.matches_played,
                        goals={"level": "team", "source": f"record '{chosen.history_league}'",
                               "matches": chosen.matches_played})
            gap = _gap_days(last, before)
        else:
            sc, co = fc.blended_goals(recent, base, side, neutral)
            st = TeamVenueStats(scored=sc, conceded=co, corners=0.0, cards=0.0, matches_played=recent.n,
                                form_last5=fc._form(recent, base))
            info.update(path="chain", goals={
                "level": "team" if recent.n >= fc.FULL_HISTORY else ("team+" + base.level if recent.n else base.level),
                "baseline_level": base.level, "source": base.source, "baseline_matches": base.matches,
                "team_weight": round(recent.n / (recent.n + fc.SHRINK_K), 2)})
            gap = _gap_days(recent.rows[0][0].date if recent.rows else None, before)
        for kind in ("corners", "cards"):
            eb = events[kind]
            if eb is None:
                info[kind] = {"level": None, "source": "no stored data"}
                continue
            own_avg, m = fc._team_events(recent, kind)
            if info["path"] == "record" and m >= fc.FULL_HISTORY:
                info[kind] = {"level": "team", "matches": m}
                continue
            val, m = fc.blended_event(recent, eb, side, kind)
            setattr(st, kind, val)
            info[kind] = {"level": ("team+" + eb.level) if m >= fc.MIN_TEAM_EVENTS else eb.level,
                          "source": eb.source, "baseline_matches": eb.matches, "team_matches": m}
        stats[side] = st
        basis[side] = info
        if source == "domestic_league":
            warnings.append(f"{label} uses its domestic '{chosen.history_league}' history "
                            f"({chosen.matches_played} matches); it has {own.matches_played or 0} verified "
                            f"'{competition.key}' matches")
        if gap is not None and gap >= STALE_DAYS:
            stale = True
            warnings.append(f"{label} latest stored result is {gap} days before this match; "
                            "newer results are not in the database")
    elo_note = None
    if not blocking and ctype.national and any(b["path"] == "chain" for b in basis.values()):
        table, slope, nfit = fc.national_elo(idx, before)
        eh = fc.elo_for(table, recents["home"].names | {home})
        ea = fc.elo_for(table, recents["away"].names | {away})
        if slope and eh and ea and eh[1] >= fc.ELO_MIN_GAMES and ea[1] >= fc.ELO_MIN_GAMES:
            prof = LEAGUE_PROFILES.get(profile_key)
            ph, pa = (prof.avg_home_goals, prof.avg_away_goals) if prof else (base.home, base.away)
            split = fc.apply_elo(stats["home"], stats["away"], eh[0] - ea[0], slope, ph, pa)
            elo_note = {"home": round(eh[0]), "away": round(ea[0]), "home_games": eh[1], "away_games": ea[1],
                        "slope_goals_per_100": round(slope, 3), "fitted_on": nfit, **split}
    recs = [ident[f"{s}_history"]["record"] for s in ("home", "away")]
    if not blocking and all(recs) and recs[0]["competition"] != recs[1]["competition"]:
        warnings.append(f"home and away statistics come from different competitions ('{recs[0]['history_league']}', "
                        f"'{recs[1]['history_league']}'); the model applies no league-strength adjustment")
    confidence = None
    if not blocking:
        conf = fc.confidence_label(list(basis.values()), cross_competition=continental and any(
            ident[f"{s}_history"]["source"] == "domestic_league" for s in ("home", "away")), stale=stale)
        confidence = {"level": conf, "note": _note(conf, basis, base, elo_note, stale, warnings),
                      "baseline": {"level": base.level, "source": base.source, "matches": base.matches,
                                   "home_goals": round(base.home, 3), "away_goals": round(base.away, 3),
                                   "window_days": fc.WINDOW_DAYS},
                      "competition_type": ctype.label(), "elo": elo_note}
    profile = None
    if not blocking and profile_key not in LEAGUE_PROFILES:
        profile = {"avg_home_goals": round(base.home, 3), "avg_away_goals": round(base.away, 3),
                   "level": base.level, "source": base.source, "matches": base.matches}
    return {"available": not blocking, "blocking": blocking, "warnings": warnings,
            "home_stats": stats.get("home"), "away_stats": stats.get("away"),
            "basis": basis, "confidence": confidence, "league_profile": profile,
            "unavailable_markets": [k.capitalize() for k in no_events] if not blocking else [],
            "identity": {"version": TEAM_IDENTITY_VERSION, "competition": competition.key,
                         "cross_competition": continental, **ident}}


def _note(conf: str, basis: dict, base, elo_note, stale: bool, warnings: list) -> str:
    from src.identity.fallback_chain import FULL_HISTORY, fmt_level
    parts = []
    for b in basis.values():
        n = b["own_matches"]
        if n == 0:
            parts.append(f"{b['team']} has no stored results")
        elif n < FULL_HISTORY:
            parts.append(f"{b['team']} has {n} recent match{'es' if n != 1 else ''}")
    if any(b["path"] == "chain" for b in basis.values()):
        parts.append(f"{fmt_level(base.level)} averages used ({base.source}, {base.matches} matches)")
    if elo_note:
        parts.append(f"national-team Elo from {elo_note['fitted_on']} international results")
    if any(w for w in warnings if "uses its domestic" in w):
        parts.append("domestic-league history used for this competition")
    if stale:
        parts.append("latest stored results are 3+ weeks old")
    if not parts:
        return f"{conf} confidence: both teams have {FULL_HISTORY}+ stored matches in this competition."
    return f"{conf} confidence: " + "; ".join(parts) + "."
