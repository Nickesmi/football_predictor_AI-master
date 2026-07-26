"""
Team Statistics Database — Realistic 2024/25 season data for all Top 5 leagues + UCL.

Every team entry: (avg_goals_scored, avg_goals_conceded, avg_corners, avg_cards)
Separate home and away profiles.

For unknown teams, deterministic hash-based stats ensure uniqueness.
"""

from __future__ import annotations
import hashlib
from dataclasses import dataclass


@dataclass
class TeamVenueStats:
    """Full statistical profile for one team at one venue."""
    scored: float
    conceded: float
    corners: float
    cards: float
    matches_played: int = 20
    form_last5: float = 0.5


# ─── PREMIER LEAGUE ───────────────────────────────────────────────────
# Format: (scored, conceded, corners, cards)

PL_HOME = {
    "arsenal":            TeamVenueStats(2.2, 0.7, 6.5, 1.8),
    "liverpool":          TeamVenueStats(2.4, 0.6, 6.2, 1.5),
    "man city":           TeamVenueStats(2.5, 0.8, 7.0, 1.6),
    "manchester city":    TeamVenueStats(2.5, 0.8, 7.0, 1.6),
    "chelsea":            TeamVenueStats(1.9, 1.0, 5.8, 2.0),
    "tottenham":          TeamVenueStats(2.0, 1.1, 5.5, 2.1),
    "man united":         TeamVenueStats(1.5, 1.2, 5.0, 2.3),
    "manchester united":  TeamVenueStats(1.5, 1.2, 5.0, 2.3),
    "newcastle":          TeamVenueStats(1.8, 0.9, 5.8, 1.9),
    "aston villa":        TeamVenueStats(1.7, 1.0, 5.3, 2.0),
    "brighton":           TeamVenueStats(1.6, 1.0, 5.5, 1.7),
    "west ham":           TeamVenueStats(1.5, 1.3, 4.8, 2.2),
    "bournemouth":        TeamVenueStats(1.3, 1.2, 4.5, 2.0),
    "fulham":             TeamVenueStats(1.4, 1.1, 4.8, 1.9),
    "wolves":             TeamVenueStats(1.2, 1.4, 4.5, 2.3),
    "wolverhampton":      TeamVenueStats(1.2, 1.4, 4.5, 2.3),
    "crystal palace":     TeamVenueStats(1.3, 1.3, 4.8, 2.1),
    "brentford":          TeamVenueStats(1.6, 1.2, 5.0, 2.1),
    "nottingham":         TeamVenueStats(1.1, 1.3, 4.2, 2.4),
    "everton":            TeamVenueStats(1.0, 1.3, 4.3, 2.2),
    "burnley":            TeamVenueStats(0.9, 1.6, 3.8, 2.5),
    "luton":              TeamVenueStats(1.0, 1.7, 4.0, 2.4),
    "sheffield":          TeamVenueStats(0.8, 1.8, 3.5, 2.6),
    "ipswich":            TeamVenueStats(1.0, 1.5, 4.0, 2.2),
    "leicester":          TeamVenueStats(1.2, 1.4, 4.5, 2.1),
    "leeds":              TeamVenueStats(1.4, 1.2, 5.0, 2.0),
    "sunderland":         TeamVenueStats(1.3, 1.1, 4.8, 1.9),
    "southampton":        TeamVenueStats(1.0, 1.6, 4.2, 2.3),
}

PL_AWAY = {
    "arsenal":            TeamVenueStats(1.8, 1.0, 5.5, 2.0),
    "liverpool":          TeamVenueStats(1.9, 0.8, 5.8, 1.7),
    "man city":           TeamVenueStats(2.1, 0.9, 6.5, 1.8),
    "manchester city":    TeamVenueStats(2.1, 0.9, 6.5, 1.8),
    "chelsea":            TeamVenueStats(1.5, 1.2, 5.0, 2.2),
    "tottenham":          TeamVenueStats(1.4, 1.4, 4.8, 2.3),
    "man united":         TeamVenueStats(1.1, 1.4, 4.2, 2.5),
    "manchester united":  TeamVenueStats(1.1, 1.4, 4.2, 2.5),
    "newcastle":          TeamVenueStats(1.3, 1.2, 5.0, 2.1),
    "aston villa":        TeamVenueStats(1.2, 1.3, 4.5, 2.2),
    "brighton":           TeamVenueStats(1.3, 1.2, 5.0, 1.9),
    "west ham":           TeamVenueStats(1.0, 1.5, 4.0, 2.4),
    "bournemouth":        TeamVenueStats(0.9, 1.5, 3.8, 2.2),
    "fulham":             TeamVenueStats(1.0, 1.3, 4.2, 2.1),
    "wolves":             TeamVenueStats(0.8, 1.5, 3.8, 2.5),
    "wolverhampton":      TeamVenueStats(0.8, 1.5, 3.8, 2.5),
    "crystal palace":     TeamVenueStats(0.9, 1.5, 4.0, 2.3),
    "brentford":          TeamVenueStats(1.2, 1.4, 4.5, 2.3),
    "nottingham":         TeamVenueStats(0.8, 1.6, 3.5, 2.6),
    "everton":            TeamVenueStats(0.7, 1.5, 3.5, 2.4),
    "burnley":            TeamVenueStats(0.6, 2.0, 3.0, 2.8),
    "luton":              TeamVenueStats(0.7, 2.0, 3.2, 2.7),
    "sheffield":          TeamVenueStats(0.5, 2.1, 3.0, 2.9),
    "ipswich":            TeamVenueStats(0.7, 1.7, 3.5, 2.4),
    "leicester":          TeamVenueStats(0.8, 1.6, 3.8, 2.3),
    "leeds":              TeamVenueStats(1.0, 1.5, 4.2, 2.2),
    "sunderland":         TeamVenueStats(0.9, 1.4, 4.0, 2.1),
    "southampton":        TeamVenueStats(0.7, 1.8, 3.5, 2.5),
}

# ─── LA LIGA ────────────────────────────────────────────────────────────

LALIGA_HOME = {
    "barcelona":          TeamVenueStats(2.6, 0.6, 7.0, 2.0),
    "real madrid":        TeamVenueStats(2.3, 0.7, 6.0, 2.2),
    "atletico":           TeamVenueStats(1.6, 0.6, 5.5, 2.8),
    "atletico madrid":    TeamVenueStats(1.6, 0.6, 5.5, 2.8),
    "real sociedad":      TeamVenueStats(1.5, 0.9, 5.5, 2.1),
    "athletic":           TeamVenueStats(1.6, 0.8, 5.8, 2.3),
    "athletic bilbao":    TeamVenueStats(1.6, 0.8, 5.8, 2.3),
    "athletic club":      TeamVenueStats(1.6, 0.8, 5.8, 2.3),
    "villarreal":         TeamVenueStats(1.7, 1.0, 5.8, 2.0),
    "betis":              TeamVenueStats(1.4, 1.1, 5.3, 2.5),
    "real betis":         TeamVenueStats(1.4, 1.1, 5.3, 2.5),
    "sevilla":            TeamVenueStats(1.3, 1.0, 5.0, 2.6),
    "girona":             TeamVenueStats(1.8, 1.0, 5.5, 1.9),
    "valencia":           TeamVenueStats(1.2, 1.2, 4.8, 2.7),
    "getafe":             TeamVenueStats(0.9, 0.8, 3.5, 3.2),
    "celta":              TeamVenueStats(1.3, 1.3, 4.5, 2.3),
    "celta vigo":         TeamVenueStats(1.3, 1.3, 4.5, 2.3),
    "osasuna":            TeamVenueStats(1.2, 1.0, 4.8, 2.5),
    "rayo vallecano":     TeamVenueStats(1.1, 1.1, 4.5, 2.8),
    "mallorca":           TeamVenueStats(1.0, 0.9, 4.2, 2.4),
    "las palmas":         TeamVenueStats(1.1, 1.4, 4.3, 2.3),
    "alaves":             TeamVenueStats(0.9, 1.3, 3.8, 2.6),
    "cadiz":              TeamVenueStats(0.8, 1.4, 3.5, 2.5),
    "almeria":            TeamVenueStats(0.9, 1.6, 3.8, 2.4),
    "granada":            TeamVenueStats(0.8, 1.7, 3.5, 2.7),
    "espanyol":           TeamVenueStats(1.0, 1.2, 4.3, 2.4),
    "leganes":            TeamVenueStats(0.9, 1.3, 3.8, 2.5),
    "valladolid":         TeamVenueStats(0.8, 1.4, 3.5, 2.6),
    "real valladolid":    TeamVenueStats(0.8, 1.4, 3.5, 2.6),
}

