# M3.5 — Controlled forward shadow deployment preparation: report (Mac host)

Date: 2026-10-19. Branch `m3.5/forward-shadow-prep`, created from `7d0deb0`, tag `backup/m3.4.1/complete`. Not merged, not pushed.

> **Update 2026-10-22:** see `docs/M3_5_MAC_READINESS_REPORT.md`.
> - The caffeinate lifecycle was hardened, an emergency stop during a cycle was added, and the quota, monitor and template validation was completed.
> - The run is now pinned to **manifest v4 `m35-f206ea91f8facda2`** (commit `0708a80`). v3 is kept unchanged but no longer matches the code.
> - The demo's 5-request cap is now used up (5 / 5).
> - Values below that say v3, `f624d0d` or "2 / 5" are the 2026-10-19 state.

**Host: this MacBook Pro, as you chose.** The Linux/VPS artefacts are kept as optional future alternatives only:
- `docs/M3_5_HOST_RUNBOOK.md`;
- `deploy/shadow/systemd/`;
- `deploy/shadow/Dockerfile`;
- `docs/M3_4_1_VPS_CHECKLIST.md`.

**The 14-day shadow has not started.** Nothing below happened:
- no launchd job was installed or loaded;
- no persistent caffeinate was started;
- no API request was sent and no quota was spent;
- production was not restarted, and its DB was not modified.

## 1. Provenance reconciliation (versioned, nothing rewritten)

| Manifest | id | Code | Status |
|---|---|---|---|
| v1 `shadow_manifest.json` (sha256 `c72b2571…`) | — | `47094d1` | kept unchanged. Its rh1 hash included the AUTOINCREMENT `id`, so it could not be reproduced. |
| v2 `shadow_manifest.v2.json` (`129051ee…`) | `m35-59658cae50a480cc` | `ddbf39e` | kept unchanged; superseded by v3 because Mac support changed runtime files |
| v3 `shadow_manifest.v3.json` (`9cd6cabe…`) | `m35-f00a477c1cad226d` | `f624d0d` | kept unchanged; superseded by v4 (caffeinate fix, mid-cycle stop) |
| **v4 `shadow_manifest.v4.json`** (`fcc91b86…`) | **`m35-f206ea91f8facda2`** | **`0708a80`** | **pinned for the run** (2026-10-22) |

v3 pins the following. It was built from a clean working tree, through a read-only in-memory copy, so the DB stays `0f892593…`.

| Item | Pinned value |
|---|---|
| Git | `f624d0ded60b7853b0231132868a1a7bb3270abf`, working tree clean |
| Code fingerprint | `dbab5d26c19b91bf…`: the content of the 224 runtime files (src/, api/, models/, the shadow and Mac scripts, the lock file). It holds in a git worktree and without `.git`; later doc-only commits do not change it. |
| Python | CPython 3.11.17 (3.11 enforced) |
| Team identity | `team-identity-v1`, registry `team-registry-v1`, sha256 `0c8bed1a…` |
| rh1 | `rh1`: **647** historical fixtures, 492 teams, 0 collisions, 1,294 state rows. Content hash `67e5072c…` (without `id`). Rebuilt in memory, it reproduces the stored table. |
| Goldens | 6 reference files hashed (v3-m2.5 3.11 and 3.13, identity-v1-m3.2, identity-v1-rh1-m3.3) |
| Odds | `odds_quotes` with the K53 `revision` column (`d6725d17…`). Revision self-check passed: a changed price under the same stamp becomes revision 1, a duplicate is ignored, and each decision sees only what had been collected by its own time. |
| Model, calibration, selection | `model-714d281b0043`; calibration `unfrozen` (code `cal-f4f5719a1a4e`); `sel-1263b6f5c072`. Unchanged since M3.4.1. |
| Profile, quota | Medium (rh1, 14 competitions, 120-min decision lead, 30-min slots, no closing capture). 70 per UTC day for all consumers, 20 per cycle, 2 attempts per window. Ledger required, provider remaining floor 20, failures counted. |
| DB | integrity ok; 30,101 matches; 184 verified prediction rows; 0 odds; **0 observations, 0 cycles, 0 picks**. 120,896 quarantined rows are all out of their tables with intact copies. |

The cycle refuses to start (exit 2, before any request) on any mismatch:
- code fingerprint, Python, identity or registry hash, rh1 version;
- profile, quota, odds schema, or model, selection or calibration version;
- a missing identity registry, or a history source that has shrunk below the pinned 647 fixtures;
- a missing or unwritable ledger, or a missing manifest;
- `PROBABILITY_INPUTS` or `BASELINE_FREEZE` being set.

## 2. Completed Linux validation

