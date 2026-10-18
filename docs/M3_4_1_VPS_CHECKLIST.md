# M3.4.1 — Linux VPS deployment checklist (Medium shadow profile)

**Status: checklist only.** No server is purchased, no credentials are transferred, no scheduler is
installed and no collection is started. Every step below needs explicit owner approval at deployment time.

The VPS runs **only** the forward shadow (`scripts/shadow_cycle.py --profile medium`) and its daily
report. It never runs the production app, the demo, the production database or any repo-root scratch script.

## 1. Minimum resources

| Item | Minimum | Notes |
|---|---|---|
| CPU | 1 vCPU (x86_64 or arm64) | a cycle is mostly I/O; the rh1 rebuild takes seconds |
| RAM | 1 GB (2 GB recommended) | pandas/scikit-learn imports peak about 400 MB |
| Disk | 10 GB SSD | shadow DB 67 MB at start; 14 days of odds and logs well under 1 GB; room for backups |
| OS | Ubuntu 24.04 LTS or Debian 12 | Python 3.11 from the image, or from deadsnakes / Debian 12 system Python 3.11 |
| Clock | UTC, NTP on (`timedatectl set-ntp true`) | decision timestamps and the UTC-day quota depend on it |
| Uptime | always on for 14 days | gaps are recorded as missed slots, never back-filled |

The image `deploy/shadow/Dockerfile` (python:3.11-slim) is an option; the same lock file and
tests passed in a linux/amd64 python:3.11 container (`docs/M3_4_1_AUTHORIZATION_REPORT.md` §1).

## 2. Code and data to copy

1. The repository at the pinned commit in `shadow_manifest.json` (`git_commit`), checked out clean.
2. The prepared directory produced by `scripts/prepare_shadow_db.py`:
   `engine_shadow.db`, `shadow_manifest.json`, `workspace/`.
3. Verify on arrival: `sha256sum engine_shadow.db` equals `database.sha256_at_preparation`.
4. Never copy `data/engine.db`, `.cache/`, `.env`, the demo directory or the local ledger.

## 3. Environment variables

Put them in `/etc/football-shadow/shadow.env` (mode `0640`, owner `root:shadow`). The API key
goes in a separate file, `/etc/football-shadow/api_key.env` (mode `0640`). It is entered on the host by the owner and never committed, imaged or sent.

| Variable | Value | Purpose |
|---|---|---|
| `API_FOOTBALL_KEY` | (owner enters on the host) | provider key |
| `API_FOOTBALL_LEDGER` | `/srv/shadow/api_football_ledger.db` | the single global quota ledger |
| `API_FOOTBALL_REQUIRE_LEDGER` | `1` | fail closed if the ledger is missing or unwritable |
| `API_FOOTBALL_CONSUMER` | `shadow_cycle` / `shadow_settle` | set per unit; recorded in the ledger |
| `ODDS_REQUEST_BUDGET_API_FOOTBALL` | `70` | per UTC day, all consumers together |
| `ODDS_MIN_REMAINING` | `20` | stop below the provider's own reported remaining allowance |
| `ODDS_MAX_AGE_MINUTES` | `240` | provisional freshness limit |
| `APP_ENV` | `production` | synthetic prices invisible |
| `FOOTBALL_PREDICTOR_DB_PATH` | `/srv/shadow/engine_shadow.db` | the shadow DB only |
| `FOOTBALL_PREDICTOR_CACHE_DIR` | `/srv/shadow/workspace/.cache` | isolated cache |
| `TZ` | `UTC` | |
| `PROBABILITY_INPUTS` | **unset** | the shadow chooses rh1 itself |
| `BASELINE_FREEZE` | **unset** | no baseline counting |
| `FOOTBALL_SERVICE_HOLD_RELEASE` | **unset** | production stays held |

The per-cycle cap of 20 is passed on the command line (`--max-requests-per-cycle 20`).

**Provider allowance rule.** The free plan allows 100 requests per day. 70 is the ceiling for all consumers on this host. Any other key user, such as the local demo, must not raise the
provider-side total above what is left. `ODDS_MIN_REMAINING=20` reads the provider's own
`x-ratelimit-requests-remaining` header and stops before the provider's actual allowance is used up.
If the same key is used anywhere else during the 14 days, the owner lowers `ODDS_REQUEST_BUDGET_API_FOOTBALL` to match.

