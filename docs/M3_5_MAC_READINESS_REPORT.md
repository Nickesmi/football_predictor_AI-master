# M3.5 — Mac readiness report (14-day Medium forward shadow on this MacBook Pro)

Date: 2026-10-22. Branch `m3.5/forward-shadow-prep`. Not merged, not pushed. This report completes the Mac part of M3.5. It builds on `docs/M3_5_REPORT.md` (§1–§7 there stay valid unless corrected here).

**Nothing was activated:**
- no launchd job was installed, loaded or enabled;
- no persistent caffeinate was started (tests used caffeinate for 2–40 s, and each one ended);
- no API-Football request was sent and no quota was spent;
- production was not restarted, and its DB was not modified;
- the official 1,000-pick baseline was not started.

## 0. Summary

| Area | Result |
|---|---|
| Caffeinate lifecycle | **Corrected.** The status condition was not inverted, but ownership was weak (see §1). Hardened and tested. |
| Recovery tests | 18 in `tests/test_shadow_mac_recovery.py`, **all pass**. Includes the new emergency stop during a cycle (stop file and SIGTERM). |
| Quota | 7 new tests in `tests/test_shadow_quota.py`, **all pass**. UTC midnight reset, no cross-day deadlock, caps, failures counted, persistence across restarts, demo cap. |
| Mac configuration | launchd templates valid, disabled, not installed; correct DB, manifest, venv Python and ledger; monitor read-only with zero network requests |
| Full validation | 3.11: 921 passed, 1 skipped. 3.13: 920 passed, 2 skipped. Goldens 16/16 on both. Migrations 4/4. Audit PASSED. |
| Manifest | **v4 `m35-f206ea91f8facda2`** pins commit `0708a80`. v3 is kept unchanged and now fails closed. |
| Production | unchanged; hold active; no shadow collection running |

**The "seven previously reported failures".** They come from intermediate runs during development, before commit `f624d0d`. They were caused by test design (wrong scenario arithmetic, ledger counts that ignored the 2 discovery requests, a quota range that was too narrow, monitor binding tuples). All were fixed then, without relaxing what the tests require, and the recovery set passed at `f624d0d`. Every test was rerun on the current code (§5). Two new failures came up in this round and were fixed the same way:
- a wrong missed-slot count I had written into a new test;
- the caffeinate `stop` treating an unreaped zombie as still running.

The expected behaviour was not weakened in either case.

## 1. Caffeinate lifecycle (`scripts/orchestration/shadow/mac_caffeinate.sh`)

**Finding.** `status` was correct (`if ours; then running; else not running; exit 1`), so there was no inverted condition. But there were three real weaknesses:
1. Ownership was only "the comm ends in `caffeinate`". A stale pidfile whose pid had been **reused by another caffeinate** would have passed. That includes the unrelated recurring `caffeinate -i -t 300` on this Mac, and `stop` would then have killed it.
2. A second `run` overwrote the pidfile and started a second caffeinate. The first became orphaned and could no longer be stopped by pidfile.
3. The timeout was not validated.

**Fix (commit `cabd961`):**
- The pidfile holds three lines: pid, process start time (`ps -o lstart`), and the exact command line `/usr/bin/caffeinate -i -s -t <secs>`. A process is "ours" only if **all three** match.
  - `exec` keeps the pid and start time, so launchd still owns the process.
- `run` while ours is running prints `already running <pid>` and starts nothing.
- The timeout must be a whole number from 1 to 1,296,000 s (15 days). Anything else is refused with exit 2, and no pidfile is written.
- `stop` sends SIGTERM only to the verified pid. It waits up to 5 s, treats a zombie as stopped, and then removes the pidfile. A repeated `stop` prints `not running (nothing stopped)`.
- The monitor now uses the same `status` test (`scripts/shadow_monitor.py`).

**Verified** (tests `test_experiment_caffeinate_lifecycle_stops_only_its_own_process` and `test_experiment_caffeinate_timeout_is_enforced_and_bounded`, real caffeinate, macOS only):