Three runs. All were in a `python:3.11-slim` linux/amd64 container (Docker Desktop on this Mac), with a read-only repository mount, no keys, and migrations tested on a copy of the pre-M3 backup.

| Run | Code | Result |
|---|---|---|
| M3.4.1 run 3 (accepted in your review) | `13cb86c` | PASSED |
| M3.5 run 1 | `ddbf39e` + manifest v2 | **FAILED** (script exit 1). Full suite: 1 failed, 874 passed. The shadow set failed 1 test, and the preflight stage exited 1. |
| **M3.5 final** | `f624d0d` + manifest v3 | **PASSED** (script exit 0, `LINUX VALIDATION PASSED`) |

**M3.5 run 1 failures, as recorded:**
1. `test_preflight_passes_offline_checks_and_never_counts_not_run_as_pass`: the test's ledger was under `/tmp`, which the preflight correctly rejects as volatile storage. The test premise was wrong; the code was right.
2. In the in-container preflight, `2_credentials` failed. The container exports `API_FOOTBALL_KEY=` (empty), and the preflight did not let an env file fill an empty variable. That was a real defect, now fixed. That stage's on-host tests also failed on item 1.

**M3.5 final run:**

| Stage | Result |
|---|---|
| Full suite | exit 0: 888 passed, 22 skipped |
| Goldens | exit 0: 16 passed |
| Shadow, identity, odds, quota, manifest and Mac-recovery set | exit 0: 259 passed, 3 skipped |
| Migrations on DB copy | exit 0: 4 passed |
| Medium dry run (53 fixtures, budget 70, 20 used elsewhere) | exit 0: ledger day total 70, integrity ok |
| Integrity audit | exit 0: PASSED |
| Host preflight on the real prepared DB and v3 (`--run-tests --offline`) | exit 0: 12 of 12 offline checks pass (on-host tests 268 passed, 3 skipped); `3_provider_quota` not run by design |

The 22 skips are:
- 18 repo-hygiene tests (no git in the image);
- 1 chmod test that cannot hold as root;
- 1 migration test in the full suite (it runs separately, 4/4);
- 2 macOS-only tests (caffeinate, plist).

Logs are in `~/Desktop/football_predictor_backups/`:
- `linux_validation_m3_5/` (run 1, the failure);
- `linux_validation_m3_5_final/` and `linux_m3_5_final_console.log` (final run).

Linux is now the **secondary** platform: the run itself is on this Mac, where the same checks pass (§3).

## 3. Completed macOS preparation and offline recovery tests

**Mac inspected** (details in `docs/M3_5_MAC_RUNBOOK.md` §0):
- MacBook Pro Mac14,9, M2 Pro, 16 GB, macOS 26.3.1 (a), Python 3.11.17;
- clock Europe/Istanbul (UTC+3, no DST);
- `pmset`: `sleep 1` on AC and battery (idle sleep after 1 min), standby and hibernate 3, Power Nap on;
- **on battery (47%)** at inspection;
- FileVault on, macOS auto-install on;
- active assertions from powerd, WhatsApp, sharingd, Electron, and an unrelated recurring `caffeinate -i -t 300`. None was touched.

**Prepared, all disabled and not installed:**
- `deploy/shadow/macos/com.footballpredictor.shadow.plist`: a LaunchAgent with `Disabled=true`, `StartCalendarInterval` at :00 and :30, `RunAtLoad=false` and `KeepAlive=false`. It runs `scripts/orchestration/shadow/mac_cycle.sh`. That wrapper:
  - does nothing if `SHADOW_STOP` exists, or if there is no network route (no request is attempted);
  - reads the 0600 env files;
  - runs the Medium cycle with `--manifest shadow_manifest.v3.json`, the shared ledger, `--budget 70` and `--max-requests-per-cycle 20`.
- `deploy/shadow/macos/com.footballpredictor.shadow.caffeinate.plist`: the experiment-specific `caffeinate -i -s -t 1296000` (15 days at most), through `mac_caffeinate.sh`, which writes a pidfile and execs caffeinate.
  - There is no `pmset` change and nothing permanent: the assertion ends on stop, after 15 days, or on logout or reboot.
  - `launchctl bootout` of this label, or `mac_caffeinate.sh stop` (which kills only its own recorded pid, after an identity check), leaves other apps' assertions alone.
- `scripts/shadow_monitor.py`: read-only. It reports:
  - cycle staleness, cycles against the expected 48 per day, and missed slots;
  - unfinished (killed) cycles;
  - network failures (connection, HTTP status, 429) and refusals;
  - the ledger by consumer, and the provider's remaining count today;
  - `quick_check`, the forward-evidence rule, the stop file, the caffeinate and the power source.

  `--notify` shows a macOS notification on any alert.
