#!/usr/bin/env bash
# Build a hazreq AppImage.
#
# Bundles Python 3.11 + all Python deps + the app code into a single
# self-contained executable. LibreOffice and CUPS are NOT bundled —
# they're expected on the host (or skipped entirely if you switch
# HAZREQ_PDF_BACKEND=fillable_pdf).
#
# Run on the SAME architecture you want the AppImage for. To build for
# the Pi 400, run this on the Pi (or in an aarch64 chroot / qemu-static
# environment).
#
# Output: dist/hazreq-<arch>.AppImage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ----- arch detection ----------------------------------------------
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  PY_ARCH="x86_64";   APPIMG_ARCH="x86_64" ;;
  aarch64) PY_ARCH="aarch64";  APPIMG_ARCH="aarch64" ;;
  armv7l)  PY_ARCH="armv7l";   APPIMG_ARCH="armhf"  ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
echo "==> Building for ${ARCH}"

PY_VERSION="${PY_VERSION:-3.11}"
PY_FULL="${PY_FULL:-3.11.14}"
BUILD="$REPO_ROOT/build-appimage"
DIST="$REPO_ROOT/dist"
mkdir -p "$BUILD" "$DIST"

# ----- 1. Download the python-appimage runtime --------------------
PY_AI_URL="https://github.com/niess/python-appimage/releases/download/python${PY_VERSION}/python${PY_FULL}-cp311-cp311-manylinux_2_28_${PY_ARCH}.AppImage"
PY_AI="$BUILD/python.AppImage"
if [ ! -f "$PY_AI" ]; then
  echo "==> Downloading python-appimage from $PY_AI_URL"
  if command -v curl >/dev/null; then
    curl -fL -o "$PY_AI" "$PY_AI_URL"
  else
    wget -O "$PY_AI" "$PY_AI_URL"
  fi
  chmod +x "$PY_AI"
fi

# ----- 2. Extract into a working AppDir ----------------------------
APPDIR="$BUILD/AppDir"
rm -rf "$APPDIR"
( cd "$BUILD" && rm -rf squashfs-root && "$PY_AI" --appimage-extract >/dev/null )
mv "$BUILD/squashfs-root" "$APPDIR"

# ----- 3. Install Python dependencies into the AppDir's Python -----
# We don't pip-install the app itself — the source is rsynced into
# /opt/hazreq below and the AppRun puts that on PYTHONPATH. That means
# we never need the pyproject to be fully package-ready, and bumping
# the app code is a re-rsync rather than a re-pip-install.
echo "==> Installing dependencies into AppDir"
"$APPDIR/AppRun" -m pip install --no-cache-dir --upgrade pip
"$APPDIR/AppRun" -m pip install --no-cache-dir \
  'fastapi>=0.115' \
  'uvicorn[standard]>=0.32' \
  'sqlalchemy>=2.0' \
  'alembic>=1.13' \
  'jinja2>=3.1' \
  'python-multipart>=0.0.12' \
  'docxtpl>=0.18' \
  'pypdf>=4.3' \
  'reportlab>=4.0'

# ----- 4. Bundle the application source + bundled assets -----------
APP_DEST="$APPDIR/opt/hazreq"
mkdir -p "$APP_DEST"
rsync -a --delete \
  --exclude='.venv' --exclude='build-appimage' --exclude='dist' \
  --exclude='data/hazreq.db*' --exclude='data/pdfs' --exclude='data/backups' \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  "$REPO_ROOT/app" "$REPO_ROOT/scripts" "$REPO_ROOT/data" \
  "$REPO_ROOT/alembic.ini" "$REPO_ROOT/pyproject.toml" \
  "$REPO_ROOT/README.md" "$REPO_ROOT/ROADMAP.md" \
  "$APP_DEST/"

