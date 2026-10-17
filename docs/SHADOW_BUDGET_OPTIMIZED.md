# Optimised 14-Day Shadow Budget (M3.3 Package E)

Commit `51f9f27`. Code:
- `src/shadow/cycle.PROFILES` (`low`, `medium`, `full`)
- `src/shadow/budget.estimate`
- `scripts/shadow_cycle.py --profile`

**Nothing is activated.** The templates are disabled and use `--profile medium`.

## 1. Levers investigated

| Lever | Finding | Applied |
|---|---|---|
| Competition prioritisation | The six biggest top-flight competitions (Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League) are about 43 % of fixture volume | `low` profile |
| Batch fetching | **Not available on the free plan:** `/odds?league=&season=2026` is refused (K44). `/odds?date=` pages cover every league worldwide (about 10 fixtures per page), which is worse than per-fixture requests for 14 competitions. | per-fixture remains the cheapest |
| Reuse of valid quotes | One mandatory decision fetch per fixture already serves the decision. The provider refreshes every few hours (§ timestamp findings), so a second fetch adds information only near kickoff (closing / CLV). | closing capture optional (`closing_capture`) |
| Scheduling around kickoff | Fetches happen only inside a fixture's pre-decision window (kickoff − 135 min) or closing window (− 45 min). No idle polling. | already in place |
| Duplicate polling | Discovery fetched today + tomorrow every 12 h (4/day). A date discovered within 20 h is now skipped, giving about 2/day plus 1 results date. | `rediscover_after_hours = 20` |
| Caps | Daily `--budget`, per-cycle cap 20, `ODDS_MIN_REMAINING = 20`, 7 s throttle; an observation waits for its fetch | in place |

## 2. Configurations (14 days)

Volumes come from competition formats, because stored data covers only 25 of 61 days: domestic league matches per week, plus UEFA league-phase matchdays in about 2 of 3 weeks. Estimates are ± 20 %.

| | Low | **Medium (recommended)** | Full |
|---|---|---|---|
| Competitions | 6 | 14 | 14 |
| Closing capture (CLV) | no | no | yes |
| Fixtures observed | ~120 (about 45 % of tracked) | ~270 (all tracked) | ~270 |
| Provider requests, 14 days | **~162** | **~312** | **~582** |
| Average / peak day | 12 / 23 | 22 / 46 (≈ 56 at the observed 53-fixture peak) | 42 / 89 |
| Fits a 70/day budget on the peak | yes | yes | **no** (≈ 19 closing fetches lost) |
| Odds freshness at decision | as measured: provider stamp 70–320 min old at fetch time; 240-min coverage about 33–61 % | same | same at decision; closing improves CLV only |
| Expected missed observations | 0 by design among the 6 (others not tracked) | ≈ 0 if the host stays up (deferral handles cap bursts) | closing partial on weekends |
| Remaining free quota per day (100 − peak) | ≥ 77 | ≥ 44 | none on peak days |

## 3. Recommendation

**Medium.** All 14 competitions; one decision fetch per fixture; discovery about twice a day; daily budget 70; per-cycle cap 20.
- It covers every tracked fixture.
- It stays within the free plan with a margin of at least 30 requests on the busiest day.
- It leaves quota for the production app if that is released.
- CLV is not measured. If CLV matters more than coverage, add closing capture on weekdays only (about +100 requests over 14 days).

## 4. Always-on deployment plan (prepared, not installed)

- **Host:** an existing always-on Linux machine with `footballpredictor-shadow.{service,timer}` (`Persistent=false`, every 30 min), or this Mac with launchd plus `caffeinate -s` on AC power.
- **Shadow DB:** a fresh copy of the remediated candidate. rh1 is rebuilt each cycle.
- **Daily monitoring:**
  - `scripts/shadow_report.py --check-stale-minutes 90` (exit 3 means stale);
  - `integrity.ok`;
  - `cycle_health.missed_slots`;
  - the provider's remaining requests.
- **Activation steps** (each needs approval): host → DB path → budget 70 → profile medium → install the template → day-14 report.
