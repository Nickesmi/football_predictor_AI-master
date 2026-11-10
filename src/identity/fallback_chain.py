"""Fallback chain for match-analysis inputs (2026-11-10).

Every fixture gets a prediction, built from the best real data available and labelled with the level used.
Nothing here is a hard-coded statistic: each number is an average of stored results (match_history) dated
strictly before the match's kick-off date.

Goals, per side (venue-specific):
  a. "team"     the team's own recent results across all competitions (up to TEAM_WINDOW, linked by provider
                team id through team_state_ids, plus the identity-resolved record), shrunk toward the
                baseline below with weight n / (n + SHRINK_K);
  b. "league"   that league's averages (home goals, away goals, draw rate) over the last WINDOW_DAYS,
                selected by provider league id, never by a shared label ("Serie A" is Italy and Brazil);
  c. "country"  the same competition type in the same country;
  d. "competition_type"  the same competition type (kind, gender, age) everywhere, then any competition of
                the same gender and age group ("gender_age"): women's and youth football never take men's
                senior averages before this point;
  e. "all_results"  every stored result, labelled as mixing all football (corners and cards reach this level
                in practice: few stored results carry them).
The first baseline level with enough matches is used; the team's own results are always blended in when it
has any. Corners and cards follow the same chain on the results that carry them.

National teams: a World-Football-style Elo is replayed over stored international results (point in time);
the goal-supremacy slope per 100 Elo points is fitted on those same results. It sets the split of the
expected total between the two sides when either side has thin history.

Constants below are model parameters (window sizes, shrinkage weight, Elo K), not data.
"""
from __future__ import annotations

import bisect
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

WINDOW_DAYS = 365
TEAM_WINDOW = 20
FULL_HISTORY = 10          # a team record with this many matches is used as stored (unchanged model path)
MEDIUM_HISTORY = 5
SHRINK_K = 5               # prior weight, in matches, of the baseline when blending a team's own results
MIN_GOALS = {"league": 30, "country": 30, "competition_type": 60, "gender_age": 60, "all_results": 1}
MIN_EVENTS = 20            # corners / cards: results carrying them, per level
MIN_TEAM_EVENTS = 3
ELO_START = 1500.0
ELO_K = {"national_friendly": 20.0, "national_competitive": 40.0}
ELO_MIN_GAMES = 5

LEVEL_TEXT = {"league": "league", "country": "country", "competition_type": "competition-type",
              "gender_age": "competition-type", "all_results": "all stored results"}

# ── competition type ─────────────────────────────────────────────────────────

_WOMEN = re.compile(r"\bwomen|\bwomen's|\bfemenin|\bfeminin|\bfemminile|frauen|\bdames\b|\bw\b|\bwsl\b|"
                    r"\bnwsl\b|\bliga f\b|damallsvenskan|toppserien|kvindeligaen|\bladies\b")
_YOUTH = re.compile(r"\bu-?\d{2}\b|\bsub-?\d{2}\b|youth|primavera|junior|juvenil|\bjugend|\breserve|"
                    r"\bii\b|\bb team\b|development|next pro|premier league 2|\buefa youth")
_NATIONAL_COMP = re.compile(r"world cup|nations league|cup of nations|copa am[eé]rica|gold cup|asian cup|"
                            r"euro championship|european championship|\beuro\b|afcon|arab cup|gulf cup|"
                            r"cosafa|cecafa|saff championship|aff championship|baltic cup|olympic|"
                            r"concacaf championship|ofc nations|waff")
_CLUB_FRIENDLY = re.compile(r"club friendl|friendlies clubs|club friendly")
_FRIENDLY = re.compile(r"friendl")
_CONTINENTAL = re.compile(r"champions league|europa league|conference league|libertadores|sudamericana|"
                          r"confederation cup|confederations cup|recopa|club world cup|super cup|"
                          r"champions cup|caf champions|afc cup|concacaf champions|leagues cup|"
                          r"campeones|intercontinental")
