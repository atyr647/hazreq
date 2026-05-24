#!/usr/bin/env bash
# Build a hazreq AppImage that bundles Void Linux's webkit2gtk stack.
#
# Why Void's webkit instead of Ubuntu's?
#   - Void's libwebkit2gtk-4.1.so.0 links zero libsystemd (Ubuntu's drags it in)
#   - Void places auxiliary processes at /usr/libexec/webkit2gtk-4.1/ (the
#     upstream-standard path), which resolves cleanly when the lib is loaded
#     from $APPDIR/usr/lib/. Ubuntu's multilib layout (/usr/lib/<arch>/...)
#     does not.
#
# This script consumes a pre-extracted Void aarch64 sysroot at
# /tmp/void/sysroot-built (populated by walking libwebkit2gtk41's
# transitive run_depends and downloading every .xbps from
# repo-default.voidlinux.org/current/aarch64/).
#
# Output: dist/hazreq-tauri-aarch64.AppImage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ARCH=aarch64
RUST_TARGET=aarch64-unknown-linux-gnu
SYSROOT=/tmp/void/sysroot-built
[ -d "$SYSROOT/usr/lib" ] || { echo "Void sysroot missing at $SYSROOT" >&2; exit 1; }

BUILD="$REPO_ROOT/build-tauri/${ARCH}-void"
DIST="$REPO_ROOT/dist"
APPDIR="$BUILD/AppDir"
mkdir -p "$BUILD" "$DIST"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/libexec" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
         "$APPDIR/opt/hazreq"

# ----- 1. Tauri shell binary --------------------------------------
SHELL_BIN="$REPO_ROOT/src-tauri/target/${RUST_TARGET}/release/hazreq-shell"
[ -x "$SHELL_BIN" ] || { echo "Build the shell first: HAZREQ_BUILD_ARCH=aarch64 deploy/build_tauri.sh" >&2; exit 1; }
install -m 0755 "$SHELL_BIN" "$APPDIR/usr/bin/hazreq-shell"
# Void puts libs flat at /usr/lib (no <arch>-linux-gnu subdir), so the
# shell needs to look one level up from its own dir. patchelf is
# arch-agnostic — runs on the build host, modifies any ELF.
patchelf --set-rpath '$ORIGIN/../lib' "$APPDIR/usr/bin/hazreq-shell"