LALIGA_AWAY = {
    "barcelona":          TeamVenueStats(2.2, 0.9, 6.5, 2.2),
    "real madrid":        TeamVenueStats(1.9, 1.0, 5.5, 2.4),
    "atletico":           TeamVenueStats(1.2, 0.9, 4.8, 3.0),
    "atletico madrid":    TeamVenueStats(1.2, 0.9, 4.8, 3.0),
    "real sociedad":      TeamVenueStats(1.1, 1.2, 4.5, 2.3),
    "athletic":           TeamVenueStats(1.2, 1.1, 5.0, 2.5),
    "athletic bilbao":    TeamVenueStats(1.2, 1.1, 5.0, 2.5),
    "athletic club":      TeamVenueStats(1.2, 1.1, 5.0, 2.5),
    "villarreal":         TeamVenueStats(1.3, 1.2, 5.0, 2.2),
    "betis":              TeamVenueStats(1.0, 1.3, 4.5, 2.7),
    "real betis":         TeamVenueStats(1.0, 1.3, 4.5, 2.7),
    "sevilla":            TeamVenueStats(0.9, 1.3, 4.2, 2.8),
    "girona":             TeamVenueStats(1.4, 1.3, 4.8, 2.1),
    "valencia":           TeamVenueStats(0.8, 1.5, 4.0, 2.9),
    "getafe":             TeamVenueStats(0.6, 1.1, 3.0, 3.5),
    "celta":              TeamVenueStats(0.9, 1.5, 3.8, 2.5),
    "celta vigo":         TeamVenueStats(0.9, 1.5, 3.8, 2.5),
    "osasuna":            TeamVenueStats(0.8, 1.3, 4.0, 2.7),
    "rayo vallecano":     TeamVenueStats(0.8, 1.4, 3.8, 3.0),
    "mallorca":           TeamVenueStats(0.7, 1.2, 3.5, 2.6),
    "las palmas":         TeamVenueStats(0.7, 1.7, 3.5, 2.5),
    "alaves":             TeamVenueStats(0.6, 1.5, 3.2, 2.8),
    "cadiz":              TeamVenueStats(0.5, 1.7, 3.0, 2.8),
    "almeria":            TeamVenueStats(0.6, 1.9, 3.2, 2.6),
    "granada":            TeamVenueStats(0.5, 2.0, 3.0, 2.9),
    "espanyol":           TeamVenueStats(0.7, 1.4, 3.5, 2.6),
    "leganes":            TeamVenueStats(0.6, 1.5, 3.2, 2.7),
    "valladolid":         TeamVenueStats(0.5, 1.6, 3.0, 2.8),
    "real valladolid":    TeamVenueStats(0.5, 1.6, 3.0, 2.8),
}

# ─── SERIE A ──────────────────────────────────────────────────────────

SERIEA_HOME = {
    "inter":              TeamVenueStats(2.0, 0.7, 5.8, 2.2),
    "napoli":             TeamVenueStats(1.9, 0.8, 5.5, 2.0),
    "ac milan":           TeamVenueStats(1.7, 1.0, 5.5, 2.3),
    "milan":              TeamVenueStats(1.7, 1.0, 5.5, 2.3),
    "juventus":           TeamVenueStats(1.5, 0.7, 5.3, 2.1),
    "atalanta":           TeamVenueStats(2.1, 0.9, 6.0, 2.2),
    "roma":               TeamVenueStats(1.5, 1.0, 5.0, 2.5),
    "lazio":              TeamVenueStats(1.7, 1.1, 5.3, 2.4),
    "fiorentina":         TeamVenueStats(1.6, 1.0, 5.0, 2.3),
    "bologna":            TeamVenueStats(1.4, 0.9, 5.2, 2.1),
    "torino":             TeamVenueStats(1.3, 1.1, 4.8, 2.5),
    "monza":              TeamVenueStats(1.0, 1.3, 4.2, 2.4),
    "udinese":            TeamVenueStats(1.2, 1.2, 4.5, 2.6),
    "sassuolo":           TeamVenueStats(1.1, 1.5, 4.3, 2.3),
    "empoli":             TeamVenueStats(1.0, 1.2, 4.0, 2.5),
    "cagliari":           TeamVenueStats(1.1, 1.3, 4.3, 2.7),
    "genoa":              TeamVenueStats(1.1, 1.2, 4.2, 2.6),
    "lecce":              TeamVenueStats(0.9, 1.3, 3.8, 2.5),
    "verona":             TeamVenueStats(1.0, 1.5, 4.0, 2.8),
    "salernitana":        TeamVenueStats(0.8, 1.7, 3.5, 2.6),
    "frosinone":          TeamVenueStats(0.9, 1.6, 3.8, 2.5),
    "como":               TeamVenueStats(1.0, 1.4, 4.0, 2.3),
    "parma":              TeamVenueStats(1.1, 1.3, 4.3, 2.2),
    "venezia":            TeamVenueStats(0.9, 1.5, 3.8, 2.5),
}

SERIEA_AWAY = {
    "inter":              TeamVenueStats(1.5, 1.0, 5.0, 2.4),
    "napoli":             TeamVenueStats(1.4, 1.1, 4.8, 2.2),
    "ac milan":           TeamVenueStats(1.2, 1.3, 4.5, 2.5),
    "milan":              TeamVenueStats(1.2, 1.3, 4.5, 2.5),
    "juventus":           TeamVenueStats(1.1, 0.9, 4.5, 2.3),
    "atalanta":           TeamVenueStats(1.6, 1.2, 5.2, 2.4),
    "roma":               TeamVenueStats(1.1, 1.3, 4.2, 2.7),
    "lazio":              TeamVenueStats(1.3, 1.4, 4.5, 2.6),
    "fiorentina":         TeamVenueStats(1.2, 1.2, 4.2, 2.5),
    "bologna":            TeamVenueStats(1.0, 1.2, 4.3, 2.3),
    "torino":             TeamVenueStats(0.9, 1.4, 4.0, 2.7),
    "monza":              TeamVenueStats(0.7, 1.5, 3.5, 2.6),
    "udinese":            TeamVenueStats(0.8, 1.5, 3.8, 2.8),
    "sassuolo":           TeamVenueStats(0.7, 1.8, 3.5, 2.5),
    "empoli":             TeamVenueStats(0.7, 1.4, 3.5, 2.7),
    "cagliari":           TeamVenueStats(0.7, 1.6, 3.5, 2.9),
    "genoa":              TeamVenueStats(0.7, 1.5, 3.5, 2.8),
    "lecce":              TeamVenueStats(0.6, 1.6, 3.2, 2.7),
    "verona":             TeamVenueStats(0.7, 1.8, 3.3, 3.0),
    "salernitana":        TeamVenueStats(0.5, 2.0, 3.0, 2.8),
    "frosinone":          TeamVenueStats(0.6, 1.9, 3.2, 2.7),
    "como":               TeamVenueStats(0.7, 1.6, 3.5, 2.5),
    "parma":              TeamVenueStats(0.8, 1.5, 3.8, 2.4),
    "venezia":            TeamVenueStats(0.6, 1.7, 3.2, 2.7),
}

