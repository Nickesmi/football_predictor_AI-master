# M3.5 — OPTIONAL Linux VPS runbook (future alternative, not the M3.5 plan)

> **Not the deployment plan.** The owner chose this Mac as the host; see `docs/M3_5_MAC_RUNBOOK.md`. This file is kept as an optional future artefact.
>
> Its pins (v2, `ddbf39e`) are **superseded** by manifest v4 `m35-f206ea91f8facda2` (`0708a80`), which superseded v3. If this path is ever used, substitute `shadow_manifest.v4.json` and `0708a80`. The cycle refuses any other code anyway.


**Nothing in this runbook has been executed.** Some steps need a separate owner authorization, marked ⛔:
- buying or choosing a server;
- entering the API key;
- installing the units;
- enabling the timer.

The commands assume Ubuntu 24.04 LTS or Debian 12 with systemd, run as root (`sudo -i`). They extend `docs/M3_4_1_VPS_CHECKLIST.md` (resources, firewall). One correction to that checklist: the prepared shadow DB is **167 MB**, not 67 MB.

Pinned artefacts:
- manifest `m35-59658cae50a480cc` (`shadow_manifest.v2.json`, superseding v1 `c72b2571…`);
- code commit `ddbf39e`, code fingerprint `736b5e1e…`;
- shadow DB sha256 `0f892593a0d4e97b…`.

## 0. ⛔ Host (owner)

Choose an always-on Linux host: 1 vCPU, 2 GB RAM, 10 GB SSD. Then:
- key-only SSH from the owner's IP;
- the firewall from the checklist §4;
- UTC with NTP.

```
timedatectl set-timezone UTC && timedatectl set-ntp true && timedatectl status | grep -E "Time zone|synchronized"
ufw default deny incoming && ufw default deny outgoing && ufw allow in from <owner-ip> to any port 22 proto tcp
ufw allow out 53 && ufw allow out 123/udp && ufw allow out to any port 443 proto tcp   # 443 narrowed after setup (§2)
ufw enable
```

## 1. Service account and directories

```
apt-get update && apt-get install -y python3.11 python3.11-venv git sqlite3   # Debian 12: python3 is 3.11
useradd --system --home-dir /srv/shadow --shell /usr/sbin/nologin shadow
install -d -o root   -g root   -m 0755 /opt/football-shadow
install -d -o shadow -g shadow -m 0750 /srv/shadow /srv/shadow/logs /srv/shadow/reports /srv/shadow/backups /srv/shadow/workspace /srv/shadow/workspace/.cache
install -d -o root   -g shadow -m 0750 /etc/football-shadow
```

## 2. Code at the pinned revision (read-only for the service)

The repository is local only (never pushed), so copy it from the Mac. Do not clone it from a remote.

```
# on the Mac (no credentials in the bundle: .env files are untracked and not in git)
git -C ~/Desktop/football_predictor_ai-master bundle create /tmp/fp-ddbf39e.bundle m3.5/forward-shadow-prep   # contains ddbf39e
scp /tmp/fp-ddbf39e.bundle <host>:/root/
# on the host
git clone /root/fp-ddbf39e.bundle /opt/football-shadow && git -C /opt/football-shadow checkout --detach ddbf39e
python3.11 -m venv /opt/football-shadow/.venv
/opt/football-shadow/.venv/bin/pip install --no-cache-dir -r /opt/football-shadow/requirements-lock.txt
chown -R root:root /opt/football-shadow && chmod -R go-w /opt/football-shadow
```

After the install, narrow outbound 443 to the provider only. If the firewall cannot filter by name, resolve the provider address each time; the cycle never contacts any other host.

> ⚠ A branch other than `ddbf39e`, for example one with later doc-only commits, gives the same code fingerprint. The preflight checks the **fingerprint**, so any code difference is refused.

## 3. Shadow data (copied, never regenerated on the host)

```
# on the Mac
cd ~/Desktop/football_predictor_backups/m3_4_1_shadow
shasum -a 256 engine_shadow.db shadow_manifest.json shadow_manifest.v2.json
scp engine_shadow.db shadow_manifest.json shadow_manifest.v2.json <host>:/root/shadow_in/
# on the host
cd /root/shadow_in && sha256sum engine_shadow.db     # must be 0f892593a0d4e97bffda161dc401915917d5eb47c3b808fe27e6eb25533b7959
install -o shadow -g shadow -m 0640 engine_shadow.db /srv/shadow/engine_shadow.db
install -o root   -g shadow -m 0440 shadow_manifest.json shadow_manifest.v2.json /srv/shadow/
```

Never copy:
- `data/engine.db` (production; it is also refused by every shadow script);
- `.cache/`;
- `.env`;
- the demo directory;
- the Mac's ledger.

The host's ledger starts empty at `/srv/shadow/api_football_ledger.db`. It is never reset during the 14 days.

## 4. Environment and ⛔ credentials

```
install -o root -g shadow -m 0640 /opt/football-shadow/deploy/shadow/shadow.env.example /etc/football-shadow/shadow.env
install -o root -g shadow -m 0640 /opt/football-shadow/deploy/shadow/api_key.env.example /etc/football-shadow/api_key.env
# ⛔ the owner types the key on the host (never sent by chat, e-mail or this repository):
sudoedit /etc/football-shadow/api_key.env        # API_FOOTBALL_KEY=<key>
```

During the 14 days, the same key must not be used elsewhere at volume. If the local demo still runs, it has 4 sends left at most (cap 5, 1 used). Lower `ODDS_REQUEST_BUDGET_API_FOOTBALL` by any other planned use, so the provider-side total stays ≤ 100 per day.

