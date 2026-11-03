#!/usr/bin/env bash
# Launch Klotse. Creates .venv and installs requirements.txt on first run.
#   ./run.sh             start the app
#   ./run.sh --install   (re)install dependencies, then start
#   ./run.sh --check     verify the dependencies import, then exit
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=.venv
FORCE=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --install) FORCE=1 ;;
    --check) CHECK=1 ;;
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

MARKER="$VENV/.deps-installed"
[ -f "$MARKER" ] || FORCE=1
if [ "$FORCE" = 1 ]; then
  echo "Installing dependencies from requirements.txt ..."
  "$PY" -m pip install --disable-pip-version-check -r requirements.txt
  echo installed > "$MARKER"
fi

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
if [ "$CHECK" = 1 ]; then
  exec "$PY" -c "import klotse.ui.main_window, kubernetes, yaml; print('Klotse dependencies OK')"
fi
exec "$PY" -m klotse
