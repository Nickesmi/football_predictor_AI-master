# M3.5.1 — Final operational blockers and activation readiness (2026-10-22, 17:50–17:55 +03)

The runtime at `7b8a29c` is accepted by the owner (fingerprint = manifest v4).

Nothing here changed system settings, spent quota, ran the provider check, started a caffeinate, enabled launchd, or touched production.

## 1. macOS updates (blocker)

**State:**
- `AutomaticDownload = 1`, `AutomaticallyInstallMacOSUpdates = 1`, `CriticalUpdateInstall = 1`, `ConfigDataInstall = 1`. Not managed by MDM.
- Last successful scan: today (the prefs show it in the Persian calendar, 1405-07-30).
- Pending, both "Recommended: YES, Action: restart":
  - **macOS Tahoe 26.7.1 (25G241)**;
  - macOS 27.0.1.
- A 3.2 GB update asset is cached (`/System/Library/AssetsV2/com_apple_MobileAsset_MacSoftwareUpdate`), so 26.7.1 is effectively downloaded.
- No restart is scheduled in `pmset -g sched`. Current macOS is 26.3.1 (a). Uptime is 64 days.

**Risk.** With automatic install on and the update staged, macOS installs it during an idle window, typically overnight on AC power, and restarts. With FileVault on, nothing runs until you log in: no cycles and no caffeinate. A whole night (≈ 8 h) loses ≈ 5 h 45 of kickoffs (readiness report §7).

**Recommendation (safest), in this order, before step 3 of the checklist:**
1. **Install 26.7.1 now, supervised, at a time you choose.** This removes the staged update and closes the 26.3.1 → 26.7.1 security gap. Otherwise the gap stays open for 14 more days on a Mac doing scheduled network work.
   - Use System Settings → General → Software Update → "Update Now" for 26.7.1 only, or `sudo softwareupdate --install "macOS Tahoe 26.7.1-25G241" --restart`.
   - **Do not install macOS 27.0.1.** It is a major upgrade, and changing the platform under the pinned runtime is not wanted.
   - The restart stops the demo, the vite server and the unrelated apps. Production stays held: it has no auto-start.
2. **Then turn off "Install macOS updates" for the 14 days:** System Settings → General → Software Update → ⓘ next to Automatic updates. Keep "Download new updates" on if you like.
   - Equivalent command: `sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates -bool false`.
   - Turn it back on when the run ends.
3. "Install Security Responses and system files" can stay on. Those are small and rarely restart. Turning them off too removes that last restart path, at the cost of urgent security fixes; that is your call.
4. After the restart, re-run the offline preflight (§6, step 0) to confirm the runtime still matches. The Homebrew Python 3.11.17 and the venv are not changed by a macOS point update, but the check is cheap.

Not recommended: changing `pmset` or using `sudo pmset disablesleep`. The experiment caffeinate covers sleep without a permanent change.

## 2. Packaged app credential exposure

| File | Mode | Content (values not shown) |
|---|---|---|
| `~/.football_predictor/` | `drwxr-xr-x` (0755) | — |
| `~/.football_predictor/settings.json` | **`-rw-r--r--` (0644)**, 98 B, 2026-10-10 | `api_key` set (32 chars), `api_host v3.football.api-sports.io` |
| `~/.football_predictor/settings.json.bak` | **0644**, 89 B, 2026-10-10 | `api_key` set (32 chars), **a different key** from `settings.json` |
| `~/Desktop/football_predictor_ai-master/.env` | **0644**, under `~/Desktop`, which is 0700 | non-empty API-Football key |

- **The key in `settings.json` is the same key as the repo `.env`.** I compared SHA-256 digests only; no key was displayed.
- **Who can read them today:**
  - your home is `drwxr-x---` (group `staff`);
  - the only other account, `macports` (uid 502, shell `/usr/bin/false`), is not in `staff`;
  - so the files are readable by **you, any process running as you** (apps, IDE plugins, scripts, containers given a mount of these paths), and root.
- No Time Machine destination is configured, so they are not copied into backups.
- **Real exposure: low for other accounts, but ordinary for anything running as you, and the key exists in 3 plaintext copies.**

**Recommended correction (yours to run; does not touch the app bundle):**
```
chmod 700 ~/.football_predictor
chmod 600 ~/.football_predictor/settings.json ~/.football_predictor/settings.json.bak
chmod 600 ~/Desktop/football_predictor_ai-master/.env
```
The app runs as you, so it keeps working. Its `save_settings` rewrites the existing file in place, which keeps mode 0600.

