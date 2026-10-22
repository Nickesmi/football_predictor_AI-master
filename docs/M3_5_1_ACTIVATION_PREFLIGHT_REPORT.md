# M3.5.1 — Supervised Mac activation preflight: report and activation checklist

Date: 2026-10-22, 17:20–17:50 +03 (14:20–14:50 UTC). Branch `m3.5/forward-shadow-prep`. Not merged, not pushed.

**The experiment has not started:**
- no API-Football request was sent and no quota was spent; the shadow ledger has 0 rows;
- no launchd job was installed, loaded or enabled;
- no persistent caffeinate was started;
- no cycle ran: the runtime DB is byte-identical to the pinned DB;
- production is stopped and held, and its DB is unchanged;
- no bet was placed, and no baseline counting started.

## 1. Prepared runtime: `~/FootballShadow`

| Path | Content | Verified |
|---|---|---|
| `~/FootballShadow/` | mode 0700, outside Desktop, Documents and iCloud | — |
| `code/` | detached git worktree at **`7b8a29c`** (see the note below), clean | runtime code fingerprint **`215dbba63b5b8f4359631893c5adf14b22802d994613a14d5fdea47c55549629`** (224 files) = manifest v4 |
| `venv/` | CPython **3.11.17** (`/opt/homebrew/bin/python3.11`), `pip install -r requirements-lock.txt` | `pip freeze` is identical to the lock file; `pip check` reports no broken requirements |
| `run/engine_shadow.db` | copy of the verified isolated shadow DB, mode 0600 (source WAL is empty) | sha256 **`0f892593a0d4e97bffda161dc401915917d5eb47c3b808fe27e6eb25533b7959`**, equal to the source and to the manifest |
| `run/shadow_manifest.v4.json` | **`m35-f206ea91f8facda2`**, mode 0444 (v1–v3 alongside, unchanged) | sha256 **`fcc91b86d7d7ea89a03185d0fec5f1902ce86554c4442bda8ba4f513d2aaca96`** |
| `run/api_football_ledger.db` | the persistent shared ledger, created empty by the preflight's writability check | **0 rows** |
| `etc/shadow.env` (0600) | ledger `~/FootballShadow/run/api_football_ledger.db`, `API_FOOTBALL_REQUIRE_LEDGER=1`, budget 70, `ODDS_MIN_REMAINING=20`, `ODDS_MAX_AGE_MINUTES=240`, DB and cache paths in `run/`, `TZ=UTC` | no secrets |
| `etc/api_key.env` (0600) | `API_FOOTBALL_KEY=` (**empty**; you type the key) | — |
| `launchd/` | both LaunchAgent plists rendered with the real paths. **Staged only, not in `~/Library/LaunchAgents`, `Disabled=true`.** | `plutil -lint` OK |

The cycle is fixed by the wrapper `code/scripts/orchestration/shadow/mac_cycle.sh`:
- Medium profile;
- `--max-requests-per-cycle 20 --budget 70`;
- `--manifest run/shadow_manifest.v4.json`.

The cycle refuses any other value (§4).

**Note on the commit (needs your acknowledgement).** You specified runtime commit `0708a80`.
- The worktree is at `7b8a29c`, two commits later. The differences are documentation and two test files only: `git diff --stat 0708a80 7b8a29c` lists 5 docs and `tests/test_odds_layer.py`, `tests/test_decision_chain.py`.
- **The runtime code is byte-identical to `0708a80`**: same fingerprint, so manifest v4 matches.
- Reason: the preflight at `0708a80` failed its on-host test gate. `shadow.env` exports `ODDS_MAX_AGE_MINUTES=240`, and two tests had silently assumed the 60-minute default. Under 240, a 120-minute (or 3-hour) quote is legitimately fresh. **The code was right; the tests were not isolated.**
- Fixed in `7b8a29c`. The tests now set their window. The odds-layer test also checks that, under 240, a quote older than 240 minutes is not used. Nothing was weakened.
- The full suite passes with the shadow environment loaded: 921 passed, 1 skipped (migration test).
- The cycle and preflight enforce the fingerprint, not the commit hash, so the run is pinned exactly as approved.

