# M3.5 — Mac host runbook: the 14-day Medium shadow on this MacBook Pro

**Host chosen by the owner: this Mac.** Nothing here has been executed. Steps marked ⛔ need explicit owner authorization:
- entering the API key;
- the one counted provider check;
- installing or enabling launchd jobs;
- starting the experiment's caffeinate.

The Linux/VPS material (`docs/M3_4_1_VPS_CHECKLIST.md`, `docs/M3_5_HOST_RUNBOOK.md`, `deploy/shadow/systemd/`, `deploy/shadow/Dockerfile`) is an optional future alternative only.

Pinned:
- manifest **v3 `m35-f00a477c1cad226d`** (`shadow_manifest.v3.json`), superseding v2 `m35-59658cae50a480cc`, which superseded v1;
- code commit `f624d0d`, code fingerprint `dbab5d26…`, 224 runtime files;
- shadow DB sha256 `0f892593a0d4e97bffda161dc401915917d5eb47c3b808fe27e6eb25533b7959`.

## 0. This Mac (inspected 2026-10-19)

| Item | Value | Consequence |
|---|---|---|
| Hardware | MacBook Pro Mac14,9, Apple M2 Pro (8P + 4E), 16 GB, 262 GB free | ample |
| macOS | 26.3.1 (a), build 25D771280a | LaunchAgents, `caffeinate` and `pmset` available |
| Python | `/opt/homebrew/bin/python3.11` = 3.11.17 | matches the manifest (3.11) |
| Clock | Europe/Istanbul, UTC+03:00, no DST | launchd slots at local :00/:30 = UTC :00/:30 |
| Power | on **battery** at inspection (47%) | the run needs AC power the whole time |
| `pmset` (AC and battery) | `sleep 1` (idle system sleep after 1 min), `displaysleep` 10 on AC / 2 on battery, `standby 1`, `hibernatemode 3`, `powernap 1`, `tcpkeepalive 1` | without an assertion the Mac sleeps within minutes of idleness. `pmset` is **not** changed. |
| Current assertions | powerd (display on), WhatsApp camera, sharingd Handoff, Electron, and an unrelated recurring `caffeinate -i -t 300` | other apps; never touched |
| FileVault | on | after a reboot, nothing runs until you log in |
| macOS updates | automatic installation **on** | an overnight update restart stops the run until you log in |

## 1. Layout (outside Desktop, Documents and iCloud)

`~/FootballShadow/` holds four folders:
- `code/`: a detached git worktree at `f624d0d`, unaffected by edits in the main repo;
- `venv/`: Python 3.11 from `requirements-lock.txt`;
- `etc/`: `shadow.env` and `api_key.env`, both mode 0600;
- `run/`: `engine_shadow.db`, the manifests, `api_football_ledger.db`, `caffeinate.pid`, `SHADOW_STOP` (only to stop), and the `logs/`, `reports/`, `backups/` and `workspace/.cache` folders.

The run lives outside `~/Desktop` for two reasons. macOS privacy controls (TCC) can block a launchd job from reading Desktop or Documents. And SQLite must not sit in an iCloud-synced folder.

## 2. Prepare (no network request, no scheduler)

```
R=~/FootballShadow
mkdir -p $R/etc $R/run/logs $R/run/reports $R/run/backups $R/run/workspace/.cache && chmod 700 $R $R/etc
git -C ~/Desktop/football_predictor_ai-master worktree add --detach $R/code f624d0d
/opt/homebrew/bin/python3.11 -m venv $R/venv && $R/venv/bin/pip install -r $R/code/requirements-lock.txt
S=~/Desktop/football_predictor_backups/m3_4_1_shadow
cp $S/engine_shadow.db $S/shadow_manifest.json $S/shadow_manifest.v2.json $S/shadow_manifest.v3.json $R/run/
shasum -a 256 $R/run/engine_shadow.db      # must be 0f892593a0d4e97bffda161dc401915917d5eb47c3b808fe27e6eb25533b7959
chmod 444 $R/run/shadow_manifest*.json
for f in shadow.env api_key.env; do sed "s#__ROOT__#$R#g" $R/code/deploy/shadow/macos/$f.example > $R/etc/$f; done
chmod 600 $R/etc/*.env
```