# ─── BUNDESLIGA ───────────────────────────────────────────────────────

BUNDES_HOME = {
    "bayern":             TeamVenueStats(2.8, 0.9, 7.5, 1.8),
    "bayern munich":      TeamVenueStats(2.8, 0.9, 7.5, 1.8),
    "leverkusen":         TeamVenueStats(2.3, 0.8, 6.5, 1.7),
    "bayer leverkusen":   TeamVenueStats(2.3, 0.8, 6.5, 1.7),
    "dortmund":           TeamVenueStats(2.1, 1.2, 6.0, 2.0),
    "borussia dortmund":  TeamVenueStats(2.1, 1.2, 6.0, 2.0),
    "rb leipzig":         TeamVenueStats(2.0, 0.9, 6.0, 1.9),
    "leipzig":            TeamVenueStats(2.0, 0.9, 6.0, 1.9),
    "stuttgart":          TeamVenueStats(1.9, 1.0, 5.8, 2.0),
    "frankfurt":          TeamVenueStats(1.7, 1.1, 5.5, 2.2),
    "eintracht frankfurt": TeamVenueStats(1.7, 1.1, 5.5, 2.2),
    "freiburg":           TeamVenueStats(1.5, 1.0, 5.0, 1.8),
    "wolfsburg":          TeamVenueStats(1.4, 1.1, 5.2, 2.0),
    "hoffenheim":         TeamVenueStats(1.5, 1.3, 5.3, 2.1),
    "union berlin":       TeamVenueStats(1.1, 1.2, 4.5, 2.3),
    "gladbach":           TeamVenueStats(1.4, 1.3, 5.0, 2.1),
    "borussia m'gladbach": TeamVenueStats(1.4, 1.3, 5.0, 2.1),
    "werder bremen":      TeamVenueStats(1.4, 1.2, 5.0, 2.2),
    "bremen":             TeamVenueStats(1.4, 1.2, 5.0, 2.2),
    "mainz":              TeamVenueStats(1.3, 1.2, 4.8, 2.2),
    "augsburg":           TeamVenueStats(1.1, 1.4, 4.3, 2.5),
    "bochum":             TeamVenueStats(0.9, 1.6, 4.0, 2.6),
    "heidenheim":         TeamVenueStats(1.2, 1.3, 4.5, 2.1),
    "darmstadt":          TeamVenueStats(0.8, 1.8, 3.5, 2.7),
    "koln":               TeamVenueStats(1.0, 1.5, 4.2, 2.4),
    "st. pauli":          TeamVenueStats(1.1, 1.2, 4.5, 2.2),
    "holstein kiel":      TeamVenueStats(1.0, 1.6, 4.0, 2.3),
}

BUNDES_AWAY = {
    "bayern":             TeamVenueStats(2.3, 1.1, 6.5, 2.0),
    "bayern munich":      TeamVenueStats(2.3, 1.1, 6.5, 2.0),
    "leverkusen":         TeamVenueStats(1.8, 1.0, 5.8, 1.9),
    "bayer leverkusen":   TeamVenueStats(1.8, 1.0, 5.8, 1.9),
    "dortmund":           TeamVenueStats(1.6, 1.5, 5.2, 2.2),
    "borussia dortmund":  TeamVenueStats(1.6, 1.5, 5.2, 2.2),
    "rb leipzig":         TeamVenueStats(1.5, 1.2, 5.2, 2.1),
    "leipzig":            TeamVenueStats(1.5, 1.2, 5.2, 2.1),
    "stuttgart":          TeamVenueStats(1.4, 1.3, 5.0, 2.2),
    "frankfurt":          TeamVenueStats(1.2, 1.4, 4.5, 2.4),
    "eintracht frankfurt": TeamVenueStats(1.2, 1.4, 4.5, 2.4),
    "freiburg":           TeamVenueStats(1.1, 1.3, 4.2, 2.0),
    "wolfsburg":          TeamVenueStats(1.0, 1.4, 4.2, 2.2),
    "hoffenheim":         TeamVenueStats(1.1, 1.5, 4.5, 2.3),
    "union berlin":       TeamVenueStats(0.8, 1.5, 3.8, 2.5),
    "gladbach":           TeamVenueStats(1.0, 1.5, 4.2, 2.3),
    "borussia m'gladbach": TeamVenueStats(1.0, 1.5, 4.2, 2.3),
    "werder bremen":      TeamVenueStats(1.0, 1.5, 4.2, 2.4),
    "bremen":             TeamVenueStats(1.0, 1.5, 4.2, 2.4),
    "mainz":              TeamVenueStats(0.9, 1.4, 4.0, 2.4),
    "augsburg":           TeamVenueStats(0.7, 1.7, 3.5, 2.7),
    "bochum":             TeamVenueStats(0.5, 2.0, 3.2, 2.9),
    "heidenheim":         TeamVenueStats(0.8, 1.6, 3.8, 2.3),
    "darmstadt":          TeamVenueStats(0.5, 2.2, 3.0, 2.9),
    "koln":               TeamVenueStats(0.7, 1.8, 3.5, 2.6),
    "st. pauli":          TeamVenueStats(0.8, 1.5, 3.8, 2.4),
    "holstein kiel":      TeamVenueStats(0.6, 1.9, 3.2, 2.5),
}

# ─── LIGUE 1 ──────────────────────────────────────────────────────────

LIGUE1_HOME = {
    "psg":                TeamVenueStats(2.5, 0.7, 7.0, 1.8),
    "paris saint-germain": TeamVenueStats(2.5, 0.7, 7.0, 1.8),
    "paris saint germain": TeamVenueStats(2.5, 0.7, 7.0, 1.8),
    "marseille":          TeamVenueStats(1.7, 0.9, 5.5, 2.3),
    "olympique marseille": TeamVenueStats(1.7, 0.9, 5.5, 2.3),
    "monaco":             TeamVenueStats(1.8, 1.0, 5.5, 2.0),
    "lille":              TeamVenueStats(1.5, 0.8, 5.0, 1.9),
    "lyon":               TeamVenueStats(1.7, 1.1, 5.5, 2.1),
    "olympique lyonnais": TeamVenueStats(1.7, 1.1, 5.5, 2.1),
    "nice":               TeamVenueStats(1.4, 0.9, 5.0, 2.2),
    "lens":               TeamVenueStats(1.5, 0.8, 5.3, 2.0),
    "rennes":             TeamVenueStats(1.4, 1.1, 5.0, 2.1),
    "toulouse":           TeamVenueStats(1.3, 1.1, 4.8, 2.2),
    "strasbourg":         TeamVenueStats(1.3, 1.2, 4.8, 2.3),
    "brest":              TeamVenueStats(1.5, 1.0, 5.0, 2.0),
    "reims":              TeamVenueStats(1.1, 1.1, 4.3, 2.1),
    "montpellier":        TeamVenueStats(1.2, 1.4, 4.5, 2.4),
    "nantes":             TeamVenueStats(1.1, 1.2, 4.3, 2.3),
    "le havre":           TeamVenueStats(1.0, 1.3, 4.0, 2.4),
    "lorient":            TeamVenueStats(0.9, 1.5, 3.8, 2.3),
    "clermont":           TeamVenueStats(1.0, 1.5, 4.0, 2.2),
    "metz":               TeamVenueStats(0.9, 1.4, 3.8, 2.5),
    "auxerre":            TeamVenueStats(1.1, 1.2, 4.3, 2.2),
    "angers":             TeamVenueStats(0.9, 1.4, 3.8, 2.4),
    "saint-etienne":      TeamVenueStats(1.0, 1.3, 4.0, 2.5),
}