The production DB was never opened by any of this: the runtime has its own DB path, and every shadow script refuses `data/engine.db`.

## 2. Mac operating conditions (measured 2026-10-22 17:24–17:26 +03)

| Condition | Measured | Assessment |
|---|---|---|
| Power | **AC Power**, battery 75%, charging | OK |
| Lid | `AppleClamshellState = No` (open); built-in display online, no external display | OK; keep it open and keep the Mac in place |
| Disk | 253 GiB free of 926 GiB (72% used). The run needs < 1 GiB (DB 167 MB + daily backups). | OK |
| Network | Wi-Fi `en0` active; **Cloudflare WARP connected** ("Network: healthy"); default route via `en0`. 18 HTTPS probes to neutral hosts (pypi.org, apple.com, 1.1.1.1; never API-Football): all 200, 0.08–0.69 s. 0% loss to 1.1.1.1 (avg 53 ms). The gateway drops ICMP (100% loss), which is normal, not a fault. | stable now. WARP is a dependency: if it disconnects or reconnects, the route can drop briefly (the wrapper then skips without spending quota). |
| Sleep settings | `pmset` AC: `sleep 1` (idle sleep after 1 min), `standby 1`, `hibernatemode 3`, `powernap 1`, `womp 1` | without the experiment caffeinate, the Mac sleeps within minutes. Not changed. |
| Scheduled power events | 2 Apple maintenance wakes (calaccessd, osanalytics) | harmless |
| **Automatic updates** | `AutomaticallyInstallMacOSUpdates = 1`; **macOS Tahoe 26.7.1 is pending ("Recommended: YES, Action: restart")**, and macOS 27.0.1 is offered. Uptime 64 days. | **Threatens the run.** An automatic install restarts the Mac. With FileVault on, nothing runs until you log in, and the caffeinate is gone. See GO condition 3. |
| Other sleep assertions | an unrelated, recurring `caffeinate -i -t 300` (its pid changes every 5 min: 29872 at 17:25, 46807 at 17:45), plus other apps | **not stopped or modified** |
| Experiment caffeinate | prepared (`launchd/com.footballpredictor.shadow.caffeinate.plist`, `caffeinate -i -s -t 1296000` through `mac_caffeinate.sh`); `mac_caffeinate.sh status run/caffeinate.pid` = `not running` | **not started** |

## 3. API-Football consumers (audit)

| Consumer | State | Can it spend the shadow's quota? |
|---|---|---|
| **Localhost demo**, pid 2670, port 8001, `--cap 5` | running since 2026-10-17; usage DB: **5 allowed, 0 refused**; last fetch 2026-10-21 20:30 UTC (for 2026-10-22); no `API_FOOTBALL_*`/`ODDS_*` variables, so no link to the shadow ledger | **No.** Each request is counted *before* sending, and the 6th is refused in-process, so nothing reaches the provider. DNS for `*.api-sports.io` outside a counted request is blocked. Its background tasks are disabled. |
| Why it fetched daily | the vite dev server (pid 46214, port 5173) proxies the local UI to 8001. An open UI page asks for the next day's fixtures, and the demo fetches each new date once. So the five "automatic refreshes" were one fetch per new date, triggered by the UI, until the cap ran out. | From 2026-10-22 on, every new date is refused locally (an `allowed = 0` row; nothing sent). |
| Production football app | stopped since 2026-10-16 under hold `m3-hold-20261016-401a4675`; no process; no LaunchAgent | no |
| Port 8000 uvicorn, pid 65948 | **a different project** (`~/cryptopredic`, its cwd); no API-Football variables; no API-Football reference in its source | no |
| Docker | `footballpredictor-backend-1` exited 7 months ago (`restart=no`); `aiclinicagent-*` running, with no football variables; the old Linux-validation container exited | no |
| **Packaged app `~/Desktop/Football Predictor AI.app`** | not running, not a login item; **it has its own API key in `~/.football_predictor/settings.json`, mode 0644 (readable by other local accounts)** | **Yes, if launched**: a full app with background refresh, no cap, no shadow ledger. Do not open it during the run. |
| Second checkout `~/Documents/gymagent/football_predictor_ai` | not running; no `.env` | only if run with a key; do not |
| Repo `.env` (`football_predictor_ai-master/.env`) | contains a non-empty key | anything run manually from the main repo with that `.env` bypasses the shadow ledger; do not run it |
| launchd, cron | no football job, no crontab | no |
| Live connections to `api-sports` | none at audit time | — |