_CUP = re.compile(r"\bcup\b|\bcopa\b|pokal|coupe|coppa|ta[cç]a|beker|trophy|shield|supercopa|supercup|"
                  r"\bfa cup\b|knvb|dfb|ekstraklasa cup|league cup|\bkupa\b|\bkup\b|\bcupa\b|\bkubok\b")


@dataclass(frozen=True)
class CompetitionType:
    kind: str       # national_friendly | national_competitive | club_friendly | continental_club | cup | league
    gender: str     # men | women
    age: str        # senior | youth

    @property
    def national(self) -> bool:
        return self.kind.startswith("national_")

    def label(self) -> str:
        kind = self.kind.replace("_", " ")
        extra = [x for x in (self.gender if self.gender == "women" else "",
                             self.age if self.age == "youth" else "") if x]
        return kind + (f" ({', '.join(extra)})" if extra else "")


def classify(label: str, country: str = "") -> CompetitionType:
    """Competition type from the league label (and provider country). Women and youth are kept apart from
    men's senior football at every level of the chain."""
    lg = (label or "").lower()
    gender = "women" if _WOMEN.search(lg) else "men"
    age = "youth" if _YOUTH.search(lg) else "senior"
    if _CLUB_FRIENDLY.search(lg):
        kind = "club_friendly"
    elif _FRIENDLY.search(lg):
        kind = "national_friendly"
    elif _NATIONAL_COMP.search(lg) and "club" not in lg:
        kind = "national_competitive"
    elif _CONTINENTAL.search(lg) and (country in ("World", "") or "uefa" in lg or "caf" in lg or "afc" in lg):
        kind = "continental_club"
    elif "qualification" in lg and country == "World":
        kind = "national_competitive"
    elif _CUP.search(lg):
        kind = "cup"
    elif country == "World":
        kind = "continental_club"
    else:
        kind = "league"
    return CompetitionType(kind, gender, age)


# ── aggregated series with prefix sums (point-in-time windows in O(log n)) ───

_FIELDS = ("n", "hg", "ag", "draw", "hw", "cn", "hc", "ac", "kn", "hk", "ak")


class Series:
    def __init__(self):
        self.dates: list[str] = []
        self._rows: list[tuple] = []
        self.cum: list[tuple] | None = None

    def add(self, d: str, hg, ag, hc, ac, hk, ak):
        cn = hc is not None and ac is not None
        kn = hk is not None and ak is not None
        self._rows.append((d, 1, hg, ag, 1 if hg == ag else 0, 1 if hg > ag else 0, 1 if cn else 0,
                           hc if cn else 0, ac if cn else 0, 1 if kn else 0, hk if kn else 0, ak if kn else 0))

    def freeze(self):
        self._rows.sort(key=lambda r: r[0])
        acc = [0] * len(_FIELDS)
        self.cum = [tuple(acc)]
        for r in self._rows:
            acc = [a + b for a, b in zip(acc, r[1:])]
            self.cum.append(tuple(acc))
        self.dates = [r[0] for r in self._rows]
        self._rows = []

    def window(self, before: str, days: int = WINDOW_DAYS) -> dict:
        start = (date.fromisoformat(before) - timedelta(days=days)).isoformat()
        lo = bisect.bisect_left(self.dates, start)
        hi = bisect.bisect_left(self.dates, before)
        return dict(zip(_FIELDS, (b - a for a, b in zip(self.cum[lo], self.cum[hi]))))


@dataclass
class Baseline:
    level: str
    source: str
    matches: int
    home: float          # mean goals of home sides
    away: float
    draw_rate: float
    home_win_rate: float

    @property
    def per_team(self) -> float:
        return (self.home + self.away) / 2


@dataclass
class EventBaseline:
    level: str
    source: str
    matches: int
    home: float
    away: float