LIGUE1_AWAY = {
    "psg":                TeamVenueStats(2.0, 1.0, 6.0, 2.0),
    "paris saint-germain": TeamVenueStats(2.0, 1.0, 6.0, 2.0),
    "paris saint germain": TeamVenueStats(2.0, 1.0, 6.0, 2.0),
    "marseille":          TeamVenueStats(1.3, 1.2, 4.8, 2.5),
    "olympique marseille": TeamVenueStats(1.3, 1.2, 4.8, 2.5),
    "monaco":             TeamVenueStats(1.4, 1.3, 4.8, 2.2),
    "lille":              TeamVenueStats(1.1, 1.1, 4.5, 2.1),
    "lyon":               TeamVenueStats(1.3, 1.4, 4.8, 2.3),
    "olympique lyonnais": TeamVenueStats(1.3, 1.4, 4.8, 2.3),
    "nice":               TeamVenueStats(1.0, 1.2, 4.2, 2.4),
    "lens":               TeamVenueStats(1.1, 1.1, 4.5, 2.2),
    "rennes":             TeamVenueStats(1.0, 1.4, 4.2, 2.3),
    "toulouse":           TeamVenueStats(0.9, 1.4, 4.0, 2.4),
    "strasbourg":         TeamVenueStats(0.9, 1.5, 4.0, 2.5),
    "brest":              TeamVenueStats(1.1, 1.3, 4.2, 2.2),
    "reims":              TeamVenueStats(0.8, 1.4, 3.5, 2.3),
    "montpellier":        TeamVenueStats(0.8, 1.7, 3.8, 2.6),
    "nantes":             TeamVenueStats(0.7, 1.5, 3.5, 2.5),
    "le havre":           TeamVenueStats(0.6, 1.6, 3.2, 2.6),
    "lorient":            TeamVenueStats(0.6, 1.8, 3.2, 2.5),
    "clermont":           TeamVenueStats(0.6, 1.8, 3.2, 2.4),
    "metz":               TeamVenueStats(0.6, 1.7, 3.2, 2.7),
    "auxerre":            TeamVenueStats(0.7, 1.5, 3.5, 2.4),
    "angers":             TeamVenueStats(0.6, 1.7, 3.2, 2.6),
    "saint-etienne":      TeamVenueStats(0.7, 1.6, 3.3, 2.7),
}

# ─── UCL ──────────────────────────────────────────────────────────────
# Use domestic stats as base for UCL — these teams appear across leagues

UCL_HOME = {**PL_HOME, **LALIGA_HOME, **SERIEA_HOME, **BUNDES_HOME, **LIGUE1_HOME}
UCL_AWAY = {**PL_AWAY, **LALIGA_AWAY, **SERIEA_AWAY, **BUNDES_AWAY, **LIGUE1_AWAY}

# ─── INTERNATIONAL / NATIONAL TEAMS ──────────────────────────────────
# Realistic stats for national teams based on recent international performance.
# Format: (avg_goals_scored, avg_goals_conceded, avg_corners, avg_cards)
# Home = host/designated home side; Away = visiting/neutral

INTL_HOME = {
    # ── Elite Tier (FIFA Top 10) ──
    "argentina":          TeamVenueStats(2.0, 0.5, 5.5, 2.0),
    "france":             TeamVenueStats(1.9, 0.6, 5.8, 1.8),
    "brazil":             TeamVenueStats(1.8, 0.6, 5.5, 2.2),
    "england":            TeamVenueStats(1.9, 0.7, 5.5, 1.6),
    "belgium":            TeamVenueStats(1.7, 0.7, 5.0, 1.9),
    "portugal":           TeamVenueStats(2.0, 0.6, 5.8, 2.0),
    "netherlands":        TeamVenueStats(1.8, 0.7, 5.3, 1.7),
    "spain":              TeamVenueStats(1.9, 0.5, 6.0, 1.8),
    "italy":              TeamVenueStats(1.5, 0.6, 5.0, 2.0),
    "croatia":            TeamVenueStats(1.6, 0.7, 5.0, 2.1),
    "germany":            TeamVenueStats(2.0, 0.8, 5.8, 1.7),
    "colombia":           TeamVenueStats(1.6, 0.7, 4.8, 2.3),
    "uruguay":            TeamVenueStats(1.7, 0.7, 4.5, 2.5),
    # ── Strong Tier (FIFA 11-25) ──
    "mexico":             TeamVenueStats(1.5, 0.8, 4.8, 2.2),
    "usa":                TeamVenueStats(1.5, 0.8, 4.8, 1.8),
    "united states":      TeamVenueStats(1.5, 0.8, 4.8, 1.8),
    "denmark":            TeamVenueStats(1.5, 0.7, 5.0, 1.6),
    "switzerland":        TeamVenueStats(1.4, 0.7, 4.8, 1.8),
    "japan":              TeamVenueStats(1.6, 0.8, 5.0, 1.5),
    "senegal":            TeamVenueStats(1.3, 0.7, 4.5, 2.3),
    "iran":               TeamVenueStats(1.4, 0.8, 4.3, 2.2),
    "morocco":            TeamVenueStats(1.5, 0.6, 4.8, 2.0),
    "south korea":        TeamVenueStats(1.3, 0.8, 5.0, 1.8),
    "korea republic":     TeamVenueStats(1.3, 0.8, 5.0, 1.8),
    "austria":            TeamVenueStats(1.5, 0.9, 5.0, 1.9),
    "ukraine":            TeamVenueStats(1.3, 0.8, 4.8, 1.8),
    "wales":              TeamVenueStats(1.2, 0.9, 4.5, 1.9),
    "turkey":             TeamVenueStats(1.4, 0.9, 4.8, 2.3),
    "poland":             TeamVenueStats(1.3, 0.9, 4.5, 2.0),
    "scotland":           TeamVenueStats(1.3, 1.0, 4.8, 1.8),
    "nigeria":            TeamVenueStats(1.3, 0.8, 4.3, 2.2),
    "australia":          TeamVenueStats(1.3, 1.0, 4.5, 1.8),
    "egypt":              TeamVenueStats(1.2, 0.7, 4.3, 2.1),
    "serbia":             TeamVenueStats(1.4, 0.9, 4.8, 2.2),
    "sweden":             TeamVenueStats(1.3, 0.8, 4.8, 1.7),
    "norway":             TeamVenueStats(1.5, 0.9, 5.0, 1.6),
    "czech republic":     TeamVenueStats(1.3, 0.9, 4.5, 1.9),
    "czechia":            TeamVenueStats(1.3, 0.9, 4.5, 1.9),
    "hungary":            TeamVenueStats(1.2, 0.9, 4.3, 2.0),
    "romania":            TeamVenueStats(1.2, 0.9, 4.3, 1.9),
    "slovakia":           TeamVenueStats(1.1, 0.9, 4.2, 2.0),
    "slovenia":           TeamVenueStats(1.1, 0.9, 4.2, 1.8),
    "greece":             TeamVenueStats(1.2, 0.8, 4.5, 2.1),
    "russia":             TeamVenueStats(1.3, 0.9, 4.5, 2.0),
    "algeria":            TeamVenueStats(1.3, 0.8, 4.0, 2.3),
    "tunisia":            TeamVenueStats(1.2, 0.8, 4.0, 2.2),
    "cameroon":           TeamVenueStats(1.2, 0.9, 4.0, 2.4),
    "ghana":              TeamVenueStats(1.2, 0.9, 4.0, 2.3),
    "ivory coast":        TeamVenueStats(1.3, 0.8, 4.2, 2.2),
    "côte d'ivoire":      TeamVenueStats(1.3, 0.8, 4.2, 2.2),
    "chile":              TeamVenueStats(1.3, 0.9, 4.5, 2.3),
    "peru":               TeamVenueStats(1.1, 0.8, 4.0, 2.2),
    "ecuador":            TeamVenueStats(1.3, 0.8, 4.3, 2.3),
    "paraguay":           TeamVenueStats(1.1, 0.9, 4.0, 2.4),
    "venezuela":          TeamVenueStats(1.1, 0.9, 4.0, 2.1),
    "bolivia":            TeamVenueStats(1.3, 1.1, 4.0, 2.5),  # strong at altitude
    # ── Lower Tier ──
    "canada":             TeamVenueStats(1.2, 1.0, 4.3, 1.8),
    "costa rica":         TeamVenueStats(1.0, 0.9, 3.8, 2.0),
    "panama":             TeamVenueStats(0.9, 1.0, 3.5, 2.2),
    "jamaica":            TeamVenueStats(1.0, 1.0, 3.8, 2.1),
    "honduras":           TeamVenueStats(0.9, 1.1, 3.5, 2.4),
    "qatar":              TeamVenueStats(1.0, 1.0, 3.5, 1.9),
    "saudi arabia":       TeamVenueStats(1.1, 1.0, 3.8, 2.2),
    "iraq":               TeamVenueStats(1.0, 1.0, 3.5, 2.3),
    "china":              TeamVenueStats(0.9, 1.1, 3.5, 2.1),
    "china pr":           TeamVenueStats(0.9, 1.1, 3.5, 2.1),
    "india":              TeamVenueStats(0.8, 1.2, 3.3, 2.0),
    "new zealand":        TeamVenueStats(1.0, 1.1, 3.5, 1.8),
    "uzbekistan":         TeamVenueStats(1.1, 0.9, 3.8, 2.1),
    "jordan":             TeamVenueStats(1.0, 0.9, 3.5, 2.0),
    "bahrain":            TeamVenueStats(0.9, 1.0, 3.3, 2.1),
    "oman":               TeamVenueStats(0.9, 1.0, 3.3, 2.0),
    "palestine":          TeamVenueStats(0.8, 1.1, 3.2, 2.2),
    "indonesia":          TeamVenueStats(1.0, 1.1, 3.5, 2.2),
    "thailand":           TeamVenueStats(1.0, 1.1, 3.5, 2.0),
    "vietnam":            TeamVenueStats(0.9, 1.1, 3.3, 2.1),
    "north korea":        TeamVenueStats(0.9, 1.0, 3.5, 2.0),
    "south africa":       TeamVenueStats(1.1, 1.0, 3.8, 2.2),
    "congo dr":           TeamVenueStats(1.1, 0.9, 3.8, 2.3),
    "dr congo":           TeamVenueStats(1.1, 0.9, 3.8, 2.3),
    "mali":               TeamVenueStats(1.1, 0.9, 3.8, 2.3),
    "burkina faso":       TeamVenueStats(1.0, 0.9, 3.5, 2.2),
    "iceland":            TeamVenueStats(1.1, 0.9, 4.0, 1.7),
    "georgia":            TeamVenueStats(1.1, 0.9, 4.0, 1.9),
    "albania":            TeamVenueStats(1.0, 0.9, 3.8, 2.1),
    "north macedonia":    TeamVenueStats(1.0, 1.0, 3.8, 2.0),
    "bosnia":             TeamVenueStats(1.1, 0.9, 4.0, 2.1),
    "bosnia & herzegovina": TeamVenueStats(1.1, 0.9, 4.0, 2.1),
    "montenegro":         TeamVenueStats(1.0, 1.0, 3.8, 2.0),
    "northern ireland":   TeamVenueStats(0.9, 1.0, 3.8, 1.9),
    "republic of ireland": TeamVenueStats(1.0, 1.0, 4.0, 1.8),
    "ireland":            TeamVenueStats(1.0, 1.0, 4.0, 1.8),
    "finland":            TeamVenueStats(1.0, 0.9, 4.0, 1.7),
    "israel":             TeamVenueStats(1.1, 1.0, 4.0, 2.0),
    "cuba":               TeamVenueStats(0.8, 1.3, 3.2, 2.2),
    "el salvador":        TeamVenueStats(0.8, 1.2, 3.2, 2.3),
    "trinidad":           TeamVenueStats(0.8, 1.1, 3.2, 2.1),
    "trinidad and tobago": TeamVenueStats(0.8, 1.1, 3.2, 2.1),
    "haiti":              TeamVenueStats(0.8, 1.2, 3.2, 2.2),
    "bermuda":            TeamVenueStats(0.6, 1.5, 3.0, 2.0),
    "curacao":            TeamVenueStats(0.8, 1.2, 3.2, 2.0),
    "guyana":             TeamVenueStats(0.7, 1.3, 3.0, 2.1),
    "suriname":           TeamVenueStats(0.7, 1.2, 3.0, 2.0),
    "guatemala":          TeamVenueStats(0.9, 1.1, 3.3, 2.2),
    "nicaragua":          TeamVenueStats(0.7, 1.3, 3.0, 2.1),
}

