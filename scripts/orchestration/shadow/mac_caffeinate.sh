#!/bin/bash
# M3.5 experiment-specific sleep prevention (no pmset change, nothing permanent).
#   mac_caffeinate.sh run    <pidfile> [seconds]   exec /usr/bin/caffeinate -i -s -t seconds (default 15 days)
#   mac_caffeinate.sh status <pidfile>             exit 0 = ours running, 1 = not running
#   mac_caffeinate.sh stop   <pidfile>             stops ONLY the caffeinate recorded in pidfile
# -i prevents idle sleep; -s prevents system sleep on AC power only. Neither prevents lid-close sleep.
# The assertion ends when the process ends (stop, timeout, logout, reboot).
#
# Ownership: the pidfile records the pid, the process start time and the exact command line. A process
# is treated as ours only if all three still match, so a stale pidfile whose pid was reused (by another
# caffeinate or anything else) never stops that process. A second `run` while ours is running starts
# nothing. The timeout must be a whole number of seconds between 1 and 1296000 (15 days).
set -uo pipefail
export LC_ALL=C
MAX_SECS=1296000
cmd="${1:?run|status|stop}"; pidfile="${2:?pidfile}"; secs="${3:-$MAX_SECS}"
started() { ps -p "$1" -o lstart= 2>/dev/null | sed 's/^ *//; s/ *$//'; }
cmdline() { ps -p "$1" -o args= 2>/dev/null | sed 's/^ *//; s/ *$//'; }
alive() { local st; st="$(ps -p "$1" -o stat= 2>/dev/null | tr -d ' ')"; [ -n "$st" ] && [ "${st#Z}" = "$st" ]; }
# ours: prints the pid if the pidfile names a live process with the recorded start time and command line.
ours() {
  [ -f "$pidfile" ] || return 1
  local pid start args
  pid="$(sed -n 1p "$pidfile")"; start="$(sed -n 2p "$pidfile")"; args="$(sed -n 3p "$pidfile")"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ -n "$start" ] && [ -n "$args" ] || return 1
  [ "$(started "$pid")" = "$start" ] && [ "$(cmdline "$pid")" = "$args" ] || return 1
  echo "$pid"
}
case "$cmd" in
  run)
    case "$secs" in ''|*[!0-9]*) echo "refusing: timeout '$secs' is not a whole number of seconds" >&2; exit 2 ;; esac
    if [ "$secs" -lt 1 ] || [ "$secs" -gt "$MAX_SECS" ]; then
      echo "refusing: timeout $secs outside 1..$MAX_SECS seconds" >&2; exit 2
    fi
    if pid="$(ours)"; then echo "already running $pid (nothing started)"; exit 0; fi
    # exec keeps this pid and its start time; only the command line changes, to the one recorded here.
    printf '%s\n%s\n%s\n' "$$" "$(started $$)" "/usr/bin/caffeinate -i -s -t $secs" > "$pidfile"
    exec /usr/bin/caffeinate -i -s -t "$secs" ;;
  status)
    if pid="$(ours)"; then echo "running $pid"; else echo "not running"; exit 1; fi ;;
  stop)
    if pid="$(ours)"; then
      kill "$pid"
      for _ in 1 2 3 4 5 6 7 8 9 10; do alive "$pid" || break; sleep 0.5; done
      if alive "$pid"; then echo "stop sent to $pid but it is still running" >&2; exit 1; fi
      echo "stopped $pid"
    else
      echo "not running (nothing stopped)"
    fi
    rm -f "$pidfile" ;;
  *) echo "usage: $0 run|status|stop <pidfile> [seconds]" >&2; exit 2 ;;
esac
