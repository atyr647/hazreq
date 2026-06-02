#!/usr/bin/env bash
# Build a native Tk hazreq AppImage (no web stack).
#
# Unlike build_appimage.sh (which bundles niess/python-appimage and runs
# uvicorn + a browser), this packages the native Tkinter app. It uses
# python-build-standalone (PBS) as the bundled interpreter because, unlike
# the manylinux-based python-appimage, PBS ships _tkinter together with the
# Tcl/Tk runtime — which a Tk app needs and cannot easily be added later.
#
# The bundle is lean: SQLAlchemy + Alembic + docxtpl + pypdf + reportlab.
# No FastAPI / uvicorn / Jinja (the data layer was decoupled from FastAPI,
# so none of it is imported by the Tk app).
#
# By default builds for the host arch. Cross-build for the Pi 400 from
# x86_64 with HAZREQ_BUILD_ARCH=aarch64 (needs qemu-aarch64-static on PATH
# for the verification step; deps are fetched as target wheels via host pip
# so the target Python never has to run during the build).
#
# Output: dist/hazreq-tk-<arch>.AppImage
#
# NOTE: this script has not been exercised in CI — build it on a real host
# (x86_64 box for x86_64; the Pi itself for aarch64) and run the printed
# verification before trusting the artifact.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ----- arch detection ----------------------------------------------
HOST_ARCH="$(uname -m)"
ARCH="${HAZREQ_BUILD_ARCH:-$HOST_ARCH}"
case "$ARCH" in
  x86_64)  PBS_TRIPLE="x86_64-unknown-linux-gnu";  APPIMG_ARCH="x86_64";  PIP_PLATFORM="manylinux2014_x86_64" ;;
  aarch64) PBS_TRIPLE="aarch64-unknown-linux-gnu"; APPIMG_ARCH="aarch64"; PIP_PLATFORM="manylinux2014_aarch64" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
CROSS=0
[ "$ARCH" != "$HOST_ARCH" ] && CROSS=1
if [ "$CROSS" -eq 1 ]; then
  QEMU="qemu-${ARCH}-static"
  command -v "$QEMU" >/dev/null || echo "warning: $QEMU not found — the tkinter verification step will be skipped" >&2
  echo "==> Cross-building Tk AppImage for ${ARCH} on ${HOST_ARCH}"
else
  echo "==> Building Tk AppImage for ${ARCH}"
fi

# python-build-standalone release (interpreter ships _tkinter + Tcl/Tk).
PY_VERSION="${PY_VERSION:-3.11}"
PBS_TAG="${PBS_TAG:-20250115}"
PBS_PY="${PBS_PY:-3.11.11}"
BUILD="$REPO_ROOT/build-tk-appimage/${ARCH}"
DIST="$REPO_ROOT/dist"
mkdir -p "$BUILD" "$DIST"

# ----- 1. Download + extract python-build-standalone ---------------
PBS_ASSET="cpython-${PBS_PY}+${PBS_TAG}-${PBS_TRIPLE}-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_ASSET}"
PBS_TGZ="$BUILD/${PBS_ASSET}"
if [ ! -f "$PBS_TGZ" ]; then
  echo "==> Downloading python-build-standalone: $PBS_URL"
  if command -v curl >/dev/null; then curl -fL -o "$PBS_TGZ" "$PBS_URL"; else wget -O "$PBS_TGZ" "$PBS_URL"; fi
fi

APPDIR="$BUILD/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/opt"
# install_only archives extract to a top-level "python/" directory.
tar -xzf "$PBS_TGZ" -C "$APPDIR/opt"
PYHOME="$APPDIR/opt/python"
PYBIN="$PYHOME/bin/python${PY_VERSION}"
[ -x "$PYBIN" ] || { echo "bundled python missing at $PYBIN" >&2; exit 1; }

# ----- 2. Install lean dependencies --------------------------------
DEPS=(
  'sqlalchemy>=2.0'
  'alembic>=1.13'
  'docxtpl>=0.18'
  'pypdf>=4.3'
  'reportlab>=4.0'
)
SITE="$PYHOME/lib/python${PY_VERSION}/site-packages"
if [ "$CROSS" -eq 1 ]; then
  echo "==> Fetching ${ARCH} wheels with host pip into ${SITE}"
  python3 -m pip install --no-cache-dir --quiet --upgrade \
    --target "$SITE" \
    --platform "$PIP_PLATFORM" \
    --python-version "$PY_VERSION" \
    --implementation cp \
    --only-binary=:all: \
    "${DEPS[@]}"
else
  echo "==> Installing dependencies into the bundled Python"
  "$PYBIN" -m pip install --no-cache-dir --upgrade pip
  "$PYBIN" -m pip install --no-cache-dir "${DEPS[@]}"
fi

# ----- 3. Verify the bundled interpreter actually has Tk -----------
verify_tk() {
  local runner="$1"
  if $runner "$PYBIN" -c "import tkinter, _tkinter; print('tkinter', _tkinter.TK_VERSION)" 2>/dev/null; then
    echo "==> tkinter OK in the bundled interpreter"
  else
    echo "ERROR: the bundled Python cannot import tkinter. The AppImage would" >&2
    echo "       not run. Use a python-build-standalone build that ships Tk." >&2
    exit 1
  fi
}
if [ "$CROSS" -eq 0 ]; then
  verify_tk ""
elif command -v "$QEMU" >/dev/null; then
  verify_tk "$QEMU"