| Requirement | Result |
|---|---|
| Start reports the correct pid | pidfile pid equals the launched process; `pmset -g assertions` shows `pid <pid>(caffeinate)` |
| Status distinguishes running and stopped | `running <pid>`, exit 0 / `not running`, exit 1 (also for a missing pidfile, or a leftover one after the timeout) |
| Stop terminates only ours | `stopped <pid>`; an unrelated `caffeinate -i -t 40` keeps running |
| Stale or reused pid | a pidfile naming the unrelated caffeinate, either as a bare pid or with the right command line but a different start time: `nothing stopped`, and the process survives |
| Repeated start/stop | a second `run` starts nothing and the pid is unchanged; a second `stop` is a no-op |
| Timeout enforced | `-t 2` ends by itself with exit 0; 0, 1296001, `abc` and `-5` are refused (exit 2) |
| No persistent sleep prevention | tests use 2–40 s timeouts and kill in `finally`. After every run: `pgrep -fl "caffeinate -i -s"` shows nothing. Installing the templates is not part of any test. |

## 2. Recovery tests (`tests/test_shadow_mac_recovery.py`, 18 tests)

Each test runs the real `run_cycle` on 30-minute slots against a fake API-Football; nothing leaves the Mac. The same rule is asserted throughout:

> A missed decision window is recorded as missed slots and no observation, and it is **never reconstructed** later. Every observation has a cycle row, the pinned manifest, `decided_at < kickoff` and zero baseline counting.

| Scenario | Verified |
|---|---|
| 2-hour sleep | gap 120 min, 3 missed slots. A fixture whose window opened during the sleep is observed at wake, with its true `decided_at` and a 30-min lead instead of 120. No fixture lost. |
| 8-hour sleep | gap 480 min, 15 missed slots. The fixture whose window lay entirely inside the sleep is never observed. |
| Wake after the decision window expired | wake at kickoff +5 min and +3 h: 0 observations |
| Connectivity loss | errors logged without an HTTP status, each counted in the ledger; at most 2 attempts per window; every cycle finishes. After recovery, the fixture is observed at the deferral deadline **without** prices. |
| HTTP 429 | the cycle stops after the first 429, which is counted; the next slot observes both fixtures |
| Quota exhaustion | with the ledger at 70, nothing reaches the provider and the ledger stays at 70; the fixture is observed honestly without prices |
| Duplicate scheduler launch | inside the 10-min lease: skipped, 0 requests. Second process while the lock is held: `skipped`, exit 0. |
| Abrupt termination and restart | SIGKILL mid-odds: integrity ok, discovery kept, cycle left unfinished; a start 5 min later is skipped (lease); the next slot recovers with its own timestamp |
| **Emergency stop during a cycle (new)** | `SHADOW_STOP` created after the first odds request: no further request, **no decision**, and the cycle is closed `stopped`. Resumed after kickoff: 0 observations, gap recorded as 3 missed slots, never back-filled. |
| **SIGTERM during a cycle (new)** | real `cycle.stop_signal` in a subprocess. SIGTERM arrives while a request is in flight; the cycle ends before the next request. Exit 0, status `stopped`, integrity ok. |
| Monitor (3 tests) | §4 |
| Templates and wrapper | §4 |

**The emergency-stop code is new runtime code** (commit `f1180b0`):
- `run_cycle(…, stop_requested=)` is checked before discovery, before each odds request and before decisions.
- `cycle.stop_signal(stop_file)` returns true once `SHADOW_STOP` exists or SIGTERM has arrived.
- `scripts/shadow_cycle.py` uses it.

Before this, a stop file only took effect at the next cycle, and `launchctl kill SIGTERM` ended the process abruptly, as in the SIGKILL case. That was safe but not clean.

## 3. Quota behaviour (`tests/test_shadow_quota.py`, 7 tests)