**Recommendation on the demo: stop it before activation, after you accept this impact.** I have not stopped it.
- *Quota impact:* none either way. It cannot send, and stopping it does not reset its allowance: the usage DB stays at 5/5.
- *Effect on the local interface:* the UI at `http://127.0.0.1:5173` (through vite to 8001) loses its backend. Pages show load errors or no data until the demo is started again, and it would still have 0 requests. There is no other effect: production stays stopped, and nothing else uses port 8001.
- *Why stop anyway:*
  - it can no longer fetch new dates, so it only shows data up to 2026-10-22;
  - it keeps the key loaded in a long-running process;
  - it removes a second consumer from the audit trail during the experiment.
- *Command (yours to run, or authorize):* `kill -TERM 2670`. Optionally stop the vite server too with `kill -TERM 46214`.

Also recommended (your decision; these are not my files):
- Run `chmod 600 ~/.football_predictor/settings.json`, or remove the key from that app.
- If the key that the packaged app and repo `.env` hold is the one you will use for the shadow, use the shadow key nowhere else for 14 days.

## 4. Offline preflight against the actual prepared runtime

Run from `~/FootballShadow/run` with the runtime's own venv, code, DB, manifest and env files. Both runs were `--run-tests --offline`, so there was no provider request.

| Run | Result |
|---|---|
| A: `etc/shadow.env` + `etc/api_key.env` (key empty) | exit 1; only `2_credentials` FAIL (`key_present: false`), as expected until you type the key. All other offline checks pass. |
| **B: as A + a dummy key from a temporary file outside the runtime** (deleted afterwards; the env files were checked to contain no dummy) | **exit 0: all 12 offline checks pass**; `3_provider_quota` `not_run` (it needs the one counted request) |

| Requirement | Evidence (run B, plus direct checks on the runtime) |
|---|---|
| Manifest v4 matches the pinned code | check 7: no problems; fingerprint `215dbba6…` = manifest; Python 3.11 |
| Corrected rh1 inputs selected | check 7: `history_version rh1`, profile history `rh1`, `shadow_inputs identity-v1-rh1`. Check 7b: rh1 rebuilt in memory from 647 fixtures, 0 collisions, **reproduces the stored table** |
| Identity verification | check 7: identity-v1 and registry hash match; on-host identity and registry tests pass |
| Quarantined evidence cannot enter the prediction path | check 8: 120,896 quarantined rows, 0 still in their source tables, 0 copy hash mismatches, integrity ok |
| Odds revisions intact | runtime DB: `odds_quotes.revision` column and both indexes present, 0 rows. `revision_selfcheck()`: a changed price under the same stamp becomes revision 1, and the decision before the second collection still sees 2.0 (after: 1.8). |
| Shared ledger fails closed | through the real `shadow_cycle.py` on a scratch copy, all **exit 2 before any request**: no ledger (`API_FOOTBALL_LEDGER is not set`); unwritable ledger; no `--manifest`; `--budget 71` (`outside 1..70`); `--max-requests-per-cycle 21` (`profile differs`); v3 manifest (`code fingerprint 215dbba63b5b8f43 != manifest dbab5d26c19b91bf`). Afterwards: 0 ledger rows, 0 quota-log rows, 0 cycles. |
| Emergency stop | on the real runtime with `run/SHADOW_STOP`: the launchd wrapper prints `{"stopped": "SHADOW_STOP present"}`, exit 0, without starting Python. The cycle script prints `{"stopped": …}`, exit 0. Check 11: stop gives 0 requests and the stale alarm fires. The stop file was removed afterwards. |
| Monitor | on the runtime: exit 1 with exactly the expected alerts (`stale: last cycle None`, `experiment caffeinate is not running`); power `AC`; ledger 0/70; read-only |
| launchd jobs disabled | nothing in `~/Library/LaunchAgents`; `launchctl list` shows no football job; `launchctl print gui/501/com.footballpredictor.shadow[.caffeinate]` = not loaded; no override entry. Staged plists `Disabled=true`. |
| No forward observations or official picks | checks 5 and 6: 0 counted, 0 official picks, 0 observations, 0 cycles. Check 8a: the DB is byte-identical to the pinned DB. |
| On-host tests | check 1: 282 passed, 1 skipped (the service-hold test, which needs a hold file in the checkout) |