@dataclass
class HistoryRow:
    match_id: str
    date: str
    league: str
    home: str
    away: str
    hg: int
    ag: int
    hc: int | None
    ac: int | None
    hk: int | None
    ak: int | None
    ctype: CompetitionType


@dataclass
class HistoryIndex:
    key: tuple
    by_league_id: dict = field(default_factory=dict)          # (provider, league_id) -> Series
    by_country_type: dict = field(default_factory=dict)       # (country, CompetitionType) -> Series
    by_type: dict = field(default_factory=dict)               # CompetitionType -> Series
    by_gender_age: dict = field(default_factory=dict)         # (gender, age) -> Series
    all: Series = field(default_factory=Series)
    team_rows: dict = field(default_factory=dict)             # (league label, team name) -> [HistoryRow]
    national_rows: list = field(default_factory=list)         # HistoryRow, date order


_LOCK = threading.Lock()
_INDEX: dict[str, HistoryIndex] = {}


def _league_countries(conn) -> dict[tuple, str]:
    out: dict[tuple, str] = {}
    try:
        for lid, country in conn.execute("SELECT DISTINCT league_id, country FROM provider_team_competitions "
                                         "WHERE provider = 'api_football'"):
            out[("api_football", int(lid))] = country
    except sqlite3.OperationalError:
        pass
    from src.identity.competitions import COMPETITIONS
    for c in COMPETITIONS:
        out.setdefault(("api_football", c.api_football_id), c.country)
        out.setdefault(("sofascore", c.sofascore_id), c.country)
    return out


def history_index(conn: sqlite3.Connection, db_key: str = "") -> HistoryIndex:
    """The index for this database, rebuilt when match_history changes."""
    if not db_key:
        db_key = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "") or ""
    stamp = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM match_history").fetchone()
    if not db_key:                      # an in-memory database: no stable identity, never cached
        return _build(conn, ("", tuple(stamp)))
    key = (db_key, tuple(stamp))
    cached = _INDEX.get(db_key)
    if cached is not None and cached.key == key:
        return cached
    with _LOCK:
        cached = _INDEX.get(db_key)
        if cached is not None and cached.key == key:
            return cached
        idx = _build(conn, key)
        _INDEX[db_key] = idx
        return idx


def _build(conn, key) -> HistoryIndex:
    idx = HistoryIndex(key=key)
    countries = _league_countries(conn)
    has_matches = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
    join = ("SELECT h.match_id, h.match_date, h.league, h.home_team, h.away_team, h.home_goals, h.away_goals, "
            "h.home_corners, h.away_corners, h.home_cards, h.away_cards, m.provider, m.league_id "
            "FROM match_history h LEFT JOIN matches m ON m.id = h.match_id ORDER BY h.match_date, h.id"
            if has_matches else
            "SELECT match_id, match_date, league, home_team, away_team, home_goals, away_goals, home_corners, "
            "away_corners, home_cards, away_cards, NULL, NULL FROM match_history ORDER BY match_date, id")
    type_cache: dict[tuple, CompetitionType] = {}

    def series(d: dict, k) -> Series:
        s = d.get(k)
        if s is None:
            s = d[k] = Series()
        return s

    for (mid, d, league, home, away, hg, ag, hc, ac, hk, ak, prov, lid) in conn.execute(join):
        d = str(d)[:10]
        country = countries.get((prov, int(lid)), "") if lid is not None and prov else ""
        tk = (league, country)
        ctype = type_cache.get(tk)
        if ctype is None:
            ctype = type_cache[tk] = classify(league, country)
        args = (d, hg, ag, hc, ac, hk, ak)
        if lid is not None and prov:
            series(idx.by_league_id, (prov, int(lid))).add(*args)
        if country:
            series(idx.by_country_type, (country, ctype)).add(*args)
        series(idx.by_type, ctype).add(*args)
        series(idx.by_gender_age, (ctype.gender, ctype.age)).add(*args)
        idx.all.add(*args)
        row = HistoryRow(str(mid), d, league, home, away, hg, ag, hc, ac, hk, ak, ctype)
        idx.team_rows.setdefault((league, home), []).append(row)
        idx.team_rows.setdefault((league, away), []).append(row)
        if ctype.national:
            idx.national_rows.append(row)
    for group in (idx.by_league_id, idx.by_country_type, idx.by_type, idx.by_gender_age):
        for s in group.values():
            s.freeze()
    idx.all.freeze()
    return idx