| Requirement | Test and result |
|---|---|
| UTC midnight reset | 70 reserved at 23:59 UTC; the 71st at 23:59:59 is refused. 02:59 Istanbul time is still the same UTC day. At 00:00:00 UTC the next day is allowed with 0 used. Yesterday's 70 stay recorded. |
| Yesterday's low remaining does not deadlock today | provider reported 3 remaining at 23:30 UTC; the 00:30 UTC cycle discovers and requests odds |
| Today's low remaining still stops (not weakened) | 3 remaining reported 5 min earlier today: nothing sent, error recorded |
| Per-cycle cap | 30 fixtures due in one window: exactly 20 odds requests, then `request cap 20 reached`; ledger total ≤ 70 |
| Daily cap | ledger at 70 sends nothing (§2, quota exhaustion) and the ledger never exceeds 70 |
| Failed requests count | HTTP 500: 2 attempts, both in `provider_quota_log` and the ledger; connectivity errors and 429s likewise (§2) |
| Restart does not reset usage | separate processes: 40 then 40 (30 allowed, total 70), then `shadow_settle` 5 (0 allowed). Ledger rows: 70 reserved, 15 refused. |
| Demo keeps its 5-request lifetime cap | two processes, 3 + 3 attempts: `3 allowed`, then `2 allowed, 1 refused`, total 5. The demo never writes the shadow ledger. |

**Live demo state (read-only check, 2026-10-22):**
- pid 2670, `--cap 5`.
- No `API_FOOTBALL_*` or `ODDS_*` variables in its environment, so it is not connected to any shadow ledger.
- Its usage DB has **5 allowed requests, 0 refused: the lifetime cap is used up.** These were one `/fixtures?date=…` per day: 2026-10-17 20:36, 10-18 19:44, 10-19 18:49, 10-20 20:30 and 10-21 20:30 UTC. All came from the demo's own daily refresh; no M3.5 command calls it.
- It cannot send any further request.

This corrects the earlier "2 / 5". The worst-case daily provider-side total for the shadow period is now 70 (shadow) + 1 (preflight) = 71.

## 4. Mac configuration

**launchd templates** (`deploy/shadow/macos/`; test `test_launchd_templates_are_valid_disabled_and_not_installed`):

| Check | Result |
|---|---|
| Syntax | `plutil -lint` OK for both |
| Disabled | `Disabled=true`, `KeepAlive=false`; cycle `RunAtLoad=false`; neither present in `~/Library/LaunchAgents`; `launchctl list` shows no football job |
| Schedule | `StartCalendarInterval` Minute 0 and 30 |
| Cycle job | `/bin/bash __ROOT__/code/scripts/orchestration/shadow/mac_cycle.sh`, `FOOTBALL_SHADOW_ROOT=__ROOT__`, `ExitTimeOut` 60 s (SIGTERM, then SIGKILL) |
| Wrapper references | `$ROOT/venv/bin/python` (isolated 3.11 venv); `--db $RUN/engine_shadow.db` (isolated DB); `--ledger $RUN/api_football_ledger.db` (shared ledger); `--manifest $RUN/shadow_manifest.v4.json`; `--profile medium --max-requests-per-cycle 20 --budget 70`; consumer `shadow_cycle`. Never `data/engine.db` or Desktop. |
| `shadow.env.example` | ledger `__ROOT__/run/api_football_ledger.db`, `API_FOOTBALL_REQUIRE_LEDGER=1`, budget 70, `ODDS_MIN_REMAINING=20`, DB path `__ROOT__/run/engine_shadow.db`, no key |
| Caffeinate job | `mac_caffeinate.sh run __ROOT__/run/caffeinate.pid 1296000` |

**Monitor** (`scripts/shadow_monitor.py`; tests `…thresholds_fire_at_their_documented_limits` and `…is_read_only_and_makes_no_network_request`):

| Alert | Threshold, tested at the limit |
|---|---|
| stale | last non-skipped cycle > 75 min: none at 75, alert at 76 |
| provider remaining | today's value ≤ 20: none at 21, alert at 20; earlier days are ignored (same query as the guard) |
| network/provider failures | ≥ 6 in 24 h: none at 5, alert at 6 |
| HTTP 429 | any in 24 h |
| ledger day total | > 70: none at exactly 70, alert at 71 |
| unexpected consumer | anything other than `shadow_cycle`, `shadow_settle` or `shadow_preflight` (tested with `demo`) |
| unfinished cycle | `running` with no finish, started > 30 min ago |
| integrity | `PRAGMA quick_check` not ok; forward-evidence rule violated |
| host (macOS) | experiment caffeinate not running (same ownership test as `stop`); on battery; both suppressed while `SHADOW_STOP` is present |

The monitor's SQL reads `shadow_cycles`, `provider_quota_log`, the ledger table and `PRAGMA quick_check`, all through `mode=ro` connections.