After all of the above, the runtime DB sha256 is still `0f892593…` and the ledger has 0 rows.

Repo tests after the test fix:
- 3.11: 921 passed, 1 skipped;
- 3.13: 920 passed, 2 skipped (migration test; `pyflakes` absent);
- full suite with the shadow environment loaded: 921 passed, 1 skipped.

## 5. Supervised activation checklist

Run the steps in order. Every command is for you to run in Terminal, logged in, on AC power, with the lid open.

**Steps 2–6 need your explicit authorization before they run.** Steps 2–6 change state or spend quota. Steps 1, 7 and 8 do not need authorization in themselves, but step 1 is useful only before step 2.

```
R=~/FootballShadow; U=gui/$(id -u); A=~/Library/LaunchAgents
```

### Step 1. Secure the API key (you only)
```
nano $R/etc/api_key.env                      # set API_FOOTBALL_KEY=<key>; save; nothing else in the file
chmod 600 $R/etc/api_key.env; stat -f "%Sp %N" $R/etc/api_key.env      # must be -rw-------
grep -c '^API_FOOTBALL_KEY=.\+' $R/etc/api_key.env                      # must be 1 (does not print the key)
```
Never paste the key into chat, the repository, `shadow.env` or a command line, where it would land in shell history.

### Step 2. ⛔ The single authorized provider-status check (1 counted request)
```
cd $R/run && $R/venv/bin/python $R/code/scripts/shadow_preflight.py \
  --manifest $R/run/shadow_manifest.v4.json --db $R/run/engine_shadow.db \
  --env-file $R/etc/shadow.env --env-file $R/etc/api_key.env \
  --run-tests --check-provider --out $R/run/reports/preflight_final.json
echo "preflight exit $?"                     # must be 0; every check pass, including 3_provider_quota
sqlite3 $R/run/api_football_ledger.db "SELECT day, consumer, endpoint, cost, status FROM ledger"   # exactly 1 row: shadow_preflight /status
```
Run it once. If it fails, stop and report; do not retry in a loop.

### Step 3. ⛔ Start the experiment-specific caffeinate
```
pmset -g batt | head -1                      # must say 'AC Power'
cp $R/launchd/com.footballpredictor.shadow.caffeinate.plist $A/
plutil -remove Disabled $A/com.footballpredictor.shadow.caffeinate.plist && plutil -lint $A/com.footballpredictor.shadow.caffeinate.plist
launchctl bootstrap $U $A/com.footballpredictor.shadow.caffeinate.plist
bash $R/code/scripts/orchestration/shadow/mac_caffeinate.sh status $R/run/caffeinate.pid      # running <pid>
pmset -g assertions | grep "pid $(head -1 $R/run/caffeinate.pid)(caffeinate)"              # PreventUserIdleSystemSleep / PreventSystemSleep
```