**Stronger option, which I recommend before activation:**
- Rotate the key in the API-Football dashboard, and type **only the new key** into `~/FootballShadow/etc/api_key.env`.
- The old key, in the app, the `.env` and the `.bak`, then cannot consume the shadow's quota at all, whatever runs.
- Rotation also neutralizes the demo, which is already 5/5.
- If you keep the current key, never open `Football Predictor AI.app`, and never run anything from the main repo with its `.env`, during the 14 days.
- The `.bak` key is a separate credential. If it is still valid at the provider, revoke it. If it is obsolete, delete the `.bak` (your decision).

## 3. Localhost demo

- pid 2670 (since 2026-10-17), `--cap 5`.
- Usage DB: **5 allowed, 0 refused**, unchanged. The last fetch was 2026-10-21 20:30 UTC (for 2026-10-22).
- No `API_FOOTBALL_*`/`ODDS_*` variables; only a listening socket on 127.0.0.1:8001; no outbound connection.

**Is stopping it necessary? No.** Every further fetch is refused in-process before anything is sent, so it cannot spend the shadow's quota, and its counter cannot be reset by a stop or a restart.
- Its only remaining activity: the open UI at :5173 will ask for 2026-10-23 tonight, and that produces one local `allowed = 0` row.
- Stopping it is optional hygiene. If you install 26.7.1 (§1), the restart stops it anyway. Do not restart it afterwards, or restart it knowing it stays at 5/5.

## 4. Mac conditions (17:51 +03)

| Check | Result |
|---|---|
| Power | **AC Power**, battery 93%, charging |
| Lid | open (`AppleClamshellState = No`) |
| Storage | 250 GiB free of 926 GiB |
| Network | route via `en0` (Wi-Fi), Cloudflare WARP "Connected / healthy", DNS through WARP (127.0.2.2). 15/15 HTTPS probes returned 200, but **slow: 0.04–4.9 s**; ping 1.1.1.1 0% loss, **avg 511 ms, max 748 ms** (it was 53 ms at 17:26). A Phone/CoreMedia call was active at the time. **Usable, but degraded right now.** Recheck before step 4 (stopping condition S1). |
| Sleep assertions | `PreventUserIdleSystemSleep` = 1, held by: the unrelated recurring `caffeinate -i -t 300` (pid 54496 now), powerd (display on), Phone/coreaudiod (the call), sharingd (Handoff), and Claude/Electron `NoIdleSleepAssertion`. **No experiment caffeinate** (`mac_caffeinate.sh status` = `not running`; no pidfile). None of these will hold once you are idle and the call ends; that is what the experiment caffeinate is for. |

## 5. Pins and state, reverified

| Item | Value | Result |
|---|---|---|
| Manifest v4 | `run/shadow_manifest.v4.json` sha256 `fcc91b86d7d7ea89…`, equal to the backup copy; id `m35-f206ea91f8facda2`; built from a clean tree at `0708a80` | match |
| Code | worktree `7b8a29c`, 0 changes; fingerprint `215dbba63b5b8f43…`, 224 files | **matches v4** |
| Python and dependencies | 3.11.17; `pip freeze` = lock file | match |
| Isolated DB | `run/engine_shadow.db` sha256 `0f892593a0d4e97b…`, equal to the source; mtime 17:29 (copy time); WAL 0 bytes | byte-identical to the pinned DB |
| Shared ledger | `run/api_football_ledger.db`: `PRAGMA integrity_check` ok, journal `delete`, schema as in `src/odds/ledger.py`, **0 rows**, inside the 0700 `~/FootballShadow` | intact, unused |
| Runtime DB contents (immutable read) | 0 cycles, 0 observations, 0 quota-log rows, 0 odds quotes, 0 picks | **no shadow cycle has executed** |
| launchd | no football/shadow plist in `~/Library/LaunchAgents`, `/Library/LaunchAgents` or `/Library/LaunchDaemons`; neither label loaded; no override entries; staged plists `Disabled = true`; no crontab | **disabled** |
| Processes | no `shadow_cycle`, `mac_cycle`, `shadow_settle` or experiment caffeinate; no `SHADOW_STOP` | — |
| Production | `data/engine.db` 15:35 / WAL 15:43 on 2026-10-16, sizes unchanged (metadata only; not opened today); hold `m3-hold-20261016-401a4675` present | unchanged |

## 6. Activation checklist: supervised first cycle