else
  # Can't execute the target Python; do a presence check instead. Recent PBS
  # builds STATICALLY link _tkinter into libpython (lib-dynload has no
  # _tkinter.so), so checking for that .so gives a false negative. What we
  # actually need bundled — and can't add later — is the tkinter package plus
  # the Tcl/Tk script libraries; the C extension rides inside libpython.
  # (Verified on the same PBS tag's x86_64 build: `import _tkinter` → TK 8.6
  # despite only 2 .so files in lib-dynload.)
  PYLIB="$PYHOME/lib/python${PY_VERSION}"
  if [ -d "$PYLIB/tkinter" ] \
     && ls -d "$PYHOME"/lib/tk8.* >/dev/null 2>&1 \
     && { ls "$PYLIB"/lib-dynload/_tkinter*.so >/dev/null 2>&1 \
          || ls "$PYHOME"/lib/libpython*.so* >/dev/null 2>&1; }; then
    echo "==> Tk runtime present (tkinter pkg + tk8.x libs; _tkinter linked into libpython)"
  else
    echo "ERROR: the bundled Python is missing Tk (no tkinter pkg / tk8.x libs)." >&2
    exit 1
  fi
fi

# ----- 4. Bundle the application source + the blank chit -----------
APP_DEST="$APPDIR/opt/hazreq"
mkdir -p "$APP_DEST"
SRC_ITEMS=(
  "app" "scripts" "data" "alembic.ini" "pyproject.toml"
  "Hazmat Request Blank.pdf" "BLANK NEW HAZMAT ISSUE CHIT 2.0.docx"
)
if command -v rsync >/dev/null; then
  rsync -a --delete \
    --exclude='.venv*' --exclude='build-appimage' --exclude='build-tk-appimage' \
    --exclude='dist' --exclude='data/hazreq.db*' --exclude='data/pdfs' \
    --exclude='data/backups' --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    "${SRC_ITEMS[@]/#/$REPO_ROOT/}" "$APP_DEST/"
else
  # rsync-less fallback: plain copy, then prune the junk rsync would exclude.
  echo "==> rsync not found; copying with cp + prune"
  for item in "${SRC_ITEMS[@]}"; do cp -a "$REPO_ROOT/$item" "$APP_DEST/"; done
  rm -rf "$APP_DEST/data/hazreq.db" "$APP_DEST/data/hazreq.db-wal" \
         "$APP_DEST/data/hazreq.db-shm" "$APP_DEST/data/pdfs" "$APP_DEST/data/backups"
  find "$APP_DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$APP_DEST" -name '*.pyc' -delete 2>/dev/null || true
fi

# ----- 5. Write the launcher (AppRun) ------------------------------
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
# hazreq native Tk launcher.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
PY="$HERE/opt/python/bin/python3.11"

DATA_DIR="${HAZREQ_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/hazreq}"
mkdir -p "$DATA_DIR" "$DATA_DIR/templates" "$DATA_DIR/pdfs" "$DATA_DIR/backups"
export HAZREQ_DATA_DIR="$DATA_DIR"
export HAZREQ_DB_URL="${HAZREQ_DB_URL:-sqlite:///$DATA_DIR/hazreq.db}"
export HAZREQ_PDF_DIR="${HAZREQ_PDF_DIR:-$DATA_DIR/pdfs}"
export HAZREQ_BACKUP_DIR="${HAZREQ_BACKUP_DIR:-$DATA_DIR/backups}"
# Pure-Python overlay PDF backend; the blank chit ships in the bundle.
export HAZREQ_PDF_BACKEND="${HAZREQ_PDF_BACKEND:-overlay}"
export HAZREQ_OVERLAY_PDF_PATH="${HAZREQ_OVERLAY_PDF_PATH:-$HERE/opt/hazreq/Hazmat Request Blank.pdf}"
# Point Tkinter at the bundled Tcl/Tk script libraries.
for d in "$HERE"/opt/python/lib/tcl8.* ; do [ -d "$d" ] && export TCL_LIBRARY="$d"; done
for d in "$HERE"/opt/python/lib/tk8.*  ; do [ -d "$d" ] && export TK_LIBRARY="$d";  done

cd "$HERE/opt/hazreq"
export PYTHONPATH="$HERE/opt/hazreq:${PYTHONPATH:-}"
exec "$PY" "$HERE/opt/hazreq/scripts/tk_prototype.py" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ----- 6. Desktop entry + icon -------------------------------------
cp "$REPO_ROOT/deploy/appimage/hazreq.png" "$APPDIR/hazreq.png" 2>/dev/null || true
cat > "$APPDIR/hazreq.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=hazreq
Comment=Offline hazmat request generator (native)
Exec=AppRun
Icon=hazreq
Categories=Utility;
Terminal=false
EOF
mkdir -p "$APPDIR/usr/share/applications"
cp "$APPDIR/hazreq.desktop" "$APPDIR/usr/share/applications/hazreq.desktop"

# ----- 7. Run appimagetool -----------------------------------------
case "$HOST_ARCH" in
  x86_64)  TOOL_ARCH="x86_64" ;;
  aarch64) TOOL_ARCH="aarch64" ;;
  *) TOOL_ARCH="$HOST_ARCH" ;;
esac
TOOL="$BUILD/appimagetool-${TOOL_ARCH}.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${TOOL_ARCH}.AppImage"
if [ ! -x "$TOOL" ]; then
  echo "==> Downloading appimagetool"
  if command -v curl >/dev/null; then curl -fL -o "$TOOL" "$TOOL_URL"; else wget -O "$TOOL" "$TOOL_URL"; fi
  chmod +x "$TOOL"
fi

OUT="$DIST/hazreq-tk-${APPIMG_ARCH}.AppImage"
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run where FUSE is unavailable
# (containers / minimal hosts) by self-extracting instead of mounting.
ARCH="${APPIMG_ARCH}" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "==> Built $(du -h "$OUT" | cut -f1) AppImage at: $OUT"
echo "    Native Tk — no LibreOffice, no browser, no host Python needed."
echo "    Run:  $OUT       (or double-click in a file manager)"