- **Read-only:** sha256 and mtime of every file in `run/` are identical before and after a monitor run, and no `-wal` file is created.
- **Zero external API requests:** `socket.connect` and `getaddrinfo` raise during the run, and the monitor completes. Its only subprocesses are `mac_caffeinate.sh status` (runs `ps`), `pmset -g batt` and, with `--notify`, `osascript display notification`. None of them uses the network.

## 5. Final validation (this Mac, code `0708a80`)

| Suite | Command | Passed | Failed | Skipped |
|---|---|---|---|---|
| Full, Python 3.11.17 | `.venv311/bin/python -m pytest tests -q -p no:cacheprovider -rs` | 921 | 0 | 1: migration test without `M0_EXISTING_DB_COPY` (run separately below) |
| Full, Python 3.13 | `.venv/bin/python -m pytest tests -q -p no:cacheprovider -rs` | 920 | 0 | 2: the same migration test; `pyflakes` not installed in the 3.13 venv |
| Goldens 3.11 / 3.13 | `pytest tests/golden` | 16 / 16 | 0 | 0 |
| Migrations | `M0_EXISTING_DB_COPY=<copy of engine_pre_m3.db> pytest tests/test_schema_migrations.py` | 4 | 0 | 0 |
| Integrity audit | `scripts/test_integrity_audit.py` | PASSED, exit 0 | — | (counts 8 static skip markers in the suite, unchanged) |
| Mac-affected set 3.11 / 3.13 | recovery, quota, manifest, shadow validation, api_guard, demo_quota | 108 / 108 | 0 | 0 |
| Offline host preflight, real prepared DB, v4 | `shadow_preflight.py --run-tests --offline` (dummy key, ledger in `m3_5_v4_mac_preflight_offline/`) | exit 0; 12/12 offline checks pass; on-host tests 283 passed | 0 | `3_provider_quota` not run (it needs 1 counted request) |

Not rerun: the Linux container validation. It last passed at `f624d0d` (`docs/M3_5_REPORT.md` §2). Since then the code changes are the Mac script, the cycle's stop hook and tests, and the Linux path is optional.

**Manifest v4.**
- File `shadow_manifest.v4.json`, sha256 `fcc91b86d7d7ea89…`, id `m35-f206ea91f8facda2`, built from a clean tree at `0708a80`.
- Code fingerprint `215dbba63b5b8f43…` (224 files). It supersedes v3 (`9cd6cabe…`).
- Everything else is pinned as in v3: Python 3.11, identity-v1, rh1 647/492/0, model, calibration and selection versions, Medium profile, 70/20.
- The `supersedes.reason` text inside v4 is the generic wording the script writes (it mentions v1). The real reason is that the runtime files changed (caffeinate, stop hook).
- DB `0f892593…` and v1–v3 are unchanged.
- The current code matches v4, and v3 now fails closed (fingerprint mismatch).
- The cycle refuses anything but `0708a80`'s runtime content.

## 6. Power and sleep-risk assessment

**Ground truth on this Mac:**
- `pmset` sleeps the system after **1 minute** idle, on AC and battery, with standby, hibernatemode 3 and Power Nap.
- FileVault and automatic macOS installs are on.
- It was on battery at the 2026-10-19 inspection.
- `pmset` is not changed by this plan.

| Risk | Likelihood over 14 days | Effect | Mitigation | Residual |
|---|---|---|---|---|
| Idle sleep | certain without an assertion | cycles stop | experiment caffeinate `-i -s` (prevents idle and, on AC, system sleep) | low on AC with the lid open |
| Lid closed or Mac moved | depends on use | sleeps regardless of caffeinate | keep it in place, lid open, or clamshell with external display and power | **high if the Mac is carried** |
| Battery | medium if unplugged | `-s` inactive; drains; forced sleep at low battery | AC for 14 days; monitor alerts on battery | medium |
| macOS update restart, crash, power cut | medium (auto-install on) | FileVault: nothing runs until login; caffeinate gone | your choice to defer updates for 14 days; log in after any restart; re-bootstrap the caffeinate job (runbook §5) | medium |
| Logout | low | both LaunchAgents stop | stay logged in | low |
| Wi-Fi loss, captive portal, VPN, DNS | medium | without a route, the wrapper skips (0 requests); with a broken route, failures are counted (≤ 2 per fixture window) and prices may be missing; a failed discovery waits up to 12 h | monitor network alert; check Wi-Fi | medium; lowers coverage only |
| Thermal or CPU | low | Background ProcessType; a cycle is short | — | low |