# ----- 5. Write the launcher (AppRun) ------------------------------
# python-appimage ships AppDir/AppRun as a symlink into usr/bin; remove
# it so our heredoc writes a real file at the AppDir root instead of
# overwriting the bundled python launcher.
rm -f "$APPDIR/AppRun"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
# hazreq AppImage launcher.
#
# Resolves a writable data dir (~/.local/share/hazreq by default),
# runs Alembic migrations, generates the docx template if missing,
# then starts uvicorn and (optionally) opens the browser.
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
PY="$HERE/opt/python3.11/bin/python3.11"

# Fallback: some python-appimage builds expose python via $HERE/usr/bin
if [ ! -x "$PY" ]; then
  PY="$(find "$HERE" -maxdepth 5 -name 'python3.11' -type f -executable | head -n 1)"
fi
if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "Bundled Python not found inside AppImage" >&2
  exit 1
fi

DATA_DIR="${HAZREQ_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/hazreq}"
mkdir -p "$DATA_DIR" "$DATA_DIR/templates" "$DATA_DIR/pdfs" "$DATA_DIR/backups"
export HAZREQ_DATA_DIR="$DATA_DIR"
export HAZREQ_DB_URL="${HAZREQ_DB_URL:-sqlite:///$DATA_DIR/hazreq.db}"
export HAZREQ_TEMPLATE_PATH="${HAZREQ_TEMPLATE_PATH:-$DATA_DIR/templates/hazmat_chit.docx}"
export HAZREQ_PDF_DIR="${HAZREQ_PDF_DIR:-$DATA_DIR/pdfs}"
export HAZREQ_BACKUP_DIR="${HAZREQ_BACKUP_DIR:-$DATA_DIR/backups}"

PORT="${HAZREQ_PORT:-8000}"
HOST="${HAZREQ_HOST:-127.0.0.1}"

cd "$HERE/opt/hazreq"
export PYTHONPATH="$HERE/opt/hazreq:${PYTHONPATH:-}"

# First-run setup: migrations + docx template.
"$PY" -m alembic -c "$HERE/opt/hazreq/alembic.ini" upgrade head >/dev/null
if [ ! -f "$HAZREQ_TEMPLATE_PATH" ]; then
  echo "Generating starter docx template at $HAZREQ_TEMPLATE_PATH"
  "$PY" "$HERE/opt/hazreq/scripts/prepare_template.py"
fi

URL="http://${HOST}:${PORT}"
echo "==> hazreq is running at ${URL}"
echo "    Data: ${DATA_DIR}"
echo "    Stop with Ctrl-C"

# Optional auto-launch browser when running interactively (skip with HAZREQ_NO_BROWSER=1).
if [ -z "${HAZREQ_NO_BROWSER:-}" ] && command -v xdg-open >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  ( sleep 1.5; xdg-open "$URL" ) &
fi

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1
APPRUN
chmod +x "$APPDIR/AppRun"

# ----- 6. Desktop entry + icon -------------------------------------
cp "$REPO_ROOT/deploy/appimage/hazreq.desktop" "$APPDIR/hazreq.desktop"
cp "$REPO_ROOT/deploy/appimage/hazreq.desktop" "$APPDIR/usr/share/applications/hazreq.desktop"
cp "$REPO_ROOT/deploy/appimage/hazreq.png" "$APPDIR/hazreq.png"

# ----- 7. Run appimagetool -----------------------------------------
TOOL="$BUILD/appimagetool-${APPIMG_ARCH}.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${APPIMG_ARCH}.AppImage"
if [ ! -x "$TOOL" ]; then
  echo "==> Downloading appimagetool"
  if command -v curl >/dev/null; then
    curl -fL -o "$TOOL" "$TOOL_URL"
  else
    wget -O "$TOOL" "$TOOL_URL"
  fi
  chmod +x "$TOOL"
fi

OUT="$DIST/hazreq-${APPIMG_ARCH}.AppImage"
ARCH="${APPIMG_ARCH}" "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "==> Built $(du -h "$OUT" | cut -f1) AppImage at: $OUT"
echo "    Run:  $OUT"
echo "    Data lives at: ~/.local/share/hazreq/"