Never copy:
- `data/engine.db` (production; every shadow script refuses it);
- `.cache/`;
- `.env`;
- the demo directory.

The shadow ledger starts empty and is never reset during the 14 days.

⛔ The owner types the key into `$R/etc/api_key.env` (`API_FOOTBALL_KEY=…`) with an editor. It is never pasted into chat or a repository file.

## 3. Preflight (as you, before anything is installed)

```
R=~/FootballShadow; cd $R/run
$R/venv/bin/python $R/code/scripts/shadow_preflight.py \
  --manifest $R/run/shadow_manifest.v3.json --db $R/run/engine_shadow.db \
  --env-file $R/etc/shadow.env --env-file $R/etc/api_key.env \
  --run-tests --check-provider --out $R/run/reports/preflight.json
echo "preflight exit $?"        # must be 0, every check "pass"
```

⛔ `--check-provider` sends **one** counted `GET /status` (consumer `shadow_preflight`). The checks are listed in `docs/M3_5_REPORT.md` §3.

## 4. Sleep prevention (experiment-specific, temporary)

The mechanism is `caffeinate -i -s -t 1296000`, run as the LaunchAgent `com.footballpredictor.shadow.caffeinate` through `mac_caffeinate.sh`. `mac_caffeinate.sh` records its pid in `run/caffeinate.pid` and then execs caffeinate, so the pid is unchanged.

- `-i` blocks idle system sleep. `-s` blocks system sleep while on AC.
- The display may still sleep; that is fine.
- It is **not** a permanent change: `pmset` is untouched. The assertion ends when the process ends:
  - on stop;
  - after 15 days;
  - on logout or reboot.
- Stopping it never affects other apps. `launchctl bootout` acts only on this label, and `mac_caffeinate.sh stop` kills only the pid in its own pidfile, after checking that the pid is a caffeinate process. This was tested next to an unrelated caffeinate, which kept running.
- It does **not** prevent sleep when the **lid closes**, or on low battery. The run needs:
  - the lid open (or clamshell mode with an external display and power);
  - AC power;
  - you logged in.

## 5. ⛔ Activation (only after written authorization)

```
R=~/FootballShadow; A=~/Library/LaunchAgents; U=gui/$(id -u)
for n in shadow shadow.caffeinate; do
  sed "s#__ROOT__#$R#g" $R/code/deploy/shadow/macos/com.footballpredictor.$n.plist > $A/com.footballpredictor.$n.plist
  plutil -remove Disabled $A/com.footballpredictor.$n.plist && plutil -lint $A/com.footballpredictor.$n.plist
done
pmset -g batt | head -1                                   # must say 'AC Power'
launchctl bootstrap $U $A/com.footballpredictor.shadow.caffeinate.plist
pmset -g assertions | grep "$(cat $R/run/caffeinate.pid)(caffeinate)"   # assertion present
launchctl bootstrap $U $A/com.footballpredictor.shadow.plist
launchctl kickstart -p $U/com.footballpredictor.shadow   # one supervised first cycle now
tail -n 3 $R/run/logs/cycles.err; tail -n 1 $R/run/logs/cycles.jsonl
sqlite3 $R/run/api_football_ledger.db "SELECT day, consumer, SUM(cost) FROM ledger GROUP BY 1, 2"
date -u +%FT%TZ > $R/run/reports/START.txt; echo m35-f00a477c1cad226d >> $R/run/reports/START.txt
```

From then on, launchd runs a cycle at every :00 and :30. A slot missed while the Mac was asleep runs **once** on wake, because launchd coalesces missed slots. That cycle records the gap as missed slots, and it never observes a fixture whose decision time has passed (tested).

## 6. Monitoring (2–3 times a day, about 1 minute)

```
R=~/FootballShadow
$R/venv/bin/python $R/code/scripts/shadow_monitor.py --run $R/run --manifest $R/run/shadow_manifest.v3.json --notify
echo "monitor exit $?"     # 0 = no alert; 1 = alerts listed under "alerts"
```

