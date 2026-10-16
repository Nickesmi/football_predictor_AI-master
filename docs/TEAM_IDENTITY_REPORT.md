# Team Identity — Coverage, Ambiguity and Before/After (M3.1, K43)

Commit `140e216`. Code: `src/identity/competitions.py`, `src/identity/teams.py`, `src/identity/inputs.py`, `scripts/team_identity_report.py`. Tests: `tests/test_team_identity.py` (29). Data: `~/Desktop/football_predictor_backups/m3_2026-10-16/m3_1/team_identity_report.json`.

**Status:** the resolver and the identity-checked inputs (`resolve_probability_inputs_v2`) are implemented and used by the shadow runner. **The production default (`probability_engine.resolve_probability_inputs`) is unchanged.** Switching production is a material change to inputs and forecast coverage (§5), so it is stopped for approval as instructed.

## 1. Root causes found

| # | Defect in the legacy lookup (`src/ml/team_stats_db.get_team_stats`) | Effect |
|---|---|---|
| I1 | The Poisson **profile key** (`LEAGUE_KEY_MAP`) is also used as the **team-history key**. Championship maps to "Premier League"; "La Liga" maps to "LaLiga". | Championship teams' real `team_state` rows ("Championship", 5–7 matches) are never found. They fall through to hash-generated statistics. La Liga teams miss their "La Liga" rows. |
| I2 | Static-table **substring** fallback (`key in name or name in key`) | Another team's statistics are substituted. Real cases: "Rangers" → *angers*, "Gent" → *argentina*, "Beitar Jerusalem" → *usa*, "Dinamo Tirana" → *iran*, "Malisheva"/"Famalicao" → *mali*, "Lillestrom" → *lille*, "Inter Turku" / "Inter Club d'Escaldes" → *inter*, "Austria Vienna" → *austria* |
| I3 | Unknown clubs get **hash-generated** statistics | Synthetic numbers presented as team strength. `matches_played = 0` does flag them as blind. |
| I4 | Static tables claim `matches_played = 20` (dataclass default) with no observed history behind them | Data quality 100 for teams with no observed history (e.g. Espanyol in M3: dq 100) |
| I5 | `team_state` is keyed by competition **label** without country. "Premier League" holds about 25 competitions (England 39, Russia 235, Egypt 233, Wales 110, …); "Championship", "Serie A" (Italy + Brazil), "Premiership", "Ligue 1" and "Pro League" are mixed too. | Two clubs with the same name and label share one history row. **Found:** "Arsenal" / "Premier League" includes fixtures from competition 116, another country's "Premier League". |
| I6 | Feeds spell clubs differently ("SV Werder Bremen" vs "Werder Bremen", "1. FC Union Berlin" vs "Union Berlin", "Atlético" vs "Atletico") | Exact lowercase lookup misses or duplicates rows |

## 2. Resolver (team-identity-v1, deterministic, fail-closed)

1. **Competition** comes from the fixture's provider league id (`competition_for_fixture`). League names are never used. 14 tracked competitions, each with its API-Football and SofaScore ids and its `team_state` history labels.
2. **Explicit mapping first:** `team_identity_map`, keyed by (provider, provider team id, competition). It is re-validated on every use, and a mapping whose record disappeared becomes `STALE_EXPLICIT_MAPPING`. Shadow discovery stores API-Football team ids. A resolved identity is remembered with source `auto-verified` and the mapping version. Ambiguous identities are never remembered.
3. **Otherwise:** an exact normalised name inside the competition's own labels. Normalisation removes accents and punctuation, drops legal suffixes (FC/SC/SV/…) and a leading "1.", and applies the existing verified alias list. Youth, reserve and women's markers (U19/U21/II/B/W/Women) are kept. No substring or fuzzy matching.
4. **History provenance:** every stored fixture behind that record (same label, normalised name) must carry one of the competition's league ids.
   - fixtures from another competition → `CONTAMINATED_HISTORY`;
   - no fixtures at all → `UNVERIFIED_HISTORY`.
5. **Two different records normalising to one name** → `AMBIGUOUS`. These are never merged automatically. Each is listed for review with per-record verification; an explicit mapping, approved by the owner, can settle it.
6. **No fallback** to static tables, substring matches or hash statistics. `resolve_probability_inputs_v2` raises `UnresolvedTeamIdentity`, and the shadow records `NO_FORECAST: TEAM_IDENTITY_UNRESOLVED`.
7. **Provenance:** every v2 input carries `identity` (version and, per side: provider, provider team id, provider name, record, history label, matches, source, evidence).

**Mathematics unchanged.**
- The same Poisson profile key is used.
- The same venue transform as legacy priority 1 is applied (overall ×1.12/0.88).
- Neutral venue is decided from the legacy profile key. This avoids a latent trap: "UEFA Europa League" contains the neutral keyword "euro".
- The same `estimate_probabilities_from_inputs` is used.
- A parity test shows identical outputs when legacy and v2 find the same record. On real fixtures, 22 of 39 resolved fixtures are unchanged to the last digit.

