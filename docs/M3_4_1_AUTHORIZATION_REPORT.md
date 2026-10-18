# M3.4.1 — Final deployment prerequisites: authorization report

Date: 2026-10-18. Branch `m3.4.1/deployment-prereqs`, created from `89bc75b` (`backup/m3.4/complete`).
Not merged and not pushed. The 14-day shadow has **not** started. Production is held and unchanged (§7).

Commits:
- `231caa1`: Task 2, the shared ledger as the single global quota, failing closed.
- `47094d1`: Task 3, shadow DB preparation script. The manifest pins this commit.
- `411f502`: Task 4, the VPS checklist, the prep-script tests, and the quota-guard tests added to the Linux list.
- `13cb86c`: the root-proof ledger test and a validation script that fails on any non-zero stage (§1).
- this report (docs only).

Since `47094d1`, only tests, docs and the validation script's test list have changed. No `src/` or runtime script changed.

## 1. Linux test results

Docker Desktop got a controlled restart, solely to restore the engine. A normal `docker desktop restart` timed out on a stale `docker-sandbox` process. I quit Docker, ended that one process and reopened Docker, and the engine came up in about 10 s. No football process was restarted.

`scripts/orchestration/linux_validation.sh` was run with:
- a `python:3.11-slim` container on `linux/amd64` (emulated on Apple silicon);
- the repo mounted read-only, with no API keys;
- `pip install -r requirements-lock.txt`;
- migrations tested on a copy of the pre-M3 backup.

The production DB was never mounted for writing.

Three runs:

| Stage | Run 1 (code at `89bc75b`) | Run 2 (`411f502`) | **Run 3 (final code, `13cb86c`)** |
|---|---|---|---|
| Python | 3.11.17 | 3.11.17 | **3.11.17** |
| Full suite | exit 0: 841 passed, 19 skipped | **exit 1: 1 failed**, 852 passed, 19 skipped | **exit 0: 853 passed, 20 skipped** |
| Goldens | exit 0: 16 passed | not run (aborted) | **exit 0: 16 passed** |
| Shadow, identity, odds, K53, demo quota, isolation; in run 3 also `test_api_guard`, `test_prepare_shadow_db` | exit 0: 212 passed | not run | **exit 0: 224 passed, 1 skipped** |
| Migrations on DB copy | exit 0: 4 passed | not run | **exit 0: 4 passed** |
| Dry run: Medium, 53 fixtures, budget 70, 20 used by others | exit 0 | not run | **exit 0**: ledger day total **70**; integrity ok; 0 after-kickoff, 0 baseline-counted, 0 picks |
| Integrity audit | exit 0 | not run | **exit 0: PASSED** |

**Run 2 failure, reported as it happened.**
- `test_shadow_deployment_with_unwritable_ledger_fails_closed` failed (`assert (0 == 2)`).
- Cause: the container runs as **root**, and root ignores `chmod 0500`. So the ledger *was* writable, and the cycle correctly ran instead of failing closed. The guard was right; the test premise does not hold under root.
- `set -e` also aborted the remaining stages without a summary. The wrapper's `| tail` hid the non-zero exit.

Fix (`13cb86c`):
- New test: a ledger path whose parent is a file. It is unusable for every user and fails closed, verified as root on Linux.
- The chmod variant now skips under root. It still runs and passes as a normal user on macOS. The VPS runs as the non-root `shadow` user.
- The script now runs every stage, records each exit code, and exits 1 unless all pass. Run 3 printed `LINUX VALIDATION PASSED` with script exit 0, with no pipe in between.

Linux skips (20, from `-rs`):
- 18 `test_repo_hygiene` ("needs a git checkout"; the slim image has no git);
- 1 root chmod variant (above);
- 1 `test_schema_migrations` in the full suite (needs `M0_EXISTING_DB_COPY`; run separately, 4/4 pass).

None is in the shadow, identity, odds or quota sets.

Logs, all in `~/Desktop/football_predictor_backups/`:
- `linux_validation_m3_4_1/` (run 1);
- `linux_validation_m3_4_1_final/` (run 2, the failure);
- `linux_validation_m3_4_1_run3/` and `linux_run3_console.log` (run 3).

Local runs on the final code:

| Check | Result |
|---|---|
| Python 3.11 (`.venv311`) full suite | 872 passed, 1 skipped |
| Python 3.13 (`.venv`) full suite | 871 passed, 2 skipped |
| Goldens (3.11) | 16 passed |
| Migrations on a copy of the pre-M3 backup | 4 passed |
| Integrity audit | PASSED |
| Frontend `npm run lint` and `npm run build` | pass |

**Verdict: Linux validation PASSED** on the final code (run 3), in a linux/amd64 container. It has not yet run on the actual VPS (blocker B3).

## 2. Quota consumer audit (Task 2)

