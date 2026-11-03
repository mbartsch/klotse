#!/usr/bin/env bash
# Run the offline test suite. Creates .venv and installs requirements-dev.txt on first run.
#   ./run_tests.sh                       run the full suite
#   ./run_tests.sh --install             (re)install dependencies, then run
#   ./run_tests.sh tests/test_app.py -k pods   any other arguments go to pytest
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=.venv
FORCE=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --install) FORCE=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done

# the venv layout differs between POSIX (bin/) and Windows (Scripts/, e.g. Git Bash)
venv_python() {
  if [ -x "$VENV/bin/python" ]; then echo "$VENV/bin/python"
  elif [ -x "$VENV/Scripts/python.exe" ]; then echo "$VENV/Scripts/python.exe"
  fi
}

PY="$(venv_python || true)"
if [ -z "$PY" ]; then
  BASE=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then BASE="$cand"; break; fi
  done
  if [ -z "$BASE" ]; then
    echo "ERROR: Python 3.10+ not found. Install it and re-run." >&2
    exit 1
  fi
  echo "Creating virtual environment in $VENV ..."
  "$BASE" -m venv "$VENV"
  PY="$(venv_python)"
  FORCE=1
fi

MARKER="$VENV/.dev-deps-installed"
[ -f "$MARKER" ] || FORCE=1
if [ "$FORCE" = 1 ]; then
  echo "Installing dependencies from requirements-dev.txt ..."
  "$PY" -m pip install --disable-pip-version-check -r requirements-dev.txt
  echo installed > "$MARKER"
fi

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
export QT_QPA_PLATFORM=offscreen
exec "$PY" -m pytest -q ${ARGS[@]+"${ARGS[@]}"}