INTL_AWAY = {
    # ── Elite Tier (FIFA Top 10) ──
    "argentina":          TeamVenueStats(1.7, 0.7, 5.0, 2.2),
    "france":             TeamVenueStats(1.6, 0.8, 5.2, 2.0),
    "brazil":             TeamVenueStats(1.5, 0.8, 5.0, 2.4),
    "england":            TeamVenueStats(1.5, 0.9, 5.0, 1.8),
    "belgium":            TeamVenueStats(1.4, 0.9, 4.5, 2.1),
    "portugal":           TeamVenueStats(1.6, 0.8, 5.2, 2.2),
    "netherlands":        TeamVenueStats(1.5, 0.9, 4.8, 1.9),
    "spain":              TeamVenueStats(1.6, 0.7, 5.5, 2.0),
    "italy":              TeamVenueStats(1.2, 0.8, 4.5, 2.2),
    "croatia":            TeamVenueStats(1.3, 0.9, 4.5, 2.3),
    "germany":            TeamVenueStats(1.6, 1.0, 5.2, 1.9),
    "colombia":           TeamVenueStats(1.3, 0.9, 4.3, 2.5),
    "uruguay":            TeamVenueStats(1.3, 0.9, 4.0, 2.7),
    # ── Strong Tier (FIFA 11-25) ──
    "mexico":             TeamVenueStats(1.2, 1.0, 4.3, 2.4),
    "usa":                TeamVenueStats(1.2, 1.0, 4.3, 2.0),
    "united states":      TeamVenueStats(1.2, 1.0, 4.3, 2.0),
    "denmark":            TeamVenueStats(1.2, 0.9, 4.5, 1.8),
    "switzerland":        TeamVenueStats(1.1, 0.9, 4.3, 2.0),
    "japan":              TeamVenueStats(1.3, 1.0, 4.5, 1.7),
    "senegal":            TeamVenueStats(1.0, 0.9, 4.0, 2.5),
    "iran":               TeamVenueStats(1.1, 1.0, 3.8, 2.4),
    "morocco":            TeamVenueStats(1.2, 0.8, 4.3, 2.2),
    "south korea":        TeamVenueStats(1.0, 1.0, 4.5, 2.0),
    "korea republic":     TeamVenueStats(1.0, 1.0, 4.5, 2.0),
    "austria":            TeamVenueStats(1.2, 1.1, 4.5, 2.1),
    "ukraine":            TeamVenueStats(1.0, 1.0, 4.3, 2.0),
    "wales":              TeamVenueStats(0.9, 1.1, 4.0, 2.1),
    "turkey":             TeamVenueStats(1.1, 1.1, 4.3, 2.5),
    "poland":             TeamVenueStats(1.0, 1.1, 4.0, 2.2),
    "scotland":           TeamVenueStats(1.0, 1.2, 4.3, 2.0),
    "nigeria":            TeamVenueStats(1.0, 1.0, 3.8, 2.4),
    "australia":          TeamVenueStats(1.0, 1.2, 4.0, 2.0),
    "egypt":              TeamVenueStats(0.9, 0.9, 3.8, 2.3),
    "serbia":             TeamVenueStats(1.1, 1.1, 4.3, 2.4),
    "sweden":             TeamVenueStats(1.0, 1.0, 4.3, 1.9),
    "norway":             TeamVenueStats(1.2, 1.1, 4.5, 1.8),
    "czech republic":     TeamVenueStats(1.0, 1.1, 4.0, 2.1),
    "czechia":            TeamVenueStats(1.0, 1.1, 4.0, 2.1),
    "hungary":            TeamVenueStats(0.9, 1.1, 3.8, 2.2),
    "romania":            TeamVenueStats(0.9, 1.1, 3.8, 2.1),
    "slovakia":           TeamVenueStats(0.8, 1.1, 3.5, 2.2),
    "slovenia":           TeamVenueStats(0.8, 1.1, 3.5, 2.0),
    "greece":             TeamVenueStats(0.9, 1.0, 4.0, 2.3),
    "russia":             TeamVenueStats(1.0, 1.1, 4.0, 2.2),
    "algeria":            TeamVenueStats(1.0, 1.0, 3.5, 2.5),
    "tunisia":            TeamVenueStats(0.9, 1.0, 3.5, 2.4),
    "cameroon":           TeamVenueStats(0.9, 1.1, 3.5, 2.6),
    "ghana":              TeamVenueStats(0.9, 1.1, 3.5, 2.5),
    "ivory coast":        TeamVenueStats(1.0, 1.0, 3.8, 2.4),
    "côte d'ivoire":      TeamVenueStats(1.0, 1.0, 3.8, 2.4),
    "chile":              TeamVenueStats(1.0, 1.1, 4.0, 2.5),
    "peru":               TeamVenueStats(0.8, 1.0, 3.5, 2.4),
    "ecuador":            TeamVenueStats(1.0, 1.0, 3.8, 2.5),
    "paraguay":           TeamVenueStats(0.8, 1.1, 3.5, 2.6),
    "venezuela":          TeamVenueStats(0.8, 1.1, 3.5, 2.3),
    "bolivia":            TeamVenueStats(0.7, 1.5, 3.2, 2.7),  # very weak away (low altitude)
    # ── Lower Tier ──
    "canada":             TeamVenueStats(0.9, 1.2, 3.8, 2.0),
    "costa rica":         TeamVenueStats(0.7, 1.1, 3.3, 2.2),
    "panama":             TeamVenueStats(0.6, 1.2, 3.0, 2.4),
    "jamaica":            TeamVenueStats(0.7, 1.2, 3.3, 2.3),
    "honduras":           TeamVenueStats(0.6, 1.3, 3.0, 2.6),
    "qatar":              TeamVenueStats(0.7, 1.2, 3.0, 2.1),
    "saudi arabia":       TeamVenueStats(0.8, 1.2, 3.3, 2.4),
    "iraq":               TeamVenueStats(0.7, 1.2, 3.0, 2.5),
    "china":              TeamVenueStats(0.6, 1.3, 3.0, 2.3),
    "china pr":           TeamVenueStats(0.6, 1.3, 3.0, 2.3),
    "india":              TeamVenueStats(0.5, 1.4, 2.8, 2.2),
    "new zealand":        TeamVenueStats(0.7, 1.3, 3.0, 2.0),
    "uzbekistan":         TeamVenueStats(0.8, 1.1, 3.3, 2.3),
    "jordan":             TeamVenueStats(0.7, 1.1, 3.0, 2.2),
    "bahrain":            TeamVenueStats(0.6, 1.2, 2.8, 2.3),
    "oman":               TeamVenueStats(0.6, 1.2, 2.8, 2.2),
    "palestine":          TeamVenueStats(0.5, 1.3, 2.8, 2.4),
    "indonesia":          TeamVenueStats(0.7, 1.3, 3.0, 2.4),
    "thailand":           TeamVenueStats(0.7, 1.3, 3.0, 2.2),
    "vietnam":            TeamVenueStats(0.6, 1.3, 2.8, 2.3),
    "north korea":        TeamVenueStats(0.6, 1.2, 3.0, 2.2),
    "south africa":       TeamVenueStats(0.8, 1.2, 3.3, 2.4),
    "congo dr":           TeamVenueStats(0.8, 1.1, 3.3, 2.5),
    "dr congo":           TeamVenueStats(0.8, 1.1, 3.3, 2.5),
    "mali":               TeamVenueStats(0.8, 1.1, 3.3, 2.5),
    "burkina faso":       TeamVenueStats(0.7, 1.1, 3.0, 2.4),
    "iceland":            TeamVenueStats(0.8, 1.1, 3.5, 1.9),
    "georgia":            TeamVenueStats(0.8, 1.1, 3.5, 2.1),
    "albania":            TeamVenueStats(0.7, 1.1, 3.3, 2.3),
    "north macedonia":    TeamVenueStats(0.7, 1.2, 3.3, 2.2),
    "bosnia":             TeamVenueStats(0.8, 1.1, 3.5, 2.3),
    "bosnia & herzegovina": TeamVenueStats(0.8, 1.1, 3.5, 2.3),
    "montenegro":         TeamVenueStats(0.7, 1.2, 3.3, 2.2),
    "northern ireland":   TeamVenueStats(0.6, 1.2, 3.3, 2.1),
    "republic of ireland": TeamVenueStats(0.7, 1.2, 3.5, 2.0),
    "ireland":            TeamVenueStats(0.7, 1.2, 3.5, 2.0),
    "finland":            TeamVenueStats(0.7, 1.1, 3.5, 1.9),
    "israel":             TeamVenueStats(0.8, 1.2, 3.5, 2.2),
    "cuba":               TeamVenueStats(0.5, 1.5, 2.8, 2.4),
    "el salvador":        TeamVenueStats(0.5, 1.4, 2.8, 2.5),
    "trinidad":           TeamVenueStats(0.5, 1.3, 2.8, 2.3),
    "trinidad and tobago": TeamVenueStats(0.5, 1.3, 2.8, 2.3),
    "haiti":              TeamVenueStats(0.5, 1.4, 2.8, 2.4),
    "bermuda":            TeamVenueStats(0.3, 1.8, 2.5, 2.2),
    "curacao":            TeamVenueStats(0.5, 1.4, 2.8, 2.2),
    "guyana":             TeamVenueStats(0.4, 1.5, 2.5, 2.3),
    "suriname":           TeamVenueStats(0.4, 1.4, 2.5, 2.2),
    "guatemala":          TeamVenueStats(0.6, 1.3, 2.8, 2.4),
    "nicaragua":          TeamVenueStats(0.4, 1.5, 2.5, 2.3),
}