Rule:
- one persistent ledger (`API_FOOTBALL_LEDGER`, `src/odds/ledger.py`) is the single global API-Football quota on the shadow host;
- each request is reserved **before** sending, and failures count;
- `API_FOOTBALL_REQUIRE_LEDGER=1` makes every consumer **fail closed** when the ledger is missing or unwritable;
- `src/ops/api_guard.py` patches `http.client` and DNS process-wide, so a code path that bypasses `quota.fetch_json` is still counted or blocked.

| Consumer | HTTP path | Shadow-host status |
|---|---|---|
| Shadow discovery `/fixtures?date=`, odds ingestion `/odds` (`scripts/shadow_cycle.py`) | `quota.fetch_json` | **ledger-counted**, 20 per cycle, 2 attempts per fixture window |
| Settlement (`scripts/shadow_settle.py`) | `quota.fetch_json` | **ledger-counted** (`--ledger`; refuses without it) |
| Timestamp experiment, `validate_odds_provider`, `odds_worker` | `quota.fetch_json` | ledger-counted if run; **not run** during the shadow |
| Localhost demo (`scripts/demo_server.py`, pid 2670) | own 5-request lifetime cap (`demo_quota`) | runs on the Mac, not the shadow host. It uses the same provider key, so its sends count against the provider's 100. Usage **1 / 5**, unchanged (§7). |
| App fixtures service and system refresh loop (`api/main.py`) | `urllib` | counted by `api_guard` under REQUIRE=1. The app refuses to start without a ledger. **Not run** on the shadow host; production is held. |
| Results statistics, debug status, old `src/data/api_client` | `requests`/`urllib3` | **blocked** by the DNS guard (no uncounted traffic), so these consumers are **disabled** |
| Repo-root scratch scripts (`check_quota.py`, `test_apisports_*.py`, `audit_pre_implementation.py`, `streamlit_app.py`, `main.py`) | direct | **must not be run** on the shadow host (checklist §2: never copied or run) |

Tests (`tests/test_api_guard.py`, 11, plus earlier ledger and operations tests), all pass on macOS and Linux:
- no ledger: exit 2, "fail closed";
- unusable ledger path: exit 2, including as root; unwritable ledger dir: exit 2 as a non-root user;
- REQUIRE flag refuses even at budget 0;
- `fetch_json` refuses without a ledger, or with an unwritable one;
- retries via raw `http.client`: 5 attempts at budget 3 gives 3 counted and 2 refused;
- **after a restart**: 0 allowed and the ledger stays at 3;
- the `requests` library with 4 retries is blocked and 0 are counted;
- uncounted DNS is blocked;
- `fetch_json` plus the guard counts once, not twice;
- the API app does not start without a ledger when the flag is required;
- HTTP errors count (`test_failed_requests_count_against_the_shared_budget`), and a 429 stops the cycle.

Ceilings: **70 per UTC day across all consumers, 20 per cycle.**

Provider allowance:
- the free plan allows 100 per day;
- `ODDS_MIN_REMAINING=20` also refuses once the provider's own `x-ratelimit-requests-remaining` header falls to 20;
- so the shadow cannot exceed what the provider actually has left, even if the key is also used elsewhere.

## 3. Shadow DB and configuration manifest (Task 3)

Prepared by `scripts/prepare_shadow_db.py` (it refuses the production DB, a non-empty output or a dirty tree):

- Source: the remediated candidate `remediation_candidate_v1/engine_m3_candidate.db`, sha256 `036b2b6986a8f026…`. It was opened read-only and copied with the online backup API. The **live DB was not used**.
- Output: `~/Desktop/football_predictor_backups/m3_4_1_shadow/`, containing `engine_shadow.db`, `shadow_manifest.json` and `workspace/`.
- DB sha256 at preparation: `0f892593a0d4e97b…`, re-checked unchanged.

| Pinned item | Value |
|---|---|
| Git commit | `47094d155f2c517b19e457fb46f678e4f7c5c12d` |
| Identity resolver | `team-identity-v1` |
| Registry | `team-registry-v1`, sha256 `0c8bed1a0bc66a88…`, 31 entries reviewed: 18 maps approved under the delegated criteria with owner confirmation still pending, 13 exclusions. rh1 does not need the 18 maps. |
| rh1 history | `rh1`: 647 fixtures, 492 teams, 0 collisions, 1,294 state rows, 14 competitions, table sha256 `b8d890f6b24a216f…`. It is rebuilt every cycle. |
| Prediction | shadow inputs `identity-v1-rh1`, model `model-714d281b0043`, selection `sel-1263b6f5c072`, calibration `unfrozen`. Production default stays `legacy`, with `PROBABILITY_INPUTS` unset. |
| Profile | Medium: all 14 competitions, decision 120 min before kickoff, discovery every 12 h, refresh 180 min, 20 per cycle, 2 attempts per window, no closing capture, 30-min slots, lease 10 min |
| Policies | `baseline-policy-v1-proposal.2670eba922e2` and `v1.1-proposal.785bb9063e49`, research only |
| Odds schema | `odds_quotes` with the K53 `revision` column, CREATE sha256 `d6725d17d453a102…` |
| DB state | `integrity_check` ok. 30,101 matches, 184 prediction errors, 0 odds quotes, **0 shadow observations**, 0 shadow fixtures, 0 picks, 120,896 quarantined evidence rows. |

