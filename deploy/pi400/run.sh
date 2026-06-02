#!/usr/bin/env bash
# hazreq — native Tk app launcher for the Raspberry Pi 400 (source path).
#
# This is the supported way to run hazreq on the Pi 400. It works on any
# arch and any distro: it provisions a local venv against the system Tk,
# installs the lean dependency set, runs the gated DB migration, and opens
# the native Tkinter window. (A self-contained AppImage is not shipped —
# see deploy/pi400/README.md for why.)
#
#   bash deploy/pi400/run.sh
#
# Requires the system Tk runtime (Tkinter is NOT pip-installable):
#   Raspberry Pi OS / Debian:  sudo apt install python3-tk
#   Void Linux:                sudo xbps-install -S python3-tkinter
#   Fedora:                    sudo dnf install python3-tkinter
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python3}"

# ----- 1. Tk present? (it ships with the OS, not pip) --------------
if ! "$PY" -c "import tkinter" 2>/dev/null; then
  cat >&2 <<'MSG'
ERROR: Python can't import tkinter.

Tk is part of the system Python, not a pip package. Install it:
  Raspberry Pi OS / Debian:  sudo apt install python3-tk
  Void Linux:                sudo xbps-install -S python3-tkinter
  Fedora:                    sudo dnf install python3-tkinter
then re-run this script.
MSG
  exit 1
fi

# ----- 2. Local venv + lean deps -----------------------------------
VENV="$REPO_ROOT/.venv-pi"
if [ ! -d "$VENV" ]; then
  echo "==> Creating venv at $VENV"
  "$PY" -m venv --system-site-packages "$VENV"   # inherit system tkinter
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

STAMP="$VENV/.deps-installed"
if [ ! -f "$STAMP" ]; then
  echo "==> Installing dependencies (one time)"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet \
    'sqlalchemy>=2.0' 'alembic>=1.13' 'docxtpl>=0.18' 'pypdf>=4.3' 'reportlab>=4.0'
  touch "$STAMP"
fi

# ----- 3. Launch (overlay PDF backend; migration is gated) ---------
export HAZREQ_PDF_BACKEND="${HAZREQ_PDF_BACKEND:-overlay}"
echo "==> Starting hazreq (native Tk)…"
exec python scripts/tk_prototype.py "$@"
