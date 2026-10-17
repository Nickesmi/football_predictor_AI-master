# Team History Reconstruction Audit (M3.3 Package A)

Commit `dbea35d`. Code: `src/history/reconstruct.py` (history version **rh1**), `scripts/team_history_audit.py`. Tests: `tests/test_team_history_rh1.py` (12).

Data, on an isolated copy of the remediated candidate:
- `~/Desktop/football_predictor_backups/m3_2026-10-16/m3_3/engine_rh1.db`
- `m3_3/team_history_audit.json`

**The live database was not modified.**

## 1. Method (data-only)

| Aspect | Rule |
|---|---|
| Source | Every finished stored fixture (FT/AET/PEN with a score) whose **provider league id** belongs to one of the 14 tracked competitions. Labels are never used. |
| Team identity | (competition, normalised name). Youth, reserve and women's markers are kept (U19/U21/II/B/W/Women), so "Chelsea U21" ≠ "Chelsea". No substring, LIKE or fuzzy matching. |
| Feeds | The same real match stored by several feeds (key: competition, date, home key, away key) counts once. Every source fixture id is kept (`source_fixture_ids`). |
| Collisions | One key appearing in two different fixtures on the same date means two clubs were normalised together. The key is excluded (`AMBIGUOUS_COLLISION`). |
| Mathematics | Exactly `live_updater._update_rolling` (new = round(0.85·old + 0.15·goals, 3), start 1.2/1.2). `form_last5` = W3/D1/L0 over the last 5 / 15. **Parity test:** on clean single-competition data the rebuild equals `on_match_finished` to the last digit. |
| Point in time | Each state row stores its fixture id and `available_from` = naive kickoff (or end of matchday) + 3 h, read as UTC. Naive times are local (+03:30), so this is never earlier than the true end of the match. A decision at T reads the latest row with `available_from ≤ T`. |
| Synthetic counts | None. `matches_played` counts observed fixtures. A team with none gets no state and fails closed. |

**Why the legacy history cannot be repaired in place:**
- `team_state` is keyed by a country-less label.
- It is written through `get_team_state`, whose LIKE fallback can update another club's row.
- Its form uses results from every competition.
- It is a "now" snapshot, so replaying an earlier prediction from it leaks later results.

## 2. Result

| | Value |
|---|---|
| Finished fixtures used | 647 (14 competitions; Conference League 126, Championship 64, Champions League 61, Europa League 55, Eredivisie 44, Premier League 41, …) |
| Duplicate feed rows merged | 0 (each match was stored once) |
| Team keys | 492: all `RESOLVED`, 0 collisions |
| State rows (one per team per fixture) | 1,294 |
| Determinism | rebuilding twice gives identical rows (test) |

## 3. Before and after — all 492 tracked teams

| Legacy source today (`get_team_stats`) | Teams |
|---|---|
| Hash-generated synthetic statistics | 315 |
| `team_state` under the profile key | 109 |
| Static table, exact name (claims 20 matches) | 31 |
| `get_team_state` LIKE substring fallback | 17 |
| Static table substring (another team) | 20 |

| Matches behind the statistics (legacy vs rh1) | Teams |
|---|---|
| Equal | 49 |
| Legacy claims more (static "20", contaminated rows, other competitions) | 95 |
| Legacy claims fewer (hash "0", wrong label) | 348 |

The full per-team table (legacy source, matches, statistics; rh1 status, matches, statistics, as-of fixture, raw names) is in `team_history_audit.json → teams`.

## 4. Named cases

| Team (competition) | Legacy today | Reconstructed (rh1) |
|---|---|---|
| **Arsenal** (Premier League) | `team_state` "Arsenal / Premier League": 9 matches, home scored 0.90 / conceded 1.88. Its source fixtures are 7 × league 116 (another country's Arsenal) and 1 × league 39. | 1 match (league 39 only, `matches_db:1557409`), scored 1.02 / conceded 1.47 |
| **Inter** (Serie A) | LIKE fallback → **Internacional** (Brazil): 11 matches | 4 Serie A (135) fixtures, scored 1.786 / conceded 1.273 |
| **Chelsea U21** | LIKE fallback → **Chelsea** (senior): 6 matches | No history. Plays in no tracked competition (leagues 46, 702), so it fails closed. |
| **Rangers** (Scottish Premiership) | static-table substring → **Angers**: "20 matches" | 4 Premiership (179) fixtures |
| **Gent** (Belgian Pro League) | static-table substring → **Argentina**: "20 matches", scored 2.0 / conceded 0.5 | 4 Pro League (144) fixtures |

## 5. Temporal integrity

| Check | Test |
|---|---|
| A result is used only from kickoff + 3 h | `test_point_in_time_excludes_later_and_same_window_results` |
| Unknown kickoff counts only from the end of the matchday + 3 h | `test_unknown_kickoff_counts_from_the_end_of_the_matchday` |
| A fixture after the decision never counts | candidate golden (`Everton`'s 2026-10-04 match) |
| Before any observed fixture → no forecast | `NO_HISTORY_BEFORE_DECISION` |
| Postponed / unfinished fixtures ignored | `test_unfinished_or_postponed_fixtures_are_ignored` |

## 6. Limits

- The history covers only stored fixtures: 647 since the store began, on 25 of the last 61 days. Most teams have 1–7 matches, so data quality stays below 60 for many fixtures. Backfilling past fixtures needs provider requests (approval).
- Only tracked competitions are rebuilt. Cup and European matches count only inside their own competition (no cross-competition mixing, by design).
