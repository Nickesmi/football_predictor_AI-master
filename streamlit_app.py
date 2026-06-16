from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import streamlit as st
from fastapi import BackgroundTasks


def _load_streamlit_secrets() -> None:
    """Expose Streamlit Cloud secrets as environment variables before backend imports."""
    for key in (
        "APIFOOTBALL_API_KEY",
        "API_FOOTBALL_KEY",
        "APIFOOTBALL_HOST",
        "RAPIDAPI_KEY",
        "RAPIDAPI_HOST",
        "RAPIDAPI_TENNIS_HOST",
        "FOOTBALL_PREDICTOR_DB_PATH",
    ):
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value and not os.getenv(key):
            os.environ[key] = str(value)

    os.environ.setdefault("APIFOOTBALL_HOST", "v3.football.api-sports.io")
    os.environ.setdefault("RAPIDAPI_HOST", "sofascore.p.rapidapi.com")
    os.environ.setdefault("RAPIDAPI_KEY", "disabled")


def _score_text(fixture: dict[str, Any]) -> str:
    home = fixture.get("home_goals")
    away = fixture.get("away_goals")
    if home is None or away is None:
        return "-"
    return f"{home} - {away}"


@st.cache_data(ttl=180, show_spinner=False)
def _fetch_fixtures(date_str: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    from api.main import get_fixtures_by_date

    response = get_fixtures_by_date(date_str, BackgroundTasks(), force_refresh=force_refresh)
    if isinstance(response, dict):
        if response.get("message"):
            st.warning(response["message"])
        return response.get("fixtures", [])
    return response


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_results(date_str: str) -> dict[str, Any]:
    from api.main import get_results_verification

    return get_results_verification(date_str, BackgroundTasks())


def _render_fixture_table(fixtures: list[dict[str, Any]]) -> None:
    if not fixtures:
        st.info("No matches available for this date.")
        return

    rows = []
    for fixture in fixtures:
        rows.append({
            "Time": fixture.get("time") or "TBD",
            "Status": fixture.get("status") or "NS",
            "Home": fixture.get("home_team", {}).get("name", "Home"),
            "Score": _score_text(fixture),
            "Away": fixture.get("away_team", {}).get("name", "Away"),
            "League": fixture.get("league", {}).get("name", ""),
            "Country": fixture.get("league", {}).get("country", ""),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_results(date_str: str) -> None:
    with st.spinner("Checking finished results..."):
        data = _fetch_results(date_str)

    summary = data.get("summary", {})
    matches = data.get("matches", [])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accuracy", f"{summary.get('accuracy_pct', 0)}%")
    c2.metric("Correct", summary.get("total_correct", 0))
    c3.metric("Wrong", summary.get("total_wrong", 0))
    c4.metric("Settled picks", summary.get("total_picks", 0))

    categories = data.get("category_summary", [])
    if categories:
        st.subheader("Category Accuracy")
        st.dataframe(categories, use_container_width=True, hide_index=True)

    if not matches:
        st.info("No settled results available for this date.")
        return

    st.subheader("Finished Matches")
    for match in matches:
        fixture = match.get("fixture", {})
        actual = match.get("actual", {})
        home = fixture.get("home_team", {}).get("name", "Home")
        away = fixture.get("away_team", {}).get("name", "Away")
        score = f"{actual.get('home_goals', '-')} - {actual.get('away_goals', '-')}"
        summary = match.get("summary", {})
        with st.expander(f"{home} {score} {away} | {summary.get('correct', 0)}/{summary.get('total', 0)} hits"):
            picks = [
                {
                    "Market": pick.get("market"),
                    "Category": pick.get("category") or pick.get("section"),
                    "Probability": pick.get("probability"),
                    "Result": "WIN" if pick.get("result") is True else "LOSS" if pick.get("result") is False else "N/A",
                }
                for pick in match.get("picks", [])
                if pick.get("isSettled")
            ]
            st.dataframe(picks, use_container_width=True, hide_index=True)


def render() -> None:
    _load_streamlit_secrets()

    st.set_page_config(
        page_title="xGenius",
        page_icon="⚽",
        layout="wide",
    )

    st.title("xGenius")
    st.caption("AI football predictions, scorelines, and settled result verification.")

    api_key_present = bool(os.getenv("APIFOOTBALL_API_KEY") or os.getenv("API_FOOTBALL_KEY"))
    if not api_key_present:
        st.error("Missing APIFOOTBALL_API_KEY. Add it in Streamlit Cloud: App settings → Secrets.")
        st.code(
            'APIFOOTBALL_API_KEY = "your_api_football_key"\n'
            'API_FOOTBALL_KEY = "your_api_football_key"\n'
            'RAPIDAPI_KEY = "your_rapidapi_key"\n'
            'RAPIDAPI_HOST = "sofascore.p.rapidapi.com"',
            language="toml",
        )
        return

    today = date.today()
    selected = st.date_input("Match date", value=today, min_value=today - timedelta(days=30), max_value=today + timedelta(days=30))
    date_str = selected.isoformat()

    col1, col2 = st.columns([1, 5])
    force_refresh = col1.button("Refresh API")
    if force_refresh:
        _fetch_fixtures.clear()
        _fetch_results.clear()

    tab_fixtures, tab_results = st.tabs(["Fixtures", "Results"])
    with tab_fixtures:
        with st.spinner("Loading fixtures..."):
            fixtures = _fetch_fixtures(date_str, force_refresh=force_refresh)
        st.metric("Matches", len(fixtures))
        _render_fixture_table(fixtures)

    with tab_results:
        _render_results(date_str)


if __name__ == "__main__":
    render()
