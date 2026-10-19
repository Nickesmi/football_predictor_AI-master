#!/bin/bash
# M3.5 experiment-specific sleep prevention (no pmset change, nothing permanent).
#   mac_caffeinate.sh run    <pidfile> [seconds]   exec /usr/bin/caffeinate -i -s -t seconds (default 15 days)
#   mac_caffeinate.sh status <pidfile>
#   mac_caffeinate.sh stop   <pidfile>             stops ONLY the caffeinate recorded in pidfile
# -i prevents idle sleep; -s prevents system sleep on AC power only. Neither prevents lid-close sleep.
# The assertion ends when the process ends (stop, timeout, logout, reboot).
set -uo pipefail
cmd="${1:?run|status|stop}"; pidfile="${2:?pidfile}"; secs="${3:-1296000}"
ours() { [ -n "${1:-}" ] && ps -p "$1" -o comm= 2>/dev/null | grep -q 'caffeinate$'; }
case "$cmd" in
  run)
    echo $$ > "$pidfile"
    exec /usr/bin/caffeinate -i -s -t "$secs" ;;
  status)
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if ours "$pid"; then echo "running $pid"; else echo "not running"; exit 1; fi ;;
  stop)
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if ours "$pid"; then kill "$pid"; echo "stopped $pid"; else echo "not running (nothing stopped)"; fi
    rm -f "$pidfile" ;;
  *) echo "usage: $0 run|status|stop <pidfile> [seconds]" >&2; exit 2 ;;
esac