## 3. Coverage (all teams in tracked-competition fixtures since 2026-07-01; 492 team-competition pairs)

| Resolver status | Teams | Share |
|---|---|---|
| RESOLVED | 461 | 93.7 % |
| AMBIGUOUS | 18 | 3.7 % |
| UNRESOLVED_NO_HISTORY | 11 | 2.2 % |
| UNVERIFIED_HISTORY | 1 | 0.2 % |
| CONTAMINATED_HISTORY | 1 | 0.2 % |

| Legacy source for the same teams | Teams |
|---|---|
| Hash-generated synthetic statistics | **317** |
| `team_state` under the profile key | 109 |
| Static league table, exact | 43 |
| Static table, **substring** (other team's statistics) | 23 |

### Review queue (fail-closed today)

| Status | Teams | Reading |
|---|---|---|
| AMBIGUOUS | Atletico Madrid, Barcelona, Paris Saint Germain, Brighton, Ajax, Twente, Utrecht, Groningen, Heerenveen, Telstar, Standard Liege, Arouca, Alverca, Nacional, Famalicao, Dundee, ST Mirren, ST Johnstone | Mostly one club spelled by two feeds under two labels (e.g. "La Liga" / "LaLiga"). Usually one record verifies and the other has no source fixtures. Brighton and PSG: both records verify ("same-club spelling split"). An explicit owner-approved mapping resolves each one. |
| UNRESOLVED_NO_HISTORY | Lyon, Marseille, Monaco, Lens, Strasbourg, Stade Brestois 29, Inter, Alanyaspor, Dundee Utd, OFI, Viking | The history exists under another feed's name ("Olympique Lyonnais", "AS Monaco", "RC Lens", …), but no stored fixture proves where those rows came from, so an alias alone would not verify them. |
| UNVERIFIED_HISTORY | Rangers | "Scottish Premiership" row (1 match) with no source fixture |
| CONTAMINATED_HISTORY | Arsenal | The English "Premier League" record mixes in competition 116 |

## 4. Before/after on upcoming fixtures (51 tracked fixtures, 2026-10-16/17)

| | Value |
|---|---|
| Fixtures | 51 |
| Identity resolved, both sides | 39 |
| Fail closed (no forecast) | 12: 8 ambiguous sides, 6 no history, 1 unverified |
| Resolved and unchanged (same record as legacy) | 22 |
| Resolved and changed | 17. Max change per fixture 3.2–37.4 pp (median about 21 pp). 26 of the 34 changed sides were hash-generated in legacy. |
| Mean data quality, resolved fixtures | **54.7 → 62.4** |
| Fixtures with data quality ≥ 60 | legacy 4 of 51 → v2 10 of 39 |

Largest changes (1X2 home/draw/away):

| Fixture | Legacy sources | dq | Legacy | v2 |
|---|---|---|---|---|
| Middlesbrough v Cardiff (Championship) | hash / hash | 50 → 80 | 28.3 / 33.2 / 38.6 | 52.5 / 22.0 / 25.5 |
| Burnley v Wolves (Championship) | "Premier League" row of a relegated club / static table | 52 → 58 | 51.5 / 26.8 / 21.7 | 19.4 / 21.5 / 59.1 |
| Hibernian v Falkirk (Premiership) | hash / hash | 50 → 58 | 55.3 / 21.4 / 23.4 | 41.8 / 30.1 / 28.1 |
| Millwall v Lincoln (Championship) | hash / hash | 50 → 84 | 38.7 / 26.7 / 34.6 | 66.6 / 18.4 / 15.0 |
| Valencia v Athletic Club (La Liga) | "LaLiga" rows (no source fixtures) | 52 → 58 | 54.8 / 27.0 / 18.2 | 28.1 / 28.1 / 43.8 |

M3 regression cases (from the K43 finding):
- Middlesbrough, Cardiff, Millwall, Lincoln, Watford, Charlton: hash → own Championship history (dq 50 → 80 / 84 / 58).
- Espanyol: static table (dq 100, "20 matches") → own La Liga history (4 matches).
- Union Berlin and Werder Bremen: resolved through "1. FC Union Berlin" / "SV Werder Bremen", verified by Bundesliga (78) fixtures.
- Atletico Madrid: AMBIGUOUS (fails closed).

**Data quality stays low after the fix.** The data-quality formula penalises anything under 15 observed matches. In mid-October teams have 3–8 recorded current-season matches, so most correctly identified fixtures still score below 60. This is a true lack of history, not an identity error. Policies v1/v1.1 reject on it.

## 5. Approval needed (material change)

Making v2 the production default would:
- change probabilities for every fixture where legacy used hash, static or substring statistics (here 17 of 39 by up to 37 pp);
- give **no forecast** to 12 of 51 fixtures until their identities are reviewed;
- record identity provenance in picks.

Model weights, probability mathematics and qualification thresholds are unchanged. Options:
- (a) adopt v2 for the official pipeline only (behind a version flag, before freezing);
- (b) also for match analysis and BILQE;
- (c) keep v2 in shadow until the review queue is cleared.

Any of these needs a golden-reference update for the official pipeline.