| Monitor field | Alert when |
|---|---|
| `cycles.last.age_minutes` | > 75 (stale: Mac asleep, logged out, or job stopped) |
| `cycles.missed_slots_24h`, `last_24h` against 48 | shown; a gap is evidence of sleep or downtime |
| `cycles.unfinished` | > 0 (a cycle was killed mid-way) |
| `network_24h` (connection, http_4xx/5xx, http_429) | ≥ 6 failures in 24 h; any 429 |
| `quota.ledger_used_today` / `by_consumer` | above 70, or a consumer other than `shadow_cycle`, `shadow_settle` or `shadow_preflight` |
| `quota.provider_remaining_today` | ≤ 20 |
| `integrity.quick_check`, `integrity.forward_evidence` | not ok |
| `host.caffeinate_running`, `host.power_source` | caffeinate not running; on battery |

Daily, in addition:
```
R=~/FootballShadow
$R/venv/bin/python $R/code/scripts/shadow_report.py --db $R/run/engine_shadow.db --out $R/run/reports/$(date -u +%F).json --check-stale-minutes 90
sqlite3 $R/run/engine_shadow.db ".backup $R/run/backups/engine_shadow_$(date -u +%F).db"
pmset -g log | grep -E " Sleep  | Wake  " | tail -5     # when the Mac actually slept
```

The stop criteria are in `docs/M3_5_FORWARD_PROTOCOL.md` §6.

## 7. Emergency stop

The first command alone is enough; nothing is deleted.

```
R=~/FootballShadow; U=gui/$(id -u)
touch $R/run/SHADOW_STOP                                   # 1. every later cycle exits at once with 0 requests
launchctl kill SIGTERM $U/com.footballpredictor.shadow     # 2. ends a cycle in progress (no-op if none)
launchctl bootout $U/com.footballpredictor.shadow          # 3. no more cycles
launchctl bootout $U/com.footballpredictor.shadow.caffeinate   # 4. ends ONLY the experiment's caffeinate
pmset -g assertions | grep -c caffeinate                   #    other apps' caffeinate, if any, remain
```

If launchd is not available, use the pidfile instead:
```
bash $R/code/scripts/orchestration/shadow/mac_caffeinate.sh stop $R/run/caffeinate.pid
```

**Resume:** first understand and record the cause; the gap stays as missed slots. Then remove the stop file and bootstrap the jobs again, as in §5.

## 8. End of run or rollback

```
R=~/FootballShadow; A=~/Library/LaunchAgents; U=gui/$(id -u)
launchctl bootout $U/com.footballpredictor.shadow; launchctl bootout $U/com.footballpredictor.shadow.caffeinate
rm $A/com.footballpredictor.shadow.plist $A/com.footballpredictor.shadow.caffeinate.plist
$R/venv/bin/python $R/code/scripts/shadow_report.py --db $R/run/engine_shadow.db --out $R/run/reports/FINAL.json
tar -C ~ -czf ~/Desktop/football_predictor_backups/shadow_m35_evidence_$(date -u +%F).tgz FootballShadow/run
rm $R/etc/api_key.env
git -C ~/Desktop/football_predictor_ai-master worktree remove $R/code   # keep run/ as evidence
```

No production action is needed: production stays held, and its DB is untouched.

## 9. The localhost demo during the run

- The demo (pid 2670, port 8001, `--cap 5`) is a separate process. It has its own usage DB (`localhost_copy/demo/demo_api_usage.db`) and a hard lifetime cap of 5.
- It has **no access** to the shadow ledger or DB, and the shadow never calls it.
- Usage is **2 / 5**: 2026-10-17 20:36 and 2026-10-18 19:44 UTC. At most **3** more requests are possible, ever.
- It uses the same API key, so its requests count on the provider side, not in the shadow ledger.
- Worst-case daily provider total: 70 (shadow) + 3 (demo, lifetime) + 1 (preflight) = 74 ≤ 100.
- The shadow's own guard also stops when the provider reports ≤ 20 remaining.
- Do not raise or reset the demo's allowance.