`R=~/FootballShadow; U=gui/$(id -u); A=~/Library/LaunchAgents`. ⛔ steps need your explicit authorization. Full commands are in `docs/M3_5_1_ACTIVATION_PREFLIGHT_REPORT.md` §5.

| # | Step | Proceed only if… | Otherwise STOP, and |
|---|---|---|---|
| 0 | (after any OS update) offline preflight, dummy-key-free: `--run-tests --offline` with the real env files | only `2_credentials` fails (no key yet) | read the failing check |
| 1 | You type the key into `$R/etc/api_key.env` (0600) | `grep -c '^API_FOOTBALL_KEY=.\+'` = 1, mode `-rw-------` | — |
| 2 ⛔ | Provider check: preflight `--run-tests --check-provider`, **once** | exit 0, every check pass; ledger has exactly 1 row (`shadow_preflight /status`); provider `limit_day ≥ 90`, remaining ≥ 90 today | **S2**: do not retry; report |
| 3 ⛔ | Start the experiment caffeinate (copy plist, remove `Disabled`, `bootstrap`) | `status` = `running <pid>`, and `pmset -g assertions` shows `pid <pid>(caffeinate)` | **S3**: `bootout` the label |
| 4 ⛔ | One manual cycle via `mac_cycle.sh`, not at :00 or :30, with no schedule installed | exit 0, a JSON report, cycle row `finished` | **S4**, **S5** |
| 5 ⛔ | Review cycle, observations, ledger, quota log and monitor | all acceptance conditions below | **S6** |
| 6 ⛔ | Install and `bootstrap` the cycle plist (no kickstart); write `START.txt` | `launchctl print` shows the job with calendar events | `bootout` |

**Stopping conditions for the supervised first cycle.** On any of these, run `touch $R/run/SHADOW_STOP`, do not continue to the next step, and report:

- **S1 Network.** Before step 4, 5 HTTPS probes to neutral hosts must all return 200 within 3 s each, with ping loss ≤ 10%. If not, wait. Do not start the cycle on a degraded link. If WARP state changes, recheck.
- **S2 Provider check.** Stop on exit ≠ 0, on any check not `pass`, on a ledger with more than 1 row after step 2, on a `limit_day` below 90, or on today's remaining below 90.
- **S3 Power and sleep.** Stop if the power source is not `AC Power`, if the lid is closed, or if the experiment caffeinate assertion is missing.
- **S4 Refusal or error.** Stop on cycle exit ≠ 0. Exit 2 means it refused, fail closed, with 0 sent; read `cycles.err`. Also stop on any Python traceback, a cycle row not `finished`, or `report.errors` containing `HTTP 401/403` (key problem), `HTTP 429`, or `remaining`.
- **S5 Quota.** The cycle's sends must be ≤ 20. The day's ledger total must be ≤ 70, with consumers only `shadow_preflight` and `shadow_cycle`. Provider-reported remaining must be > 20. Stop on any request to a host other than API-Football in `provider_quota_log`.
- **S6 Evidence integrity.** Every observation, if any, must have `manifest_id = m35-f206ea91f8facda2`, `decided_at < kickoff` and `counts_toward_baseline = 0`. The monitor's `forward_evidence.ok` must be true and `quick_check` must be `ok`. Stop on any official pick. Zero observations is acceptable for a first cycle (only fixtures within 135 min of kickoff are decided). Do not "fix" that by forcing anything.
- **S7 Anything unexpected.** A second process, a launchd job you did not bootstrap, or a demo `allowed = 1` row beyond 5.

**After step 6:** the 14 days run unattended under the monitoring in readiness report §4 and the stop criteria in the forward protocol §6. The emergency stop is the `SHADOW_STOP` file, then `launchctl kill SIGTERM`, then `bootout` (runbook §7).

## 7. Remaining owner actions

1. **macOS update:** install 26.7.1 supervised (recommended) and/or turn off "Install macOS updates" for 14 days. Approve, or do it yourself. Do not install 27.0.1.
2. **Credentials:** run the `chmod` correction (§2). Decide whether to rotate the API key; I recommend it, then enter only the new key in the shadow. Revoke or delete the `.bak` key.
3. **Demo:** stopping is not required. Decide whether to leave it running.
4. **Network:** run step 4 only when S1 holds (not during a call on a degraded link).
5. **Step 1:** type the key.
6. **Authorization** for steps 2–6. The scheduler stays disabled until step 6.

**Stopped for review.**