# ─── League name → stats lookup ───────────────────────────────────────

LEAGUE_STATS = {
    "Premier League":     (PL_HOME, PL_AWAY),
    "LaLiga":             (LALIGA_HOME, LALIGA_AWAY),
    "La Liga":            (LALIGA_HOME, LALIGA_AWAY),
    "Serie A":            (SERIEA_HOME, SERIEA_AWAY),
    "Bundesliga":         (BUNDES_HOME, BUNDES_AWAY),
    "Ligue 1":            (LIGUE1_HOME, LIGUE1_AWAY),
    "Champions League":   (UCL_HOME, UCL_AWAY),
    "Europa League":      (UCL_HOME, UCL_AWAY),
    "International":      (INTL_HOME, INTL_AWAY),
    "World Cup":          (INTL_HOME, INTL_AWAY),
    "FIFA World Cup":     (INTL_HOME, INTL_AWAY),
}

# ─── Merged lookup for any league ─────────────────────────────────────

ALL_HOME = {**PL_HOME, **LALIGA_HOME, **SERIEA_HOME, **BUNDES_HOME, **LIGUE1_HOME, **INTL_HOME}
ALL_AWAY = {**PL_AWAY, **LALIGA_AWAY, **SERIEA_AWAY, **BUNDES_AWAY, **LIGUE1_AWAY, **INTL_AWAY}

