# hazreq on the Raspberry Pi 400 (native Tk)

The fast, offline, native-Tkinter build of hazreq — no WebKit, no browser,
no uvicorn, no LibreOffice. Two ways to get it running on the Pi.

## Option A — prebuilt AppImage (double-click, nothing to install)

A self-contained `aarch64` AppImage lives in the repo at:

```
dist/hazreq-tk-aarch64.AppImage
```

It bundles its own Python 3.11 + Tcl/Tk + every dependency + the blank
chit. Nothing on the Pi is required except FUSE (preinstalled on Raspberry
Pi OS).

```bash
chmod +x dist/hazreq-tk-aarch64.AppImage
./dist/hazreq-tk-aarch64.AppImage
```

On first launch it creates `~/.local/share/hazreq/` for the SQLite DB,
generated PDFs and backups, runs migrations, and opens the window.

> Built by cross-compiling from x86_64. Every bundled binary was verified
> to be `ARM aarch64`, but it has not been launched on a physical Pi from
> this build host (no emulator was available). If it doesn't start, use
> Option B — it's guaranteed to work and produces an identical app.

If the AppImage won't mount (some minimal setups lack FUSE), either install
it (`sudo apt install libfuse2`) or extract and run:

```bash
./dist/hazreq-tk-aarch64.AppImage --appimage-extract
./squashfs-root/AppRun
```

## Option B — run from source (works on any arch, guaranteed)

Tk ships with the OS, not pip, so install it once:

```bash
sudo apt install python3-tk          # Raspberry Pi OS / Debian
```

Then:

```bash
bash deploy/pi400/run.sh
```

The script makes a local `.venv-pi`, installs the five Python deps
(SQLAlchemy, Alembic, docxtpl, pypdf, reportlab), runs the gated DB
migration, and opens the app. Subsequent launches skip straight to the
window.

## Rebuilding the AppImage yourself

On the Pi (native), or cross from an x86_64 box:

```bash
# native on the Pi:
bash deploy/build_tk_appimage.sh

# cross from x86_64:
HAZREQ_BUILD_ARCH=aarch64 bash deploy/build_tk_appimage.sh
```

Output lands in `dist/hazreq-tk-<arch>.AppImage`.

## What you get

Full app, all four surfaces: **new request**, **history**, **catalog
management**, and **admin** (backup/restore, CSV + whole-DB import/export,
health, audit log). PDF generation uses the pure-Python `overlay` backend
(~20 ms/render) onto the bundled blank chit — no LibreOffice anywhere.