## 4. Firewall

- Inbound: deny all except SSH (22/tcp) from the owner's IP; key-only SSH, no password login.
- Outbound: allow 443/tcp to `v3.football.api-sports.io`, plus NTP (123/udp).
- Outbound for setup only: apt mirrors and PyPI (`pypi.org`, `files.pythonhosted.org`). Close them after install.
- No web server, no open app port: the shadow has no HTTP interface.

## 5. Service user and permissions

- User `shadow` (system, no login shell, no sudo).
- `/srv/shadow` owned `shadow:shadow`, mode `0750`; the repo at `/opt/football-shadow`, owned by root, read-only to `shadow`.
- systemd hardening for both units:
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`,
  `ReadWritePaths=/srv/shadow`, `PrivateTmp=yes`, `PrivateDevices=yes`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `MemoryMax=1500M`.

## 6. Persistent storage

`/srv/shadow` (a separate volume if available) holds:
- `engine_shadow.db`, `workspace/`, `api_football_ledger.db`;
- `cycles/` (per-cycle JSON), `reports/` (daily), `logs/`;
- `backups/`: a daily `sqlite3 .backup` of the shadow DB and the ledger, with 14 copies kept.

The ledger must survive restarts and reboots. It is the persistent quota, so it is never deleted or reset during the 14 days.

## 7. Units (templates only, not installed)

`footballpredictor-shadow.service`: `Type=oneshot`, `User=shadow`, both env files, and:
```
ExecStart=/opt/football-shadow/.venv/bin/python scripts/shadow_cycle.py \
  --db /srv/shadow/engine_shadow.db --ledger /srv/shadow/api_football_ledger.db \
  --profile medium --max-requests-per-cycle 20 --budget 70
TimeoutStartSec=25min
```
`footballpredictor-shadow.timer`: `OnCalendar=*:00,30` (the 30-minute slot), `Persistent=false`
(no catch-up run after downtime; gaps are recorded as missed slots), `AccuracySec=1min`.

`footballpredictor-shadow-report.timer`: daily at 00:15 UTC, running `scripts/shadow_report.py --check-stale-minutes 90`.

## 8. Restart behaviour

- Duplicate runs: the cycle lease and the OS `flock` stop a second cycle. A timer firing during a running cycle exits without sending.
- Crash or reboot: the next slot runs normally. The ledger and the per-window attempt counts persist, so a restart never resets the 70/day or the 2 attempts per fixture window.
- HTTP 429 or provider errors: the cycle stops sending for that cycle. Failed requests still count.
- No `Restart=` loop on the oneshot service (`Restart=no`): a failing cycle waits for the next slot.

## 9. Monitoring

- Daily: `shadow_report.py` exit code (3 = stale, meaning the host or scheduler stopped), `integrity.ok`,
  the ledger day total ≤ 70, the provider remaining ≥ 20, missed slots, forward observations and identity share.
- `journalctl -u footballpredictor-shadow` for errors; disk usage below 80 %.
- Stop criteria are in `docs/M3_4_READINESS_REPORT.md` §5. On any one of them, disable the timer and report.

## 10. Rollback

1. Run `systemctl disable --now footballpredictor-shadow.timer footballpredictor-shadow-report.timer`.
2. Keep `/srv/shadow` intact as evidence (copy it off the host before any teardown).
3. Remove the API key file. If the host is decommissioned, rotate the key with the provider.
4. No production rollback is needed: production is held, untouched and on another machine.

## 11. Pre-start verification on the host (before enabling anything)

1. Run `scripts/orchestration/linux_validation.sh`, or the same pytest commands in the venv: all pass.
2. Check the `sha256sum` of the shadow DB and the `git rev-parse HEAD` against the manifest.
3. Run `python scripts/shadow_cycle.py --db … --budget 70` with no ledger. It must exit 2 ("fail closed").
4. Make the ledger unwritable and confirm exit 2. Then restore the permissions.
5. Run a dry run with `scripts/shadow_dry_run.py --profile medium --budget 70`: the day total stays ≤ 70.
6. Owner approval, then enable the timer.
