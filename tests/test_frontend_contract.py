"""The match-analysis response must keep every field the frontend reads."""
import re
from pathlib import Path

from tests.golden.test_golden_probabilities import isolated_engine  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
MATCH_DETAIL = ROOT / "frontend" / "src" / "components" / "MatchDetail.jsx"


def _fields_read_by_frontend() -> set[str]:
    return set(re.findall(r"analysis\??\.([a-z_]+)", MATCH_DETAIL.read_text(encoding="utf-8")))


def test_frontend_field_list_is_what_we_expect():
    assert _fields_read_by_frontend() == {
        "dominance", "full_analysis", "poisson", "score_prediction", "suggested_pick", "suggested_picks",
        "tiers", "top_picks", "total_confident_picks", "total_markets_scanned", "total_qualified",
    }


def test_analysis_response_contains_every_frontend_field(isolated_engine):  # noqa: F811
    from api.services.match_analysis_service import _compute_match_analysis

    analysis = _compute_match_analysis("Arsenal", "Chelsea", "Premier League", shuffle_tiers=False, fixture_id="")
    assert _fields_read_by_frontend() - set(analysis) == set()
    assert isinstance(analysis["suggested_picks"], list)
    assert analysis["suggested_pick"] == (analysis["suggested_picks"][0] if analysis["suggested_picks"] else None)
