#!/usr/bin/env bash
# Runtime preflight, sourced by the launchers after they choose $PYTHON.
# Canonical runtime is Python 3.11 (runtime.txt). A frozen football baseline
# (BASELINE_FREEZE set) is only valid on the runtime recorded in its manifest,
# so a mismatched interpreter refuses to start instead of producing picks that
# the integrity check would reject anyway.
_fp_py="${PYTHON:-.venv/bin/python}"
if [ -z "${BASELINE_FREEZE:-}" ] && [ -f .env ]; then  # src/config.py also reads .env
  BASELINE_FREEZE="$(sed -n 's/^[[:space:]]*BASELINE_FREEZE[[:space:]]*=[[:space:]]*//p' .env | tail -1 | tr -d '"'"'"' ')"
fi
_fp_ver="$("$_fp_py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
echo "Python runtime: ${_fp_py} (${_fp_ver})"
if [ "${_fp_ver}" != "3.11" ]; then
  if [ -n "${BASELINE_FREEZE:-}" ]; then
    echo "ERROR: BASELINE_FREEZE=${BASELINE_FREEZE} requires Python 3.11 (found ${_fp_ver}). See docs/CUTOVER_PYTHON311.md." >&2
    return 1 2>/dev/null || exit 1
  fi
  echo "WARNING: canonical runtime is Python 3.11 (found ${_fp_ver}); see docs/CUTOVER_PYTHON311.md." >&2
fi
