"""
Parser + validator for real historical football results in the
"openfootball" football.txt format (https://github.com/openfootball).

This is REAL, publicly documented match data compiled from official
results (not synthetic, not fabricated). It intentionally does NOT carry
xG, shots, possession, corners, cards, odds, or lineup data — those fields
are simply not part of this free open dataset. Downstream code MUST treat
those fields as unavailable (None), never estimate/fabricate them.

Fields present per match:
    date, kickoff_time (when known), season, league, home_team, away_team,
    home_goals, away_goals, home_goals_ht, away_goals_ht (when known)

Everything else the audit template asked for (xG, shots, possession,
corners, cards, odds, injuries/lineups) is UNAVAILABLE in this dataset and
is not represented at all in the output schema — no placeholder columns,
no zero-filling, so nothing downstream can accidentally treat "missing" as
"zero".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

import pandas as pd

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_DATE_LINE_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:\s+(\d{4}))?\s*$"
)

# Format A (older exports): "TeamA   v  TeamB   H-A (HTH-HTA)"
_FIXTURE_V_RE = re.compile(
    r"^(?:(?P<time>\d{1,2}:\d{2})\s+)?"
    r"(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+"
    r"(?P<hg>\d+)-(?P<ag>\d+)"
    r"(?:\s*\((?P<hgt>\d+)-(?P<agt>\d+)\))?"
    r"\s*(?:\[(?P<tag>[^\]]+)\])?\s*$"
)

# Format B (newer exports): "TeamA   H-A (HTH-HTA)  TeamB"
_FIXTURE_SCORE_MID_RE = re.compile(
    r"^(?:(?P<time>\d{1,2}:\d{2})\s+)?"
    r"(?P<home>.+?)\s{2,}"
    r"(?P<hg>\d+)-(?P<ag>\d+)"
    r"(?:\s*\((?P<hgt>\d+)-(?P<agt>\d+)\))?\s{2,}"
    r"(?P<away>.+?)"
    r"\s*(?:\[(?P<tag>[^\]]+)\])?\s*$"
)

_HEADER_RE = re.compile(r"^=\s+(?P<league>.+?)\s+(?P<season>\d{4}/\d{2})\s*$")
_MATCH_COUNT_RE = re.compile(r"^#\s*Matches\s+(\d+)\s*$")


@dataclass
class ParsedMatch:
    match_id: str
    date: str            # ISO YYYY-MM-DD
    kickoff_time: Optional[str]   # "HH:MM" or None if unknown
    season: str           # "2015-16"
    league: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    home_goals_ht: Optional[int]
    away_goals_ht: Optional[int]
    source: str = "openfootball"
    raw_tag: Optional[str] = None   # e.g. "awarded" — carried through for validation


@dataclass
class ParseResult:
    matches: list[ParsedMatch] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    expected_match_count: Optional[int] = None


def _infer_year(season: str, month: int) -> int:
    """openfootball season strings are 'YYYY/YY' (e.g. '2015/16').
    Aug-Dec fixtures belong to the season's start year; Jan-Jul to start+1.
    """
    start_year = int(season.split("/")[0])
    return start_year if month >= 7 else start_year + 1


def parse_openfootball_text(text: str, league_hint: str, season_hint: str) -> ParseResult:
    """Parse one openfootball .txt file's contents into validated matches.

    league_hint / season_hint (e.g. "Premier League", "2015-16") are used as
    a fallback/consistency check against the in-file header, and to build
    stable match_ids and the source-of-truth `season` column.
    """
    result = ParseResult()
    current_date: Optional[date_cls] = None
    league_name = league_hint
    season_slug = season_hint

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue

        header_m = _HEADER_RE.match(stripped)
        if header_m:
            league_name = header_m.group("league")
            continue

        count_m = _MATCH_COUNT_RE.match(stripped)
        if count_m:
            result.expected_match_count = int(count_m.group(1))
            continue

        if stripped.startswith("#") or stripped.startswith("▪"):
            continue

        date_m = _DATE_LINE_RE.match(stripped)
        if date_m:
            weekday, mon, day, year = date_m.groups()
            month_num = MONTHS[mon]
            try:
                if year:
                    current_date = date_cls(int(year), month_num, int(day))
                else:
                    # Most openfootball exports never print the year at all —
                    # infer it from the season string (e.g. "2015-16": Aug-Dec
                    # fixtures are 2015, Jan-Jul fixtures are 2016). This does
                    # not depend on having seen a prior date line.
                    inferred_year = _infer_year(season_hint.replace("-", "/"), month_num)
                    current_date = date_cls(inferred_year, month_num, int(day))
            except ValueError as e:
                result.quarantined.append({
                    "line": lineno, "raw": raw_line,
                    "reason": f"invalid calendar date: {e}",
                })
                current_date = None
            continue

        fixture_m = _FIXTURE_V_RE.match(stripped) or _FIXTURE_SCORE_MID_RE.match(stripped)
        if not fixture_m:
            # Not a date, header, or fixture line we recognise — could be a
            # standings table, footnote, etc. Not an error, just skip it.
            continue

        if current_date is None:
            result.quarantined.append({
                "line": lineno, "raw": raw_line,
                "reason": "fixture line encountered before any date line",
            })
            continue

        gd = fixture_m.groupdict()
        home = re.sub(r"\s+", " ", gd["home"]).strip()
        away = re.sub(r"\s+", " ", gd["away"]).strip()
        try:
            hg, ag = int(gd["hg"]), int(gd["ag"])
        except (TypeError, ValueError):
            result.quarantined.append({
                "line": lineno, "raw": raw_line, "reason": "unparseable score",
            })
            continue

        hgt = int(gd["hgt"]) if gd.get("hgt") is not None else None
        agt = int(gd["agt"]) if gd.get("agt") is not None else None
        tag = gd.get("tag")

        # ── Validation (Phase 2 requirement: reject/quarantine, never silently insert) ──
        reasons = []
        if not home or not away:
            reasons.append("missing team name")
        if home.lower() == away.lower():
            reasons.append("home_team == away_team")
        if hg < 0 or ag < 0 or hg > 20 or ag > 20:
            reasons.append(f"implausible score {hg}-{ag}")
        if hgt is not None and agt is not None:
            if hgt < 0 or agt < 0 or hgt > hg or agt > ag:
                reasons.append(f"HT score ({hgt}-{agt}) inconsistent with FT score ({hg}-{ag})")
        if tag and tag.lower() in ("awarded", "abandoned", "postponed", "cancelled", "canceled"):
            reasons.append(f"non-organic result tag: [{tag}]")

        if reasons:
            result.quarantined.append({
                "line": lineno, "raw": raw_line,
                "home_team": home, "away_team": away,
                "reason": "; ".join(reasons),
            })
            continue

        match_id = hashlib.sha1(
            f"{league_name}|{season_hint}|{current_date.isoformat()}|{home}|{away}".encode()
        ).hexdigest()[:16]

        result.matches.append(ParsedMatch(
            match_id=match_id,
            date=current_date.isoformat(),
            kickoff_time=gd.get("time"),
            season=season_hint,
            league=league_name,
            home_team=home,
            away_team=away,
            home_goals=hg,
            away_goals=ag,
            home_goals_ht=hgt,
            away_goals_ht=agt,
            raw_tag=tag,
        ))

    return result


def parse_directory(raw_dir: Path, league_map: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Parse every '<repo>_<season>.txt' file in raw_dir.

    league_map: {"england": "Premier League", "deutschland": "Bundesliga", "italy": "Serie A"}

    Returns (matches_df, quarantined_df, count_check_report).
    """
    all_matches: list[ParsedMatch] = []
    all_quarantined: list[dict] = []
    count_report = []

    for path in sorted(raw_dir.glob("*.txt")):
        repo, season_hint = path.stem.split("_", 1)
        league_hint = league_map.get(repo, repo)
        text = path.read_text(encoding="utf-8")
        parsed = parse_openfootball_text(text, league_hint=league_hint, season_hint=season_hint)

        for q in parsed.quarantined:
            q["file"] = path.name
        all_quarantined.extend(parsed.quarantined)
        all_matches.extend(parsed.matches)

        count_report.append({
            "file": path.name,
            "league": league_hint,
            "season": season_hint,
            "expected_matches": parsed.expected_match_count,
            "parsed_matches": len(parsed.matches),
            "quarantined": len(parsed.quarantined),
            "count_matches_header": (
                parsed.expected_match_count is None
                or parsed.expected_match_count == len(parsed.matches) + len(parsed.quarantined)
            ),
        })

    matches_df = pd.DataFrame([vars(m) for m in all_matches])
    if not matches_df.empty:
        matches_df = matches_df.sort_values(["date", "league", "home_team"]).reset_index(drop=True)

        # Cross-file duplicate detection (same league/season/date/teams appearing twice,
        # e.g. because a file was fetched twice or a fixture was re-listed after a
        # postponement under two different matchday headers).
        dup_mask = matches_df.duplicated(subset=["league", "season", "date", "home_team", "away_team"], keep="first")
        if dup_mask.any():
            dups = matches_df[dup_mask]
            for _, row in dups.iterrows():
                all_quarantined.append({
                    "reason": "duplicate match (same league/season/date/home/away already parsed)",
                    "home_team": row["home_team"], "away_team": row["away_team"],
                    "date": row["date"], "league": row["league"],
                })
            matches_df = matches_df[~dup_mask].reset_index(drop=True)

    quarantined_df = pd.DataFrame(all_quarantined)
    return matches_df, quarantined_df, count_report