## 7. Expected observation losses during downtime

The decision window for a fixture opens **135 min** before kickoff (120-min lead + 15-min margin) and closes at kickoff. Any cycle in that window observes the fixture once. A cycle late in the window still observes it, with a shorter lead, and records the true lead.

Take a downtime from the last completed cycle at `s` to the first cycle after wake at `w`, with `D = w − s`:

- **Lost entirely** (never observed, never reconstructed): fixtures with kickoff in `(s + 135 min, w]`. The kickoff span lost is **max(0, D − 135 min)**.
- **Observed with a shortened lead**: fixtures with kickoff in `(max(w, s + 135 min), w + 135 min)` whose window opened during the downtime. They are observed at `w` with lead `kickoff − w`.
- **Missed slots recorded**: `D // 30 − 1` (coalesced launchd run on wake).

| Downtime D | Kickoff span lost | Shortened-lead span | Missed slots |
|---|---|---|---|
| 30 min (normal) | 0 | 0 | 0 |
| 1 h | 0 | up to 1 h of kickoffs | 1 |
| 2 h | 0 (tested) | up to 2 h | 3 (tested) |
| 2 h 30 | 15 min | 2 h 15 | 4 (tested) |
| 4 h | 1 h 45 | 2 h 15 | 7 |
| 8 h | 5 h 45 (tested: fully inside → lost) | 2 h 15 | 15 (tested) |
| 12 h + discovery missed | ≥ 9 h 45; fixtures not yet discovered may also be missed until the next discovery | — | 23 |

Also:
- **Emergency stop** loses every window that closes before the stop file is removed. The cycle running at that moment takes no decision.
- **No network but powered:** the wrapper skips without requests. The loss is the same as for a downtime of the same length.
- **Partial network:** fixtures can be observed without prices (recorded as missing).

Losses reduce **coverage** (operational reliability, protocol metric A). They never create a late or reconstructed observation, so the prediction-quality and profitability metrics are computed only over genuinely forward observations.

## 8. Monitoring and emergency stop

The exact commands are in `docs/M3_5_MAC_RUNBOOK.md` §6–§7.

- **Monitor** 2–3 times a day: `shadow_monitor.py --run ~/FootballShadow/run --manifest …/shadow_manifest.v4.json --notify`. Exit 0 means no alert; exit 1 lists the alerts. It is read-only and sends nothing.
- **Emergency stop, in order:**
  1. `touch ~/FootballShadow/run/SHADOW_STOP`. This is enough on its own. A running cycle stops before its next request (tested), and later cycles exit with 0 requests.
  2. `launchctl kill SIGTERM gui/$(id -u)/com.footballpredictor.shadow`. This ends a cycle in progress cleanly (status `stopped`, tested).
  3. `launchctl bootout` the cycle label.
  4. `launchctl bootout` the caffeinate label, or `mac_caffeinate.sh stop`. Only the experiment's caffeinate ends.

## 9. GO / NO-GO

GO conditions are as in `docs/M3_5_REPORT.md` §5, with these corrections:
- condition 3: worktree at **`0708a80`**, manifest **v4**;
- condition 8: the demo has **0** requests left, so no other use of the key is acceptable.

NO-GO now: there is no written authorization, AC power and lid-open are not confirmed, no key has been entered, and neither the provider preflight nor the caffeinate has run. The official 1,000-pick baseline, a production restart and a hold release remain NO-GO.

## 10. Production state (checked 2026-10-22)

- `data/engine.db`: mtime 2026-10-16 15:35, WAL 15:43 (unchanged). The read-only `.dump` sha256 starts `c530e65b4dc213fa`, which equals the backup. The `-shm` index mtime of 2026-10-19 comes from earlier read-only checks.
- Service hold `m3-hold-20261016-401a4675` is present.
- The production app was not restarted.
- There is no football LaunchAgent (file or loaded), no crontab, no `~/FootballShadow`, no shadow cycle process and no experiment caffeinate.
- No API-Football request was made by M3.5.

**Stopped for review.**