# ── baselines ───────────────────────────────────────────────────────────────

def _levels(idx: HistoryIndex, league_keys, league_name: str, country: str, ctype: CompetitionType):
    for k in league_keys:
        if k in idx.by_league_id:
            yield "league", f"{league_name or 'league'}", idx.by_league_id[k]
    if country and country != "World" and (country, ctype) in idx.by_country_type:
        yield "country", f"{country} {ctype.label()}", idx.by_country_type[(country, ctype)]
    if ctype in idx.by_type:
        yield "competition_type", ctype.label(), idx.by_type[ctype]
    ga = (ctype.gender, ctype.age)
    if ga in idx.by_gender_age:
        yield "gender_age", ("men's" if ctype.gender == "men" else "women's") + (
            " youth" if ctype.age == "youth" else " senior") + " football, all competitions", idx.by_gender_age[ga]
    yield "all_results", "all stored results (men's, women's and youth)", idx.all


def _merge(windows: list[dict]) -> dict:
    out = dict.fromkeys(_FIELDS, 0)
    for w in windows:
        for k in _FIELDS:
            out[k] += w[k]
    return out


def goal_baseline(idx, league_keys, league_name, country, ctype, before) -> Baseline | None:
    seen = set()
    for level, source, _ in _levels(idx, league_keys, league_name, country, ctype):
        if level in seen:
            continue
        seen.add(level)
        ss = [s for lv, _, s in _levels(idx, league_keys, league_name, country, ctype) if lv == level]
        w = _merge([s.window(before) for s in ss])
        if w["n"] >= MIN_GOALS[level]:
            n = w["n"]
            return Baseline(level, source, n, w["hg"] / n, w["ag"] / n, w["draw"] / n, w["hw"] / n)
    w = idx.all.window(before, days=365 * 30)            # nothing in the last WINDOW_DAYS: any earlier season
    if w["n"]:
        n = w["n"]
        return Baseline("all_results", "all stored results, all seasons", n, w["hg"] / n, w["ag"] / n,
                        w["draw"] / n, w["hw"] / n)
    return None


def event_baseline(idx, league_keys, league_name, country, ctype, before, kind: str) -> EventBaseline | None:
    nk, hk, ak = ("cn", "hc", "ac") if kind == "corners" else ("kn", "hk", "ak")
    seen = set()
    for level, source, _ in _levels(idx, league_keys, league_name, country, ctype):
        if level in seen:
            continue
        seen.add(level)
        ss = [s for lv, _, s in _levels(idx, league_keys, league_name, country, ctype) if lv == level]
        w = _merge([s.window(before) for s in ss])
        if w[nk] >= MIN_EVENTS:
            return EventBaseline(level, source, w[nk], w[hk] / w[nk], w[ak] / w[nk])
    # any date before kick-off at the widest level (few stored results carry corners/cards)
    w = idx.all.window(before, days=365 * 30)
    if w[nk] >= MIN_EVENTS:
        return EventBaseline("all_results", "all stored results (all seasons)", w[nk], w[hk] / w[nk],
                             w[ak] / w[nk])
    return None


# ── the team's own results ──────────────────────────────────────────────────

