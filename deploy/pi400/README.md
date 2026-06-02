# hazreq on the Raspberry Pi 400 (native Tk)

The fast, offline, native-Tkinter build of hazreq — no WebKit, no browser,
no uvicorn, no LibreOffice.

## Run it (supported path)

Tk ships with the OS, not pip, so install it once:

```bash
sudo xbps-install -S python3-tkinter     # Void Linux
sudo apt install python3-tk              # Raspberry Pi OS / Debian
sudo dnf install python3-tkinter         # Fedora
```

Then, from a clone of this repo:

```bash
bash deploy/pi400/run.sh
```

The script makes a local `.venv-pi` (inheriting the system Tk via
`--system-site-packages`), installs the five Python deps (SQLAlchemy,
Alembic, docxtpl, pypdf, reportlab), runs the gated DB migration, and opens
the window. Subsequent launches skip straight to the app.

On first launch it creates `~/.local/share/hazreq/` for the SQLite DB,
generated PDFs and backups.

## Why not a prebuilt AppImage?

We tried, and a self-contained AppImage is **not reliable for this app**.

The only interpreter that bundles Tk for an AppImage
(python-build-standalone, PBS) **statically links its own libxcb into Tk**.
hazreq's PDF backend uses reportlab, which hard-requires Pillow, and Pillow
links the **system** libxcb. Two libxcb instances then coexist in one
process, their X11 protocol sequence numbers desync, and the process aborts:

```
[xcb] Unknown sequence number while appending request
[xcb] You called XInitThreads, this is not your fault
xcb_io.c: append_pending_request:
    Assertion `!xcb_xlib_unknown_seq_number' failed.
```

This was reproduced and isolated directly: **system Tk + Pillow works
8/8; PBS Tk + Pillow crashes 10/10**, both with threaded Tcl and a single
*dynamic* libxcb (the conflicting one is static inside libpython). It can't
be fixed from Python — reportlab won't even import without Pillow, and
removing Pillow or repointing sonames doesn't resolve it.

The `run.sh` path above uses your distro's own Tk, which links the system
libxcb like everything else, so the conflict never arises. That's why it's
the supported path.

> `deploy/build_tk_appimage.sh` is kept for reference / future
> experimentation, but the AppImage it produces is subject to the bug above.

## What you get

Full app, all four surfaces: **new request**, **history**, **catalog
management**, and **admin** (backup/restore, CSV + whole-DB import/export,
health, audit log). PDF generation uses the pure-Python `overlay` backend
(~20 ms/render) onto the bundled blank chit — no LibreOffice anywhere.
