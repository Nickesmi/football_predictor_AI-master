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
}

# ─── Merged lookup for any league ─────────────────────────────────────

ALL_HOME = {**PL_HOME, **LALIGA_HOME, **SERIEA_HOME, **BUNDES_HOME, **LIGUE1_HOME}
ALL_AWAY = {**PL_AWAY, **LALIGA_AWAY, **SERIEA_AWAY, **BUNDES_AWAY, **LIGUE1_AWAY}


def get_team_stats(team_name: str, venue: str, league: str = "") -> TeamVenueStats:
    """
    Get team-specific stats. Tries league-specific lookup first,
    then falls back to all-leagues lookup, then generates from hash.

    Returns (scored, conceded, corners, cards).
    """
    name_lower = team_name.lower()

    # Try league-specific lookup
    if league in LEAGUE_STATS:
        home_db, away_db = LEAGUE_STATS[league]
        db = home_db if venue == "home" else away_db
        for key, stats in db.items():
            if key in name_lower:
                return stats

    # Fallback to all-leagues lookup
    db = ALL_HOME if venue == "home" else ALL_AWAY
    for key, stats in db.items():
        if key in name_lower:
            return stats

    # Unknown team → hash-based (deterministic but unique per team)
    h = int(hashlib.md5(f"{team_name}:{venue}".encode()).hexdigest(), 16)

    if venue == "home":
        scored = 0.8 + (h % 200) / 100
        conceded = 0.5 + ((h >> 16) % 160) / 100
        corners = 3.5 + ((h >> 32) % 40) / 10
        cards = 1.5 + ((h >> 48) % 20) / 10
    else:
        scored = 0.5 + (h % 180) / 100
        conceded = 0.7 + ((h >> 16) % 180) / 100
        corners = 3.0 + ((h >> 32) % 35) / 10
        cards = 1.8 + ((h >> 48) % 18) / 10

    return TeamVenueStats(
        scored=round(scored, 2),
        conceded=round(conceded, 2),
        corners=round(corners, 1),
        cards=round(cards, 1),
    )