The forward-evidence rule is recorded in the manifest:
- only `shadow_observations` written by a cycle with `decided_at` before kickoff count as forward evidence;
- the rebuilt rh1 history, the 184 pre-existing verified rows and any replay are history, **never forward evidence**;
- the DB starts with zero observations;
- genuine decision timestamps are enforced by tests: no observation at or after kickoff and no back-filling after downtime.

## 4. Deployment checklist (Task 4)

See `docs/M3_4_1_VPS_CHECKLIST.md`. It covers:
- resources: 1 vCPU, 1–2 GB RAM, 10 GB, Ubuntu 24.04 or Debian 12, UTC and NTP;
- environment variables, with the key entered on the host only;
- the firewall: SSH in from the owner IP; out to `v3.football.api-sports.io:443` and NTP only;
- a non-root `shadow` user with systemd hardening;
- the `/srv/shadow` volume, where the ledger is never reset;
- oneshot and timer templates (`Persistent=false`, `Restart=no`);
- restart behaviour, monitoring and stop criteria, rollback;
- the pre-start verification on the host.

Nothing was purchased, installed, scheduled or collected.

## 5. Remaining blockers

| # | Item | Blocks the shadow? |
|---|---|---|
| B1 | **Host choice and provisioning**: buy or choose a VPS, or use this Mac with `caffeinate` and accept recorded gaps | **yes**, owner action |
| B2 | API key entered on the host by the owner (no credential transfer by me) | **yes**, owner action |
| B3 | Linux validation re-run **on the actual host** (checklist §11), after the container pass here | yes, minutes at deploy time |
| B4 | Approval to install and enable the timers | **yes**, owner action |
| B5 | Provider allowance shared with the demo and any other key use: keep the daily total including other uses ≤ 100; the demo stays capped at 5 lifetime | no, but the owner must not use the key elsewhere at volume |
| B6 | Odds timestamps ambiguous (provider stamps 2.7–4.9 h old): CLV and price-freshness claims are unsupported | no (shadow measures it); blocks profitability claims |
| B7 | Owner confirmation of the 18 registry maps | no (rh1 does not use them) |
| B8 | Calibration unfrozen; E0–E5 and calibration evidence definitions | no; blocks the official baseline |
| B9 | Shared `.cache` validity review | no; needed only before a production restart |

## 6. GO / NO-GO

- **Technical readiness for the 14-day Medium shadow: GO.** Linux passed, the quota is enforced globally and fails closed, and the shadow DB and manifest are pinned and verified. This is subject to the owner actions B1, B2 and B4 and the on-host check B3.
- **Starting the shadow now: NO-GO until you approve.** There is no host, key, timer or approval, and nothing has been started.
- **Official 1,000-pick baseline: NO-GO** (calibration unfrozen, odds timestamps unresolved, no forward evidence yet).
- **Production restart or hold release: NO-GO** (not requested; requires the cache review).

## 7. Production state at report time

- `data/engine.db`: mtime 2026-10-16 15:35, WAL 15:43. The `.dump` read through `?mode=ro` hashes to `c530e65b4dc213fa` (sqlite3 CLI), which **equals the verified backup by the same method**. Python `iterdump` gives `46d2aeda97e7da31` for both. Unchanged. The `engine.db-shm` mtime is 2026-10-18. That file is SQLite's shared-memory WAL index, which read-only readers rewrite (these verification dumps and the Docker read-only mount). `engine.db` and `engine.db-wal` were not modified.

  > Note: the `4fc62ae03b391eab` in the M3.4 report came from a different dump method. Prod and backup still match by every method used today.

- Read-only handles on the DB came only from the Docker VM, through the read-only mount used by the Linux validation.
- Service hold `m3-hold-20261016-401a4675` is present.
- Demo pid 2670 is still running on the isolated copy at port 8001 with cap 5. Usage is **1 / 5**, unchanged (`/fixtures?date=2026-10-18`, 2026-10-17 20:36 UTC). It was not restarted or reset.
- No shadow cycle or odds worker is running, there is no crontab and no launchd football job.
  > Note: pid 65948 (uvicorn :8000) belongs to another project (`~/cryptopredic…`). It was not touched.
- No API-Football request was made in M3.4.1.
- Nothing was merged or pushed. No timer, bet or baseline was started.

**Stopped. Waiting for approval.**