## 5. Preflight (as the service user, before anything is installed)

```
cd /srv/shadow
sudo -u shadow env $(grep -hv '^#' /etc/football-shadow/shadow.env | xargs) \
  /opt/football-shadow/.venv/bin/python /opt/football-shadow/scripts/shadow_preflight.py \
  --manifest /srv/shadow/shadow_manifest.v2.json --db /srv/shadow/engine_shadow.db \
  --env-file /etc/football-shadow/shadow.env --env-file /etc/football-shadow/api_key.env \
  --run-tests --check-provider --out /srv/shadow/reports/preflight.json
echo "preflight exit $?"      # must be 0; every check "pass"
```

`--check-provider` sends **one** counted `GET /status` (consumer `shadow_preflight`). Run it as close to the start as possible, and not more than a few times.

| # | Preflight check | Requirement it proves |
|---|---|---|
| 1 | `1_linux_tests_on_host` | shadow, quota, identity, rh1, K53, manifest and golden tests pass **on this host** |
| 2 | `2_credentials` | key present, read from a root-owned env file not readable by others, not in the code |
| 3 | `3_provider_quota` | plan active, `limit_day` ≥ 70 + 20, enough left today |
| 4 | `4_shared_ledger_all_consumers` | ledger required, writable, persistent; today's consumers only `shadow_cycle`, `shadow_settle` and `shadow_preflight`; total ≤ 70 |
| 5 | `5_zero_counted_official_picks` | 0 counted rows, 0 picks |
| 6 | `6_no_historical_row_as_forward` | 0 observations from another manifest, without a cycle, or at or after kickoff |
| 7, 7b | `7_pinned_rh1_identity_configuration`, `7b_rh1_rebuild` | code fingerprint, Python 3.11, identity-v1 and registry hash, rh1, Medium profile, quota, odds schema, model and selection versions; rh1 rebuild reproduces the stored table with 0 collisions |
| 8, 8a | `8_quarantine_and_provenance`, `8a_db_is_the_pinned_db` | 120,896 quarantined rows still out of their tables with intact copies; integrity ok; DB byte-identical to the pinned DB |
| 9 | `9_no_overlapping_cycles` | the cycle lock is exclusive (plus the 10-min cycle lease and systemd oneshot) |
| 10 | `10_failed_requests_count` | an HTTP 500 is counted in the ledger |
| 11 | `11_monitoring_and_emergency_stop` | the stale alarm fires (exit 3), and `SHADOW_STOP` stops a cycle with 0 requests |

Fail-closed refusals, all exit 2 with no request:
- no ledger, or an unwritable ledger;
- no `--manifest`;
- any manifest mismatch;
- a missing identity registry;
- a shrunken history source.

## 6. ⛔ Install and enable (only after written authorization)

```
install -m 0644 /opt/football-shadow/deploy/shadow/systemd/footballpredictor-shadow{,-report}.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/footballpredictor-shadow.service
systemctl start footballpredictor-shadow.service          # one supervised first cycle
journalctl -u footballpredictor-shadow -n 50 --no-pager; tail -n 1 /srv/shadow/logs/cycles.jsonl
sqlite3 /srv/shadow/api_football_ledger.db "SELECT day, consumer, SUM(cost) FROM ledger GROUP BY 1, 2"
systemctl enable --now footballpredictor-shadow.timer footballpredictor-shadow-report.timer
systemctl list-timers 'footballpredictor-*'
```

The 14 days start at the first timer-run cycle. Record that UTC time in `/srv/shadow/reports/START.txt`, together with the manifest id.

## 7. Daily operation

```
sudo -u shadow /opt/football-shadow/.venv/bin/python /opt/football-shadow/scripts/shadow_report.py \
  --db /srv/shadow/engine_shadow.db --out /srv/shadow/reports/$(date -u +%F)-manual.json --check-stale-minutes 90
sqlite3 /srv/shadow/api_football_ledger.db "SELECT consumer, SUM(cost) FROM ledger WHERE day = date('now') GROUP BY 1"
sudo -u shadow sqlite3 /srv/shadow/engine_shadow.db ".backup /srv/shadow/backups/engine_shadow_$(date -u +%F).db"
df -h /srv/shadow; journalctl -u footballpredictor-shadow --since today -p warning --no-pager | tail
```

Apply the stop criteria in `docs/M3_5_FORWARD_PROTOCOL.md` §6.

## 8. Stop procedures

**Emergency stop.** This is immediate, and it works even if systemd is misbehaving:
```
touch /srv/shadow/SHADOW_STOP                  # the next cycle (and the unit's ConditionPathExists) does nothing
systemctl stop footballpredictor-shadow.service  # ends a cycle in progress (observations are written atomically per fixture)
```

**Planned stop or end of day 14:**
```
systemctl disable --now footballpredictor-shadow.timer footballpredictor-shadow-report.timer
systemctl stop footballpredictor-shadow.service
sudo -u shadow /opt/football-shadow/.venv/bin/python /opt/football-shadow/scripts/shadow_report.py \
  --db /srv/shadow/engine_shadow.db --out /srv/shadow/reports/FINAL.json
tar -C /srv -czf /root/shadow_evidence_$(date -u +%F).tgz shadow   # then copy off the host
```

**Resume after an emergency stop.** This needs the cause to be understood. Gaps remain as missed slots.
```
rm /srv/shadow/SHADOW_STOP
```

**Rollback or teardown:**
1. Disable the timers.
2. Archive `/srv/shadow` and copy it off the host.
3. Remove `/etc/football-shadow/api_key.env`.
4. Rotate the key with the provider if the host is decommissioned.

No production action is needed: production is on the Mac, held and untouched.
