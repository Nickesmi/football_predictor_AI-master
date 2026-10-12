"""Offline stand-ins for the external football data providers.

The payloads follow the real API-Football v3 `/fixtures` response shape, so
the production conversion code (`_api_football_to_fixture`, caching,
scoreline merging) runs unchanged; only the HTTP call is replaced.
"""
from __future__ import annotations

_TEAMS = [
    (40, "Liverpool", 50, "Manchester City", 39, "Premier League", "England"),
    (505, "Inter", 500, "Bologna", 135, "Serie A", "Italy"),
    (157, "Bayern Munich", 165, "Borussia Dortmund", 78, "Bundesliga", "Germany"),
]


def api_football_fixtures(date_str: str) -> list[dict]:
    """Three fixtures on `date_str` (UTC 12:00/15:00/18:00): one finished, one live, one not started."""
    statuses = [
        ("FT", {"home": 2, "away": 1}, {"home": 1, "away": 0}),
        ("2H", {"home": 0, "away": 0}, {"home": 0, "away": 0}),
        ("NS", {"home": None, "away": None}, {"home": None, "away": None}),
    ]
    out = []
    for i, ((home_id, home, away_id, away, league_id, league, country), (short, goals, ht)) in enumerate(
        zip(_TEAMS, statuses)
    ):
        out.append({
            "fixture": {
                "id": 9_000_000 + i,
                "date": f"{date_str}T{12 + 3 * i:02d}:00:00+00:00",
                "status": {"short": short},
            },
            "league": {
                "id": league_id, "name": league, "country": country,
                "logo": f"https://example.invalid/league/{league_id}.png",
                "season": int(date_str[:4]), "round": "Regular Season - 1",
            },
            "teams": {
                "home": {"id": home_id, "name": home, "logo": f"https://example.invalid/team/{home_id}.png"},
                "away": {"id": away_id, "name": away, "logo": f"https://example.invalid/team/{away_id}.png"},
            },
            "goals": goals,
            "score": {"halftime": ht, "fulltime": goals if short == "FT" else {"home": None, "away": None}},
        })
    return out


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class _ImageResponse:
    status_code = 200
    content = PNG_1X1


class _OfflineResponse:
    """Any non-image curl_cffi request: behaves like an unreachable upstream (HTTP 503)."""
    status_code = 503
    content = b""
    text = ""

    def json(self):
        return {}


def _curl_get(url, **kwargs):
    return _ImageResponse() if "/image" in str(url) else _OfflineResponse()


def install(monkeypatch) -> None:
    """Route every provider call made by the API layer to offline fakes."""
    import api.services.debug_service as debug_service
    import api.services.fixtures_service as fixtures_service
    import curl_cffi.requests as curl_requests
    import src.data.live_score_provider as live_score_provider
    import src.data.sofascore_fetcher as sofascore_fetcher

    for module in (fixtures_service, debug_service):
        monkeypatch.setattr(module, "_fetch_api_football_fixtures", api_football_fixtures)
        monkeypatch.setattr(module, "_fetch_sofascore_fixtures", lambda date_str: [])
    monkeypatch.setattr(sofascore_fetcher, "fetch_fixtures_by_date", lambda date_str: [])
    monkeypatch.setattr(fixtures_service, "fetch_fixtures_by_date", lambda date_str: [])
    monkeypatch.setattr(live_score_provider, "fetch_live_scores", lambda date_str: [])
    monkeypatch.setattr(fixtures_service, "fetch_live_scores", lambda date_str: [])
    monkeypatch.setattr(curl_requests, "get", _curl_get)