# ----- 2. Void's libs (curated subset of the sysroot) -------------
echo "==> Copying Void libs (skipping bloat we don't need)"
# Top-level shared libs: copy all, then strip the bits we won't use.
cp -a "$SYSROOT"/usr/lib/*.so* "$APPDIR/usr/lib/" 2>/dev/null || true
# Module dirs (runtime-loaded by gtk/glib/webkit)
for d in gtk-3.0 gdk-pixbuf-2.0 gio glib-2.0 girepository-1.0 webkit2gtk-4.1; do
  [ -d "$SYSROOT/usr/lib/$d" ] && cp -a "$SYSROOT/usr/lib/$d" "$APPDIR/usr/lib/"
done
# Speech / codec / ghostscript / encoder bloat — not needed for a form UI
rm -rf "$APPDIR"/usr/lib/libflite*.so* \
       "$APPDIR"/usr/lib/libgs.so* \
       "$APPDIR"/usr/lib/libx264.so* \
       "$APPDIR"/usr/lib/libx265.so* \
       "$APPDIR"/usr/lib/libaom.so* \
       "$APPDIR"/usr/lib/libde265*.so* \
       "$APPDIR"/usr/lib/libheif*.so* \
       "$APPDIR"/usr/lib/libopenh264*.so* \
       "$APPDIR"/usr/lib/libwavpack*.so* \
       "$APPDIR"/usr/lib/libmp3lame*.so* \
       "$APPDIR"/usr/lib/librav1e*.so* \
       "$APPDIR"/usr/lib/libtwolame*.so* \
       "$APPDIR"/usr/lib/libtag.so* \
       "$APPDIR"/usr/lib/libdv*.so* \
       "$APPDIR"/usr/lib/libv4l*.so* \
       "$APPDIR"/usr/lib/libwebrtc*.so* \
       "$APPDIR"/usr/lib/libsbc*.so* \
       "$APPDIR"/usr/lib/libcelt*.so* \
       "$APPDIR"/usr/lib/libgstreamer*.so* \
       "$APPDIR"/usr/lib/libgst*.so* \
       "$APPDIR"/usr/lib/libaa.so* \
       "$APPDIR"/usr/lib/libalsa*.so* \
       "$APPDIR"/usr/lib/libopus*.so* \
       "$APPDIR"/usr/lib/libSvtAv1*.so* \
       "$APPDIR"/usr/lib/libjxl*.so* \
       "$APPDIR"/usr/lib/libhwy*.so* \
       "$APPDIR"/usr/lib/libvpx*.so* \
       "$APPDIR"/usr/lib/libvorbis*.so* \
       "$APPDIR"/usr/lib/libogg*.so* \
       "$APPDIR"/usr/lib/libtheora*.so* \
       "$APPDIR"/usr/lib/libFLAC*.so* \
       "$APPDIR"/usr/lib/libspeex*.so* \
       "$APPDIR"/usr/lib/libsnd*.so* \
       "$APPDIR"/usr/lib/libpulse*.so* \
       "$APPDIR"/usr/lib/libshout*.so* \
       "$APPDIR"/usr/lib/libmpg123*.so*

# ----- 3. WebKit auxiliary processes (upstream-standard path) -----
echo "==> Copying WebKit aux processes"
cp -a "$SYSROOT/usr/libexec/webkit2gtk-4.1" "$APPDIR/usr/libexec/"
rm -f "$APPDIR"/usr/libexec/webkit2gtk-4.1/MiniBrowser \
      "$APPDIR"/usr/libexec/webkit2gtk-4.1/jsc
# Aux processes find their libs via ../../lib (relative from libexec/<dir>)
for bin in WebKitNetworkProcess WebKitWebProcess WebKitGPUProcess; do
  patchelf --set-rpath '$ORIGIN/../../lib' \
    "$APPDIR/usr/libexec/webkit2gtk-4.1/$bin"
done

# ----- 4. Shared data: schemas, icons, fonts -----------------------
echo "==> Copying schemas / icons / mime"
mkdir -p "$APPDIR/usr/share/glib-2.0/schemas" \
         "$APPDIR/usr/share/icons" \
         "$APPDIR/usr/share/mime"
cp -a "$SYSROOT"/usr/share/glib-2.0/schemas/* "$APPDIR/usr/share/glib-2.0/schemas/"
cp -a "$SYSROOT"/usr/share/icons/Adwaita "$APPDIR/usr/share/icons/" 2>/dev/null || true
cp -a "$SYSROOT"/usr/share/icons/hicolor "$APPDIR/usr/share/icons/" 2>/dev/null || true
cp -a "$SYSROOT"/usr/share/mime/* "$APPDIR/usr/share/mime/" 2>/dev/null || true

# Drop oversized icon resolutions to keep AppImage small. Keep only the
# size webkit actually requests for inline UI (16x16 / scalable).
find "$APPDIR/usr/share/icons" -type d \
  \( -name '8x8' -o -name '22x22' -o -name '24x24' \
     -o -name '32x32' -o -name '48x48' -o -name '64x64' -o -name '96x96' \
     -o -name '128x128' -o -name '192x192' -o -name '256x256' \
     -o -name '512x512' \) \
  -exec rm -rf {} + 2>/dev/null || true
# Drop locale data — the UI is English-only for now
rm -rf "$APPDIR"/usr/share/locale 2>/dev/null || true
# Strip debug symbols from every ELF we ship (cuts ~10-20%)
find "$APPDIR/usr/lib" "$APPDIR/usr/libexec" "$APPDIR/usr/bin" -type f \
  \( -name '*.so' -o -name '*.so.*' -o -executable \) \
  -exec strip --strip-unneeded {} + 2>/dev/null || true
find "$APPDIR/opt/hazreq/runtime" -type f -name '*.so*' \
  -exec strip --strip-unneeded {} + 2>/dev/null || true

# Fontconfig config + base font set (Void's may be needed for webkit text)
mkdir -p "$APPDIR/etc/fonts"
cp -a "$SYSROOT"/etc/fonts/* "$APPDIR/etc/fonts/" 2>/dev/null || true

# ----- 5. Python runtime (existing AppImage) -----------------------
echo "==> Embedding Python runtime"
APPIMAGE_PY="$DIST/hazreq-${ARCH}.AppImage"
[ -x "$APPIMAGE_PY" ] || { echo "Missing $APPIMAGE_PY (run build_appimage.sh first)" >&2; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
( cd "$WORK" && qemu-aarch64-static "$APPIMAGE_PY" --appimage-extract >/dev/null )
cp -a "$WORK/squashfs-root" "$APPDIR/opt/hazreq/runtime"
rm -f "$APPDIR/opt/hazreq/runtime/AppRun" \
      "$APPDIR/opt/hazreq/runtime/.DirIcon" \
      "$APPDIR/opt/hazreq/runtime/python3.11.desktop" 2>/dev/null || true

# Strip Python bloat we don't use at runtime
PYLIB="$APPDIR/opt/hazreq/runtime/opt/python3.11/lib/python3.11"
SITE="$PYLIB/site-packages"
rm -rf "$PYLIB/idlelib" "$PYLIB/tkinter" "$PYLIB/lib2to3" \
       "$PYLIB/unittest" "$PYLIB/distutils" "$PYLIB/pydoc_data" \
       "$PYLIB/ensurepip" "$PYLIB/turtledemo" "$PYLIB/test" \
       "$SITE/pip" "$SITE"/pip-*.dist-info \
       "$SITE/uvloop" "$SITE"/uvloop-*.dist-info \
       "$SITE/reportlab" "$SITE"/reportlab-*.dist-info \
       "$SITE/PIL" "$SITE/pillow.libs" "$SITE"/pillow-*.dist-info \
       "$SITE/pypdf" "$SITE"/pypdf-*.dist-info \
       "$SITE/setuptools" "$SITE"/setuptools-*.dist-info \
       "$SITE/wheel" "$SITE"/wheel-*.dist-info
# Wipe __pycache__ — xz compresses .py files well; .pyc just doubles bytes
find "$PYLIB" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ----- 6. Desktop entry + icon + AppRun ----------------------------
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
install -m 0644 "$REPO_ROOT/src-tauri/icons/icon.png" \
                "$APPDIR/usr/share/icons/hicolor/256x256/apps/hazreq.png"
install -m 0644 "$REPO_ROOT/src-tauri/icons/icon.png" "$APPDIR/hazreq.png"
cat > "$APPDIR/hazreq.desktop" <<'EOF'
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

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
set -e
HERE="$(readlink -f "$(dirname "$0")")"
export APPDIR="$HERE"
# Void layout: libs at usr/lib, no <arch>-linux-gnu subdir.
export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas"
export FONTCONFIG_PATH="$HERE/etc/fonts"
exec "$HERE/usr/bin/hazreq-shell" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ----- 7. Package: mksquashfs (xz) + AppImage type2 runtime --------
# appimagetool ships an embedded mksquashfs built without xz support;
# bypass it and use the host's mksquashfs + the official aarch64 runtime
# binary, then concatenate. AppImage type 2 = [runtime ELF][squashfs].
RUNTIME="$BUILD/runtime-aarch64"
[ -x "$RUNTIME" ] || {
  echo "==> Downloading AppImage type2 aarch64 runtime"
  curl -fL -o "$RUNTIME" \
    "https://github.com/AppImage/type2-runtime/releases/latest/download/runtime-aarch64"
  chmod +x "$RUNTIME"
}
command -v mksquashfs >/dev/null || { echo "install squashfs-tools" >&2; exit 1; }

echo "==> AppDir size: $(du -sh "$APPDIR" | cut -f1)"
SQ="$BUILD/image.squashfs"
rm -f "$SQ"
mksquashfs "$APPDIR" "$SQ" -root-owned -noappend -comp xz -b 1M -Xdict-size 100%
OUT="$DIST/hazreq-tauri-${ARCH}.AppImage"
cat "$RUNTIME" "$SQ" > "$OUT"
chmod +x "$OUT"
echo
echo "==> Built $(du -h "$OUT" | cut -f1) at: $OUT"