- `docs/M3_5_MAC_RUNBOOK.md`: exact preparation, preflight, activation, monitoring, emergency-stop and rollback commands. **None was executed.**
- The old M3 plist (`scripts/orchestration/shadow/com.footballpredictor.shadow.plist`, which had no ledger or manifest) is marked SUPERSEDED.

**Offline recovery tests** (`tests/test_shadow_mac_recovery.py`, 13 tests, all pass). They run the real cycle on 30-minute slots against a fake provider:

| Scenario | Verified |
|---|---|
| 2-hour sleep | gap 120 min, **3 missed slots** recorded. With the 120-minute lead, no fixture is lost entirely. A fixture whose window opened during sleep is observed **at wake**, with its true `decided_at` and a 30-min lead (not 120). |
| 8-hour sleep | gap 480 min, **15 missed slots**. A fixture whose whole window fell inside the sleep is **never observed** and never back-filled after wake. The others behave as in the 2-hour case. |
| Wake after kickoff | 0 observations at wake +5 min and +3 h |
| Lost connectivity | each failed send is logged as `error` (no HTTP status) and counted in the ledger. Attempts stop at 2 per fixture window, and every cycle still finishes. When the network returns, the fixture is observed at the deferral deadline with **no prices**, recorded as missing, never invented. |
| HTTP 429 | the cycle stops after the first 429, which is counted. The next slot collects and observes both fixtures. |
| Quota exhaustion | with the ledger at 70, nothing reaches the provider and the ledger stays at 70. Refusals are recorded, and the fixture is observed honestly without prices. |
| Duplicate launch | a start inside the 10-min lease of a running cycle is skipped with 0 requests. A second process while the lock is held prints `skipped` and exits 0. |
| Abrupt termination | a `SIGKILL` mid-cycle (after discovery, during odds) leaves the DB integrity `ok`, the discovery committed and the cycle marked unfinished. A start 5 min later is skipped (lease). The next slot recovers and observes with its own timestamp. |
| Monitoring | after a 2.5-hour gap with offline failures, the monitor reports the 4 missed slots, the connection failures (alert), the ledger use and integrity ok. Later, the stale alert fires. |
| Caffeinate (real, under 1 s, Mac only) | the assertion appears under our pid; `status` and `stop` act only on it; an unrelated caffeinate keeps running; a stale pidfile kills nothing. |
| launchd templates | `plutil -lint` OK, `Disabled=true`, `KeepAlive=false`, slots :00/:30, caffeinate bounded to 15 days, **not present** in `~/Library/LaunchAgents` |
| Wrapper | `SHADOW_STOP` exits 0 without starting Python or creating a ledger |

Two **defects were found and fixed** during this work (`f624d0d`):
1. **Quota deadlock across UTC days.** The provider's "remaining" value was used from any earlier day. A day ending at ≤ 20 remaining would have refused every request the next day, and so would never have been refreshed. It now applies only on the UTC day it was reported. Tested in both directions.
2. **Preflight env files.** A variable that was set but empty, such as `API_FOOTBALL_KEY=` exported by a shell or container, hid the env-file value. The preflight also now treats macOS temp directories (`/private/var/folders`, `/private/tmp`) as volatile storage for the ledger.

**Mac preflight on the real prepared DB and v3** (offline, dummy key, scratch ledger, no request): **exit 0**.
- 12 of 12 offline checks pass, including the on-host tests (271 passed);
- `3_provider_quota` is `not_run` by design;
- report: `~/Desktop/football_predictor_backups/m3_5_mac_preflight_offline/preflight.json`.

The DB file is unchanged. The preflight's lock check left an empty `shadow_cycle.lock` in the prepared directory, and the read-only reads created empty `-wal` and `-shm` files.

**Demo kept separate and capped:**
- pid 2670 runs on the isolated copy (port 8001, `--cap 5`), with its own usage DB and no `--ledger`;
- usage is **2 / 5**. A second request, `/fixtures?date=2026-10-19`, was sent at 2026-10-18 19:44 UTC through the demo's own manual refresh; no M3.5 command calls it;
- it was not restarted, reset or raised.

It uses the same key, so its requests count only on the provider side. The worst case per day is 70 (shadow) + 3 (demo, lifetime) + 1 (preflight) = 74 ≤ 100. The shadow also stops when the provider reports ≤ 20 remaining.

**Tests on the final code (Mac):**
- Python 3.11: 909 passed, 1 skipped;
- Python 3.13: 908 passed, 2 skipped;
- goldens: 16 passed;
- integrity audit: PASSED.

## 4. Remaining risks from Mac sleep and connectivity