### Step 4. ⛔ One supervised shadow cycle (manual; no schedule yet)
Run it away from :00 and :30. The cycle job is not installed yet, so nothing else starts.
```
cd $R/run && FOOTBALL_SHADOW_ROOT=$R /bin/bash $R/code/scripts/orchestration/shadow/mac_cycle.sh \
  >> $R/run/logs/cycles.jsonl 2>> $R/run/logs/cycles.err
echo "cycle exit $?"                         # must be 0
tail -n 5 $R/run/logs/cycles.err; tail -c 2000 $R/run/logs/cycles.jsonl
```
An exit of 2 means it refused (fail closed) and sent nothing; read `cycles.err` and stop.

### Step 5. ⛔ Review the first observation and the quota ledger
```
sqlite3 -header $R/run/engine_shadow.db "SELECT cycle_id, started_at, finished_at, status, discovery_ran, missed_slots FROM shadow_cycles"
sqlite3 -header $R/run/engine_shadow.db "SELECT fixture_id, manifest_id, decided_at, kickoff, minutes_to_kickoff, quote_status, counts_toward_baseline FROM shadow_observations ORDER BY decided_at"
sqlite3 -header $R/run/api_football_ledger.db "SELECT day, consumer, COUNT(*) n, SUM(cost) cost FROM ledger GROUP BY 1, 2"
sqlite3 -header $R/run/engine_shadow.db "SELECT endpoint, status, http_status, requests_remaining, at FROM provider_quota_log ORDER BY at"
$R/venv/bin/python $R/code/scripts/shadow_monitor.py --run $R/run --manifest $R/run/shadow_manifest.v4.json
```
Accept only if all of these hold:
- the cycle is `finished`;
- the ledger shows only `shadow_preflight` (1) and `shadow_cycle`, with the cycle's sends ≤ 20 and the total ≤ 70;
- every observation, if any, has `manifest_id = m35-f206ea91f8facda2`, `decided_at < kickoff` and `counts_toward_baseline = 0`. The M3.5 protocol columns, such as `manifest_id`, are added by the first cycle, so this query works only after step 4. The first cycle may legitimately produce 0 observations, because only fixtures within 135 min of kickoff are decided;
- `provider_remaining` is well above 20;
- the monitor's only alert, if any, is about staleness after more than 75 min.

### Step 6. ⛔ Enable the 30-minute launchd schedule
```
cp $R/launchd/com.footballpredictor.shadow.plist $A/
plutil -remove Disabled $A/com.footballpredictor.shadow.plist && plutil -lint $A/com.footballpredictor.shadow.plist
launchctl bootstrap $U $A/com.footballpredictor.shadow.plist        # no kickstart: the first run is the next :00 or :30
launchctl print $U/com.footballpredictor.shadow | grep -E "state|event"
date -u +%FT%TZ > $R/run/reports/START.txt; echo m35-f206ea91f8facda2 >> $R/run/reports/START.txt
```
Day 1 of the 14 days begins at the first scheduled cycle.

### Step 7. Monitor daily progress (read-only)
2–3 times a day:
```
$R/venv/bin/python $R/code/scripts/shadow_monitor.py --run $R/run --manifest $R/run/shadow_manifest.v4.json --notify; echo "monitor exit $?"
```
Once a day:
```
$R/venv/bin/python $R/code/scripts/shadow_report.py --db $R/run/engine_shadow.db --out $R/run/reports/$(date -u +%F).json --check-stale-minutes 90
sqlite3 $R/run/engine_shadow.db ".backup $R/run/backups/engine_shadow_$(date -u +%F).db"
sqlite3 $R/run/api_football_ledger.db "SELECT consumer, SUM(cost) FROM ledger WHERE day = date('now') GROUP BY 1"
pmset -g log | grep -E " Sleep  | Wake  " | tail -5; pmset -g batt | head -1
```
Alert thresholds and stop criteria: `docs/M3_5_MAC_READINESS_REPORT.md` §4 and `docs/M3_5_FORWARD_PROTOCOL.md` §6. Expected losses per downtime are in readiness report §7.