# ─── International team name aliases (SofaScore/API → canonical key) ──
# Maps common API name variants to the canonical lowercase key used in
# INTL_HOME / INTL_AWAY. This fixes the #1 source of World Cup prediction
# bias: elite teams falling through to hash-generated average stats.

_TEAM_NAME_ALIASES: dict[str, str] = {
    # ── Middle East / Asia name variants ──
    "ir iran": "iran",
    "ir. iran": "iran",
    "islamic republic of iran": "iran",
    "korea republic": "south korea",
    "republic of korea": "south korea",
    "korea": "south korea",
    "korea dpr": "north korea",
    "dpr korea": "north korea",
    "chinese taipei": "china",
    "hong kong": "china",
    "uae": "saudi arabia",  # rough proxy
    "united arab emirates": "saudi arabia",  # rough proxy
    "kyrgyz republic": "uzbekistan",  # rough proxy
    "kyrgyzstan": "uzbekistan",  # rough proxy
    # ── Europe name variants ──
    "türkiye": "turkey",
    "turkiye": "turkey",
    "republic of ireland": "ireland",
    "eire": "ireland",
    "czech republic": "czechia",
    "bosnia and herzegovina": "bosnia",
    "bosnia & herzegovina": "bosnia",
    "north macedonia": "north macedonia",
    "fyrom": "north macedonia",
    "faroe islands": "iceland",  # rough proxy
    # ── Africa name variants ──
    "cote d'ivoire": "ivory coast",
    "côte d'ivoire": "ivory coast",
    "cote d'ivoire": "ivory coast",
    "dr congo": "congo dr",
    "democratic republic of congo": "congo dr",
    "congo": "cameroon",  # rough proxy for Republic of Congo
    "cape verde": "senegal",  # rough proxy
    "cabo verde": "senegal",  # rough proxy
    # ── Americas name variants ──
    "united states": "usa",
    "united states of america": "usa",
    "us": "usa",
    "trinidad and tobago": "trinidad",
    "trinidad & tobago": "trinidad",
    "antigua and barbuda": "jamaica",  # rough proxy
    "saint kitts and nevis": "jamaica",  # rough proxy
    "st. kitts and nevis": "jamaica",  # rough proxy
    "dominican republic": "haiti",  # rough proxy
    # ── Oceania ──
    "new caledonia": "new zealand",  # rough proxy
    "fiji": "new zealand",  # rough proxy
    "tahiti": "new zealand",  # rough proxy
}

# International leagues for fallback detection
_INTERNATIONAL_LEAGUES = {
    "International", "World Cup", "FIFA World Cup",
    "World Cup Qualification (Europe)", "World Cup Qualification (CONMEBOL)",
    "World Cup Qualification (Africa)", "World Cup Qualification (Asia)",
    "World Cup Qualification (CONCACAF)", "UEFA Nations League",
    "Copa America", "Euro Championship",
}

# Youth / gender markers — presence in a team name means it should NOT
# substring-match against senior men's national team entries.
_YOUTH_GENDER_MARKERS = {"u17", "u19", "u20", "u21", "u23", "olympic", "women", "women's"}


def is_neutral_tournament(league: str) -> bool:
    """Return True if a league/competition is played at neutral venues.

    For neutral tournaments (World Cup, Euros, Copa America, U17-U23
    championships, Olympics), "Home" and "Away" are purely administrative.
    No venue multiplier should be applied.
    """
    if not league:
        return False
    lg = league.lower()
    # Explicit set membership
    if league in _INTERNATIONAL_LEAGUES:
        return True
    # Keyword detection for tournaments not in the explicit set
    neutral_keywords = (
        "world cup", "euro", "copa america", "nations league",
        "olympic", "u17", "u19", "u20", "u21", "u23",
        "toulon", "tournoi", "shebelieves", "concacaf",
        "afc asian cup", "africa cup", "afcon", "gold cup",
    )
    return any(kw in lg for kw in neutral_keywords)


def _normalize_team_name(name: str) -> str:
    """Normalize a team name to its canonical lowercase key.

    Steps:
        1. Strip and lowercase
        2. Check alias dictionary for exact match
        3. Return normalized name
    """
    name_lower = name.lower().strip()
    # Direct alias lookup (handles "IR Iran" → "iran", "Korea Republic" → "south korea", etc.)
    if name_lower in _TEAM_NAME_ALIASES:
        return _TEAM_NAME_ALIASES[name_lower]
    return name_lower


def _lookup_in_db(db: dict, name_lower: str) -> TeamVenueStats | None:
    """Look up team stats using a 3-step matching strategy.

    Steps:
        1. Exact key match (fastest, most reliable)
        2. Alias-normalized match (handles API name variants)
        3. Substring match (fallback for partial names)

    This replaces the old single-pass `key in name_lower` which caused
    false positives ("iran" matching "Northern Iran FC") and misses
    ("Korea Republic" not matching "south korea").
    """
    # Step 1: Exact match
    if name_lower in db:
        return db[name_lower]

    # Step 2: Alias-normalized match
    canonical = _TEAM_NAME_ALIASES.get(name_lower)
    if canonical and canonical in db:
        return db[canonical]

    # Step 3: Substring match (original behavior, kept for club teams)
    # GUARD: If the query name contains youth/gender markers (e.g., "wales u19",
    # "germany u19"), do NOT fall through to substring matching — it would match
    # the senior men's team ("wales" is inside "wales u19").
    name_tokens = set(name_lower.split())
    if name_tokens & _YOUTH_GENDER_MARKERS:
        return None  # Only exact or alias matches are safe for youth/women teams

    # Sort keys by length descending so longer, more specific keys match first
    # (e.g., "borussia dortmund" before "borussia")
    for key in sorted(db.keys(), key=len, reverse=True):
        if key in name_lower or name_lower in key:
            return db[key]

    return None


def _resolve_static_corners(team_name: str, venue: str, league: str) -> float | None:
    """Look up the hardcoded static corner average for a team.

    Returns None if the team is not found in any static table.
    This is used to seed the corner lambda when the DB rolling_corners is
    still at its uninitialized default (5.0) — i.e. no real per-team corner
    data has been ingested yet (the DB only stores total_corners, not splits).
    """
    name_lower = team_name.lower().strip()

    if league in LEAGUE_STATS:
        home_db, away_db = LEAGUE_STATS[league]
        db = home_db if venue == "home" else away_db
        result = _lookup_in_db(db, name_lower)
        if result is not None:
            return result.corners

    db = ALL_HOME if venue == "home" else ALL_AWAY
    result = _lookup_in_db(db, name_lower)
    if result is not None:
        return result.corners

    return None


_DB_CORNERS_DEFAULT = 5.0   # matches TeamState.rolling_corners default