| Risk | Effect on the run | Mitigation | Residual |
|---|---|---|---|
| Lid closed, or clamshell without external power and display | system sleeps regardless of caffeinate | keep the lid open, on AC; the monitor shows gaps | **high if the Mac moves**; each sleep loses slots |
| On battery | `-s` inactive; `-i` holds but drains; low battery forces sleep | AC power for 14 days; the monitor alerts on battery | medium |
| Sleep longer than about 2 h | whole decision windows lost: never observed, recorded as missed slots. Shorter sleeps only shorten lead times. | caffeinate; the monitor's stale alert | lowers coverage (A), not integrity |
| macOS auto-update restart, or a crash | FileVault: nothing runs until login. Caffeinate ends. | you may defer updates for 14 days (a setting change, your decision); log in after any restart; the monitor goes stale | medium |
| Logout | both LaunchAgents stop | stay logged in | low |
| Wi-Fi loss, captive portal, VPN, DNS | failed sends counted (at most 2 per fixture window), so prices may be missing. With no route at all, the wrapper skips without spending quota. Discovery that fails waits 12 h, so fixtures can be missed. | monitor network alerts; check Wi-Fi | medium; reduces coverage |
| Provider 429 or outage | cycle stops; next slot retries within the attempt cap | — | low |
| launchd catch-up on wake | one coalesced run; it records the gap and never back-fills | tested | none for integrity |
| The same key used elsewhere | the provider-side total rises outside the shadow ledger | the demo is capped (3 left); the provider floor of 20 stops the shadow | low if the key is not used elsewhere |
| Clock change or timezone | slots stay 30 min apart; decisions are stored in UTC | NTP on | low |
| Disk or DB damage | detected by `quick_check` and integrity | daily `.backup` | low |

## 5. GO / NO-GO conditions for activating the experiment

**All** of these must be true at activation:
1. Your written authorization to activate. ⛔
2. The Mac is on **AC power**, with the lid open (or proper clamshell), and you are logged in. You decide whether to defer macOS auto-updates for 14 days.
3. The layout from runbook §2 exists outside Desktop: a worktree at `0708a80` (manifest v4), a 3.11 venv from the lock file, the DB sha256 `0f892593…`, and the manifests copied read-only.
4. You have typed the key into `~/FootballShadow/etc/api_key.env` (mode 0600). ⛔
5. The preflight exits **0** with `--run-tests --check-provider`: every check passes, including `3_provider_quota` (1 counted `/status`). ⛔
6. The experiment caffeinate is loaded first, and its assertion is visible under its pid.
7. The first supervised cycle (`kickstart`) finishes without refusal, and the ledger shows only `shadow_cycle`.
8. No other consumer of the key is planned. The demo has 0 requests left (5 / 5 used).

**GO** for the 14-day Medium forward shadow on this Mac when 1–8 hold. The technical preparation for that is complete.

**NO-GO** for:
- starting now (conditions 1, 2, 4, 5 and 6 are not met; the Mac is on battery);
- the official 1,000-pick baseline (calibration unfrozen, odds timestamps unresolved);
- a production restart or hold release.

## 6. Changes in M3.5

| Commit | Content |
|---|---|
| `ddbf39e` | manifest v2 machinery; the cycle's `--manifest` and `SHADOW_STOP`; observation protocol fields (manifest id, competition and provider ids, canonical team ids, input availability); `shadow_manifest.py`; `shadow_preflight.py`; the optional systemd and Docker templates |
| `f624d0d` | Mac launchd and caffeinate templates, the wrapper, the monitor, the recovery tests; the quota day-scope fix; the preflight fixes |
| docs | this report; `M3_5_MAC_RUNBOOK.md`; `M3_5_FORWARD_PROTOCOL.md` (frozen at v3); `M3_5_HOST_RUNBOOK.md` marked optional; Linux script and optional templates pointed at v3 |

No model, weights, calibration, selection or policy file changed, and no golden changed.

## 7. Production state

- `data/engine.db`: mtime 2026-10-16 15:35, WAL 15:43. The read-only `.dump` hash is `c530e65b4dc213fa`, which equals the verified backup. Unchanged.
- Service hold `m3-hold-20261016-401a4675` is present.
- The production app was not restarted.
- Demo pid 2670 is running at **2 / 5** on 2026-10-19; **5 / 5** (cap used up, by its own daily refresh) on 2026-10-22. Not touched.
- No football LaunchAgent is in `~/Library/LaunchAgents` or loaded. There is no crontab, and no shadow or odds process.
- `~/FootballShadow` has not been created.
- No experiment caffeinate is running. The only caffeinate seen is the unrelated recurring `caffeinate -i -t 300`.
- No API-Football request was made in M3.5.
- Nothing was merged or pushed. No bet was placed, and no baseline was started.

**Stopped. Waiting for your review and authorization.**