def team_record_keys(conn, provider, provider_team_id, resolved: tuple | None) -> list[tuple]:
    """(league label, record name) of every record linked to the team: the identity-resolved record and
    every record bound to its provider team id (team_state_ids), in any competition."""
    keys = []
    if resolved:
        lower, league = resolved
        row = conn.execute("SELECT team_name FROM team_state WHERE team_name_lower = ? AND league = ? "
                           "AND venue = 'overall'", (lower, league)).fetchone()
        if row:
            keys.append((league, row[0]))
    if provider and provider_team_id not in (None, ""):
        try:
            for league, name in conn.execute("SELECT league, team_name FROM team_state_ids WHERE provider = ? "
                                             "AND provider_team_id = ?", (provider, str(provider_team_id))):
                if (league, name) not in keys:
                    keys.append((league, name))
        except sqlite3.OperationalError:
            pass
    return keys


@dataclass
class TeamRecent:
    rows: list            # (HistoryRow, is_home)
    names: set

    @property
    def n(self) -> int:
        return len(self.rows)


def team_recent(idx: HistoryIndex, keys: list[tuple], before: str, limit: int = TEAM_WINDOW) -> TeamRecent:
    seen, out = set(), []
    for league, name in keys:
        for r in idx.team_rows.get((league, name), ()):
            if r.date < before and r.match_id not in seen:
                seen.add(r.match_id)
                out.append((r, r.home == name))
    out.sort(key=lambda x: x[0].date, reverse=True)
    return TeamRecent(out[:limit], {n for _, n in keys})


def _team_means(recent: TeamRecent):
    s = sum((r.hg if h else r.ag) for r, h in recent.rows)
    c = sum((r.ag if h else r.hg) for r, h in recent.rows)
    n = recent.n
    return (s / n, c / n) if n else (0.0, 0.0)


def _team_events(recent: TeamRecent, kind: str):
    vals = []
    for r, h in recent.rows:
        own = (r.hc if h else r.ac) if kind == "corners" else (r.hk if h else r.ak)
        if own is not None:
            vals.append(own)
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def _form(recent: TeamRecent, base: Baseline) -> float:
    last = recent.rows[:5]
    if not last:
        # league mean points per team-match, normalised like team_state.form_last5 (points / 3 per match)
        return round((3 * (1 - base.draw_rate) + 2 * base.draw_rate) / 6, 3)
    pts = sum(3 if (r.hg > r.ag) == h and r.hg != r.ag else 1 if r.hg == r.ag else 0 for r, h in last)
    return round(pts / (3 * len(last)), 3)


def blended_goals(recent: TeamRecent, base: Baseline, venue: str, neutral: bool) -> tuple[float, float]:
    """Venue-specific scored/conceded: the team's means shrunk toward the baseline's per-team mean, then
    scaled by the baseline's own home/away split."""
    mu = base.per_team
    n = recent.n
    s_avg, c_avg = _team_means(recent)
    s = (n * s_avg + SHRINK_K * mu) / (n + SHRINK_K)
    c = (n * c_avg + SHRINK_K * mu) / (n + SHRINK_K)
    if neutral or mu <= 0:
        return round(s, 3), round(c, 3)
    bs, bc = (base.home, base.away) if venue == "home" else (base.away, base.home)
    return round(s * bs / mu, 3), round(c * bc / mu, 3)


def blended_event(recent: TeamRecent, eb: EventBaseline, venue: str, kind: str) -> tuple[float, int]:
    own, m = _team_events(recent, kind)
    base = eb.home if venue == "home" else eb.away
    if m < MIN_TEAM_EVENTS:
        return round(base, 2), m
    return round((m * own + SHRINK_K * base) / (m + SHRINK_K), 2), m


# ── national-team Elo ───────────────────────────────────────────────────────

_ELO_CACHE: dict[tuple, tuple] = {}