### Step 8. Emergency stop (any time; the first line is enough)
```
touch $R/run/SHADOW_STOP                                        # a running cycle stops before its next request; later ones send nothing
launchctl kill SIGTERM $U/com.footballpredictor.shadow          # ends a cycle in progress cleanly (status 'stopped')
launchctl bootout $U/com.footballpredictor.shadow               # no more cycles
launchctl bootout $U/com.footballpredictor.shadow.caffeinate    # ends ONLY the experiment's caffeinate
bash $R/code/scripts/orchestration/shadow/mac_caffeinate.sh status $R/run/caffeinate.pid   # not running
```
The gap is recorded as missed slots and never back-filled. Resume only after you have understood the cause: remove `SHADOW_STOP`, then repeat steps 3 and 6. End of run and rollback: `docs/M3_5_MAC_RUNBOOK.md` §8.

## 6. Exact remaining activation requirements

1. **Your written authorization** for steps 2–6.
2. **Your acknowledgement** that the runtime worktree is at `7b8a29c`: the same runtime code as `0708a80` and the same fingerprint, with test-only and doc-only differences (§1).
3. **An automatic-restart decision**, before step 3. Either:
   - install the pending macOS 26.7.1 update first and then re-run step 2's preflight (still 1 request); or
   - turn off *Install macOS updates* under System Settings → General → Software Update → Automatic updates for the 14 days.

   Leaving both as they are is NO-GO.
4. **The demo decision**: stop it (`kill -TERM 2670`, impact in §3) or leave it running at 5/5. Either is quota-safe. I recommend stopping it.
5. **Do not launch** `Football Predictor AI.app` or any repo copy with a key during the run.
6. Step 1 done: the key is in `etc/api_key.env` (0600). Then step 2 exits 0 with `3_provider_quota` pass and exactly 1 ledger row.
7. At steps 3–6: AC power, lid open, logged in, WARP connected or deliberately off. Don't toggle WARP during the run.

## 7. GO / NO-GO recommendation

- **Technical readiness: GO.**
  - The runtime is prepared and verified: code fingerprint, manifest v4 and DB hash match.
  - The offline preflight passes 12/12 on the actual runtime.
  - Fail-closed and the emergency stop are verified on the real scripts.
  - The ledger is empty and there are no competing consumers.
  - The Mac is on AC with the lid open, and the network is stable.
- **Activation now: NO-GO** until requirements 1–3 are met: authorization, acknowledgement of `7b8a29c`, and the macOS-update decision. Requirement 4 is recommended.
- Once 1–3 are met, then key entry, step 2 passing, and step 5 accepted: **GO** for the 14-day Medium forward shadow on this Mac.
- **NO-GO, unchanged:**
  - the official 1,000-pick baseline;
  - a production restart or hold release;
  - any bet.

## 8. State at the end of M3.5.1

- **Production:**
  - `data/engine.db` mtime 2026-10-16 15:35 (67,547,136 bytes), WAL 15:43 (85,963,832 bytes), both unchanged;
  - read-only `.dump` sha256 `c530e65b4dc213fa…`, equal to the backup (the read-only check updates only the `-shm` index);
  - hold `m3-hold-20261016-401a4675` present;
  - no football app process.
- **Shadow:** no LaunchAgent installed or loaded, no shadow cycle has run, no experiment caffeinate, ledger 0 rows, runtime DB `0f892593…`.
- **Demo:** pid 2670 still running at 5/5, not stopped or reset.
- **Repo:** branch `m3.5/forward-shadow-prep`. The worktree list now includes `~/FootballShadow/code` (detached `7b8a29c`).

**Stopped. Waiting for your authorization.**
