#!/usr/bin/env bash
# Build the hazreq Tauri AppImage.
#
# Produces a single hazreq-tauri-<arch>.AppImage that contains:
#   - the Rust Tauri shell (native WebKitGTK window, ~3 MB)
#   - the bundled Python 3.11 + uvicorn + hazreq app (~180 MB, extracted
#     from dist/hazreq-<arch>.AppImage so we don't duplicate the build)
#
# Host runtime deps (NOT bundled — must be on the Pi):
#   webkit2gtk, gtk+3   (libreoffice-writer only if HAZREQ_PDF_BACKEND=docx)
#
# Cross-build for the Pi 400 from x86_64 hardware is NOT supported here.
# Tauri's Rust code links against webkit2gtk-dev and gtk-3-dev, which
# means you need aarch64 sysroot libs to cross-compile. Easier to run
# this script on the Pi itself.
#
# Output:
#   dist/hazreq-tauri-<arch>.AppImage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

HOST_ARCH="$(uname -m)"
ARCH="${HAZREQ_BUILD_ARCH:-$HOST_ARCH}"
case "$ARCH" in
  x86_64)  RUST_TARGET="x86_64-unknown-linux-gnu";   APPIMG_ARCH="x86_64" ;;
  aarch64) RUST_TARGET="aarch64-unknown-linux-gnu";  APPIMG_ARCH="aarch64" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
CROSS=0
[ "$ARCH" != "$HOST_ARCH" ] && CROSS=1
if [ "$CROSS" -eq 1 ]; then
  echo "==> Cross-building Tauri shell for ${ARCH} on ${HOST_ARCH}"
  # The Rust target + a cross gcc + arm64 dev libs must already be on the
  # host. On Debian/Ubuntu that's: dpkg --add-architecture arm64, then
  # apt install gcc-aarch64-linux-gnu libwebkit2gtk-4.1-dev:arm64
  # libgtk-3-dev:arm64 librsvg2-dev:arm64 libayatana-appindicator3-dev:arm64
  # libsoup-3.0-dev:arm64 (force-overwrite for pango .gir conflicts) and
  # rustup target add ${RUST_TARGET}.
  command -v "${ARCH}-linux-gnu-gcc" >/dev/null \
    || { echo "missing ${ARCH}-linux-gnu-gcc cross compiler" >&2; exit 1; }
fi

BUILD="$REPO_ROOT/build-tauri/${ARCH}"
DIST="$REPO_ROOT/dist"
APPDIR="$BUILD/AppDir"
mkdir -p "$BUILD" "$DIST"

# ----- 1. Python AppImage (the backend bundle) --------------------
APPIMAGE="$DIST/hazreq-${ARCH}.AppImage"
if [ ! -x "$APPIMAGE" ]; then
  echo "==> Building underlying Python AppImage (${ARCH})"
  HAZREQ_BUILD_ARCH="$ARCH" "$REPO_ROOT/deploy/build_appimage.sh"
fi

# ----- 2. Tauri shell binary --------------------------------------
echo "==> Building Tauri shell for ${ARCH}"
if [ "$CROSS" -eq 1 ]; then
  ( cd "$REPO_ROOT/src-tauri" && \
    PKG_CONFIG_ALLOW_CROSS=1 \
    PKG_CONFIG_LIBDIR="/usr/lib/${ARCH}-linux-gnu/pkgconfig:/usr/share/pkgconfig" \
    PKG_CONFIG_SYSROOT_DIR=/ \
    cargo build --release --target "$RUST_TARGET" )
else
  ( cd "$REPO_ROOT/src-tauri" && cargo build --release --target "$RUST_TARGET" )
fi
SHELL_BIN="$REPO_ROOT/src-tauri/target/${RUST_TARGET}/release/hazreq-shell"
[ -x "$SHELL_BIN" ] || { echo "Tauri shell missing at $SHELL_BIN" >&2; exit 1; }

# ----- 3. Assemble AppDir -----------------------------------------
echo "==> Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
         "$APPDIR/opt/hazreq"

# Extract the python AppImage straight into opt/hazreq/runtime so the
# Tauri shell finds it via $APPDIR/opt/hazreq/runtime/.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ "$CROSS" -eq 1 ]; then
  QEMU="qemu-${ARCH}-static"
  command -v "$QEMU" >/dev/null \
    || { echo "missing $QEMU for cross-arch AppImage extraction" >&2; exit 1; }
  ( cd "$WORK" && "$QEMU" "$APPIMAGE" --appimage-extract >/dev/null )
else
  ( cd "$WORK" && "$APPIMAGE" --appimage-extract >/dev/null )
fi
cp -a "$WORK/squashfs-root" "$APPDIR/opt/hazreq/runtime"
# The python-appimage's AppRun would conflict with ours; drop it.
rm -f "$APPDIR/opt/hazreq/runtime/AppRun" \
      "$APPDIR/opt/hazreq/runtime/.DirIcon" \
      "$APPDIR/opt/hazreq/runtime/python3.11.desktop" 2>/dev/null || true

# Tauri shell + icon + desktop entry.
install -m 0755 "$SHELL_BIN" "$APPDIR/usr/bin/hazreq-shell"
install -m 0644 "$REPO_ROOT/src-tauri/icons/icon.png" \
                "$APPDIR/usr/share/icons/hicolor/256x256/apps/hazreq.png"
install -m 0644 "$REPO_ROOT/src-tauri/icons/icon.png" "$APPDIR/hazreq.png"

cat > "$APPDIR/hazreq.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=hazreq
Comment=Offline hazmat request generator
Exec=hazreq-shell
Icon=hazreq
Categories=Office;Utility;
Terminal=false
EOF
install -m 0644 "$APPDIR/hazreq.desktop" \
                "$APPDIR/usr/share/applications/hazreq.desktop"

# AppRun: forward to the Tauri shell. APPDIR is set automatically by
# AppImages, which is exactly what runtime_dir() in main.rs reads.
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
exec "$HERE/usr/bin/hazreq-shell" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ----- 4. appimagetool --------------------------------------------
# Always download the host-arch tool — it just calls mksquashfs and embeds
# the runtime named by the ARCH env var, so it packages any arch as long
# as ARCH is set correctly below.
case "$HOST_ARCH" in
  x86_64)  TOOL_ARCH="x86_64" ;;
  aarch64) TOOL_ARCH="aarch64" ;;
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

OUT="$DIST/hazreq-tauri-${APPIMG_ARCH}.AppImage"
ARCH="${APPIMG_ARCH}" "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "==> Built $(du -h "$OUT" | cut -f1) AppImage at: $OUT"
echo "    Pi host deps: sudo xbps-install -S webkit2gtk gtk+3   # libreoffice only if HAZREQ_PDF_BACKEND=docx"
echo "    Run:          $OUT"