def national_elo(idx: HistoryIndex, before: str):
    """({team: (rating, games)}, slope goals per 100 Elo points, fitted matches) from international results
    dated before `before`."""
    ck = (idx.key, before)
    if ck in _ELO_CACHE:
        return _ELO_CACHE[ck]
    ratings: dict[str, float] = {}
    games: dict[str, int] = {}
    xs, ys = [], []
    for r in idx.national_rows:
        if r.date >= before:
            break
        rh, ra = ratings.get(r.home, ELO_START), ratings.get(r.away, ELO_START)
        if games.get(r.home, 0) >= ELO_MIN_GAMES and games.get(r.away, 0) >= ELO_MIN_GAMES:
            xs.append((rh - ra) / 100.0)
            ys.append(r.hg - r.ag)
        exp = 1 / (1 + 10 ** ((ra - rh) / 400))
        res = 1.0 if r.hg > r.ag else 0.5 if r.hg == r.ag else 0.0
        gd = abs(r.hg - r.ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        k = ELO_K.get(r.ctype.kind, 30.0) * mult
        ratings[r.home] = rh + k * (res - exp)
        ratings[r.away] = ra - k * (res - exp)
        games[r.home] = games.get(r.home, 0) + 1
        games[r.away] = games.get(r.away, 0) + 1
    sxx = sum(x * x for x in xs)
    slope = (sum(x * y for x, y in zip(xs, ys)) / sxx) if sxx > 0 and len(xs) >= 30 else None
    out = ({t: (ratings[t], games[t]) for t in ratings}, slope, len(xs))
    if len(_ELO_CACHE) > 64:
        _ELO_CACHE.clear()
    _ELO_CACHE[ck] = out
    return out


def elo_for(elo_table: dict, names: set) -> tuple[float, int] | None:
    best = None
    for n in names:
        v = elo_table.get(n)
        if v and (best is None or v[1] > best[1]):
            best = v
    return best


def apply_elo(home_stats, away_stats, diff_points: float, slope: float, prof_home: float, prof_away: float):
    """Keep the model's expected total; split it by the Elo-implied goal supremacy.

    The Poisson model computes lambda_home = scored_h * conceded_a / prof_home (and the same for away), so
    the inputs are set to sqrt(target * profile average) on both sides of each product: the resulting
    expected goals are exactly the targets (the supremacy is not applied twice)."""
    import math
    lam_h = home_stats.scored * away_stats.conceded / max(prof_home, 0.01)
    lam_a = away_stats.scored * home_stats.conceded / max(prof_away, 0.01)
    total = lam_h + lam_a
    sup = max(-0.85 * total, min(0.85 * total, slope * diff_points / 100.0))
    t_h, t_a = (total + sup) / 2, (total - sup) / 2
    home_stats.scored = away_stats.conceded = round(math.sqrt(t_h * prof_home), 3)
    away_stats.scored = home_stats.conceded = round(math.sqrt(t_a * prof_away), 3)
    return {"supremacy": round(sup, 3), "target_home": round(t_h, 3), "target_away": round(t_a, 3)}


def confidence_label(sides: list[dict], *, cross_competition: bool, stale: bool) -> str:
    """High: both teams from their own stored record with FULL_HISTORY+ matches, same competition, recent.
    Medium: both teams have MEDIUM_HISTORY+ own matches. Low: anything thinner."""
    if all(s["path"] == "record" and s["own_matches"] >= FULL_HISTORY for s in sides) and not cross_competition \
            and not stale:
        return "High"
    if all(s["own_matches"] >= MEDIUM_HISTORY for s in sides):
        return "Medium"
    return "Low"


def fmt_level(level: str) -> str:
    return LEVEL_TEXT.get(level, level)


__all__ = ["classify", "history_index", "goal_baseline", "event_baseline", "team_record_keys", "team_recent",
           "blended_goals", "blended_event", "national_elo", "elo_for", "apply_elo", "confidence_label",
           "CompetitionType", "Baseline", "EventBaseline", "FULL_HISTORY", "MEDIUM_HISTORY"]
