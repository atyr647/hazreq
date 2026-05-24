#!/usr/bin/env bash
# Build a hazreq AppImage.
#
# Bundles Python 3.11 + all Python deps + the app code into a single
# self-contained executable. LibreOffice and CUPS are NOT bundled —
# they're expected on the host (or skipped entirely if you switch
# HAZREQ_PDF_BACKEND=fillable_pdf).
#
# By default builds for the host architecture. To cross-build for the
# Pi 400 from an x86_64 box, set HAZREQ_BUILD_ARCH=aarch64; this needs
# qemu-${arch}-static on PATH (extraction step) and the host's pip
# fetches aarch64 wheels via --platform/--python-version, so the target
# Python never has to execute during the build.
#
# Output: dist/hazreq-<arch>.AppImage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ----- arch detection ----------------------------------------------
HOST_ARCH="$(uname -m)"
ARCH="${HAZREQ_BUILD_ARCH:-$HOST_ARCH}"
case "$ARCH" in
  x86_64)  PY_ARCH="x86_64";   APPIMG_ARCH="x86_64";  PIP_PLATFORM="manylinux2014_x86_64" ;;
  aarch64) PY_ARCH="aarch64";  APPIMG_ARCH="aarch64"; PIP_PLATFORM="manylinux2014_aarch64" ;;
  armv7l)  PY_ARCH="armv7l";   APPIMG_ARCH="armhf";   PIP_PLATFORM="manylinux2014_armv7l" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
CROSS=0
[ "$ARCH" != "$HOST_ARCH" ] && CROSS=1
if [ "$CROSS" -eq 1 ]; then
  QEMU="qemu-${ARCH}-static"
  command -v "$QEMU" >/dev/null || { echo "Cross-build needs $QEMU on PATH" >&2; exit 1; }
  echo "==> Cross-building for ${ARCH} on ${HOST_ARCH} (via ${QEMU})"
else
  echo "==> Building for ${ARCH}"
fi

PY_VERSION="${PY_VERSION:-3.11}"
PY_FULL="${PY_FULL:-3.11.14}"
BUILD="$REPO_ROOT/build-appimage/${ARCH}"
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
if [ "$CROSS" -eq 1 ]; then
  ( cd "$BUILD" && rm -rf squashfs-root && "$QEMU" "$PY_AI" --appimage-extract >/dev/null )
else
  ( cd "$BUILD" && rm -rf squashfs-root && "$PY_AI" --appimage-extract >/dev/null )
fi
mv "$BUILD/squashfs-root" "$APPDIR"

# ----- 3. Install Python dependencies into the AppDir's Python -----
# We don't pip-install the app itself — the source is rsynced into
# /opt/hazreq below and the AppRun puts that on PYTHONPATH. That means
# we never need the pyproject to be fully package-ready, and bumping
# the app code is a re-rsync rather than a re-pip-install.
DEPS=(
  'fastapi>=0.115'
  'uvicorn[standard]>=0.32'
  'sqlalchemy>=2.0'
  'alembic>=1.13'
  'jinja2>=3.1'
  'python-multipart>=0.0.12'
  'docxtpl>=0.18'
  'pypdf>=4.3'
  'reportlab>=4.0'
)
SITE="$APPDIR/opt/python3.11/lib/python3.11/site-packages"
if [ "$CROSS" -eq 1 ]; then
  # Cross: have host pip fetch wheels for the target platform straight
  # into the bundled site-packages — keeps the target Python from ever
  # having to run during build.
  echo "==> Fetching ${ARCH} wheels with host pip into ${SITE}"
  python3 -m pip install --no-cache-dir --quiet --upgrade \
    --target "$SITE" \
    --platform "$PIP_PLATFORM" \
    --python-version 3.11 \
    --implementation cp \
    --abi cp311 \
    --only-binary=:all: \
    "${DEPS[@]}"
else
  echo "==> Installing dependencies into AppDir"
  "$APPDIR/AppRun" -m pip install --no-cache-dir --upgrade pip
  "$APPDIR/AppRun" -m pip install --no-cache-dir "${DEPS[@]}"
fi

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
  "$REPO_ROOT/BLANK NEW HAZMAT ISSUE CHIT 2.0.docx" \
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

HOST="${HAZREQ_HOST:-127.0.0.1}"

# Port: honour HAZREQ_PORT verbatim if set (fail loudly if taken). Otherwise
# scan 8000..8009 for the first free port, then fall back to whatever the
# kernel hands out — so two AppImages on the same box don't trip over each other.
if [ -n "${HAZREQ_PORT:-}" ]; then
  PORT="$HAZREQ_PORT"
else
  PORT="$("$PY" -c "
import socket, sys
for p in range(8000, 8010):
    s = socket.socket()
    try:
        s.bind(('$HOST', p))
        s.close()
        print(p); sys.exit(0)
    except OSError:
        s.close()
s = socket.socket(); s.bind(('$HOST', 0))
print(s.getsockname()[1]); s.close()
")"
fi

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

# Browser auto-launch. Defaults to a chromeless Chromium app window
# (chromium --app=URL); set HAZREQ_APP_MODE=0 to open in a regular browser
# tab instead, or HAZREQ_NO_BROWSER=1 to skip the launch entirely.
launch_browser() {
  local url="$1"
  [ -n "${HAZREQ_NO_BROWSER:-}" ] && return 0
  [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && return 0
  if [ "${HAZREQ_APP_MODE:-1}" != "0" ]; then
    for bin in chromium chromium-browser google-chrome chrome brave-browser microsoft-edge; do
      if command -v "$bin" >/dev/null; then
        ( sleep 1.5; "$bin" --app="$url" --window-size=1200,800 >/dev/null 2>&1 ) &
        return 0
      fi
    done
    echo "App mode requested but no Chromium-family browser found; falling back to default browser" >&2
  fi
  command -v xdg-open >/dev/null && ( sleep 1.5; xdg-open "$url" ) &
}
launch_browser "$URL"

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1
APPRUN
chmod +x "$APPDIR/AppRun"

# ----- 6. Desktop entry + icon -------------------------------------
cp "$REPO_ROOT/deploy/appimage/hazreq.desktop" "$APPDIR/hazreq.desktop"
cp "$REPO_ROOT/deploy/appimage/hazreq.desktop" "$APPDIR/usr/share/applications/hazreq.desktop"
cp "$REPO_ROOT/deploy/appimage/hazreq.png" "$APPDIR/hazreq.png"

# ----- 7. Run appimagetool -----------------------------------------
# Always download the host-arch appimagetool — it just calls mksquashfs
# and embeds the runtime named by the ARCH env var, so a host-arch tool
# can package any-arch AppDirs as long as ARCH is set correctly.
case "$HOST_ARCH" in
  x86_64)  TOOL_ARCH="x86_64" ;;
  aarch64) TOOL_ARCH="aarch64" ;;
  armv7l)  TOOL_ARCH="armhf" ;;
  *) TOOL_ARCH="$HOST_ARCH" ;;
esac
TOOL="$BUILD/appimagetool-${TOOL_ARCH}.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${TOOL_ARCH}.AppImage"
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