def get_team_stats(team_name: str, venue: str, league: str = "") -> TeamVenueStats:
    """
    Get team-specific stats — LIVE ADAPTIVE VERSION.

    Priority order:
        1. Live DB (team_state table) — if team has ≥1 match ingested
        2. Hardcoded league-specific lookup — season baseline
        3. Hardcoded all-leagues lookup — cross-league fallback
        4. International-aware defaults — for unknown international teams
        5. Hash-based generation — truly unknown club teams

    The live DB values are rolling exponentially-weighted averages
    that update after every match via on_match_finished().
    """
    # ── Priority 1: Live DB lookup (overall state — updates after EVERY match) ──
    # The mapped league key (e.g. "International") may differ from the raw league name
    # stored in the DB (e.g. "World Cup", "FIFA World Cup"). Try multiple variants.
    _LEAGUE_DB_ALIASES = {
        "International": [
            "World Cup", "FIFA World Cup", "FIFA World Cup 2026",
            "World Cup Qualification (Europe)", "World Cup Qualification (CONMEBOL)",
            "World Cup Qualification (Africa)", "World Cup Qualification (Asia)",
            "World Cup Qualification (CONCACAF)",
            "UEFA Nations League", "Copa America", "Euro Championship",
            "Euro 2024", "Euro 2028",
            "International Friendly Games",
            "AFC Asian Cup", "AFC Asian Cup Qual.",
            "Africa Cup of Nations", "AFCON",
            "Gold Cup", "CONCACAF Gold Cup",
        ],
    }
    leagues_to_try = [league] + _LEAGUE_DB_ALIASES.get(league, [])

    try:
        from src.db.database import get_db
        from src.db.team_state import get_team_state as get_live_state

        conn = get_db()

        _neutral = is_neutral_tournament(league)

        for try_league in leagues_to_try:
            # PRIMARY: overall row (written by on_match_finished regardless of venue)
            overall = get_live_state(conn, team_name, try_league, "overall")
            if overall is not None and overall.matches_played >= 1:
                if _neutral:
                    # Neutral venue: no home/away multiplier — both sides equal
                    scored   = round(overall.rolling_scored, 2)
                    conceded = round(overall.rolling_conceded, 2)
                else:
                    # Club football: home teams score ~12% more / concede ~12% less
                    if venue == "home":
                        scored   = round(overall.rolling_scored   * 1.12, 2)
                        conceded = round(overall.rolling_conceded * 0.88, 2)
                    else:
                        scored   = round(overall.rolling_scored   * 0.88, 2)
                        conceded = round(overall.rolling_conceded * 1.12, 2)
                # If rolling_corners is still the uninitialized default,
                # seed it from the static lookup (has per-team differentiation).
                _corners = overall.rolling_corners
                if _corners == _DB_CORNERS_DEFAULT:
                    _corners = _resolve_static_corners(team_name, venue, league) or _corners
                return TeamVenueStats(
                    scored=scored,
                    conceded=conceded,
                    corners=round(_corners, 1),
                    cards=round(overall.rolling_cards, 1),
                    matches_played=overall.matches_played,
                    form_last5=overall.form_last5,
                )

            # SECONDARY: venue-specific row (team has only played at one venue so far)
            live = get_live_state(conn, team_name, try_league, venue)
            if live is not None and live.matches_played >= 1:
                _corners = live.rolling_corners
                if _corners == _DB_CORNERS_DEFAULT:
                    _corners = _resolve_static_corners(team_name, venue, league) or _corners
                return TeamVenueStats(
                    scored=round(live.rolling_scored, 2),
                    conceded=round(live.rolling_conceded, 2),
                    corners=round(_corners, 1),
                    cards=round(live.rolling_cards, 1),
                    matches_played=live.matches_played,
                    form_last5=live.form_last5,
                )

        # TERTIARY: Fuzzy league match for international competitions
        # Catches variant tournament names the alias list doesn't anticipate
        if league in ("International",) or league in _INTERNATIONAL_LEAGUES:
            try:
                # Build gender/age filter to prevent cross-contamination:
                # If team is "Germany U19", exclude "Women" leagues.
                # If team is "Spain U19 Women", only match Women leagues.
                _tname_lower = team_name.lower().strip()
                _has_women = "women" in _tname_lower
                _gender_filter = ""
                if _has_women:
                    _gender_filter = "AND (league LIKE '%women%' OR league LIKE '%Women%')"
                else:
                    _gender_filter = "AND league NOT LIKE '%women%' AND league NOT LIKE '%Women%'"

                row = conn.execute(
                    f"""SELECT team_name, league, venue, elo,
                              rolling_scored, rolling_conceded,
                              rolling_corners, rolling_cards,
                              matches_played, form_last5
                       FROM team_state
                       WHERE team_name_lower = ? AND venue = 'overall'
                             AND (league LIKE '%world cup%' OR league LIKE '%international%'
                                  OR league LIKE '%nations league%' OR league LIKE '%euro%'
                                  OR league LIKE '%copa%' OR league LIKE '%qualification%'
                                  OR league LIKE '%friendly%' OR league LIKE '%championship%')
                             {_gender_filter}
                       ORDER BY matches_played DESC
                       LIMIT 1""",
                    (_tname_lower,),
                ).fetchone()
                if row and row["matches_played"] >= 1:
                    overall_scored = row["rolling_scored"]
                    overall_conceded = row["rolling_conceded"]
                    if _neutral:
                        # Neutral venue: no multiplier
                        scored   = round(overall_scored, 2)
                        conceded = round(overall_conceded, 2)
                    else:
                        if venue == "home":
                            scored   = round(overall_scored   * 1.12, 2)
                            conceded = round(overall_conceded * 0.88, 2)
                        else:
                            scored   = round(overall_scored   * 0.88, 2)
                            conceded = round(overall_conceded * 1.12, 2)
                    _corners = row["rolling_corners"]
                    if _corners == _DB_CORNERS_DEFAULT:
                        _corners = _resolve_static_corners(team_name, venue, league) or _corners
                    return TeamVenueStats(
                        scored=scored,
                        conceded=conceded,
                        corners=round(_corners, 1),
                        cards=round(row["rolling_cards"], 1),
                        matches_played=row["matches_played"],
                        form_last5=row["form_last5"],
                    )
            except Exception:
                pass
    except Exception:
        pass  # DB not available or import error → fall through to static

    # ── Priority 2: Hardcoded league-specific lookup (with alias normalization) ──
    name_lower = team_name.lower().strip()

    if league in LEAGUE_STATS:
        home_db, away_db = LEAGUE_STATS[league]
        db = home_db if venue == "home" else away_db
        result = _lookup_in_db(db, name_lower)
        if result is not None:
            return result

    # ── Priority 3: All-leagues fallback (with alias normalization) ──
    db = ALL_HOME if venue == "home" else ALL_AWAY
    result = _lookup_in_db(db, name_lower)
    if result is not None:
        return result

    # ── Priority 4: International-aware defaults for unknown national teams ──
    # If the league is international, use conservative weak-team defaults instead
    # of random hash-generated stats. This ensures unknown international teams
    # are treated as underdogs rather than randomly average or even elite.
    is_intl = (
        league in _INTERNATIONAL_LEAGUES
        or league == "International"
        or "world cup" in league.lower()
        or "international" in league.lower()
        or "nations league" in league.lower()
        or "euro" in league.lower()
        or "copa" in league.lower()
        or "qualification" in league.lower()
    )
    if is_intl:
        import logging
        logging.getLogger("football_predictor").warning(
            f"⚠️ Unknown international team '{team_name}' — using weak-team defaults (not hash)"
        )
        # Symmetric neutral defaults: no venue bias for unknown internationals.
        # Both nominal "home" and "away" get the same weak-team stats.
        return TeamVenueStats(
            scored=0.8, conceded=1.3, corners=3.2, cards=2.3,
            matches_played=0, form_last5=0.3,
        )

    # ── Priority 5: Unknown club team → hash-based (deterministic) ──
    # Ranges capped below average to prevent unknown teams from getting
    # elite-level stats (old ranges allowed scored=2.8, more than Man City).
    h = int(hashlib.md5(f"{team_name}:{venue}".encode()).hexdigest(), 16)

    if venue == "home":
        scored = 0.8 + (h % 100) / 100              # 0.8–1.8 (below PL avg ~1.5)
        conceded = 1.0 + ((h >> 16) % 100) / 100    # 1.0–2.0
        corners = 3.5 + ((h >> 32) % 25) / 10       # 3.5–6.0
        cards = 1.5 + ((h >> 48) % 15) / 10         # 1.5–3.0
    else:
        scored = 0.5 + (h % 80) / 100               # 0.5–1.3
        conceded = 1.2 + ((h >> 16) % 80) / 100     # 1.2–2.0
        corners = 3.0 + ((h >> 32) % 25) / 10       # 3.0–5.5
        cards = 1.8 + ((h >> 48) % 15) / 10         # 1.8–3.3

    return TeamVenueStats(
        scored=round(scored, 2),
        conceded=round(conceded, 2),
        corners=round(corners, 1),
        cards=round(cards, 1),
    )

