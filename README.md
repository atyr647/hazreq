# hazreq

Offline hazmat request generator. Designed to run on a Raspberry Pi 400 as a
multipurpose-desktop app, accessible via the system browser; also reachable
on the LAN over mDNS for phones / other workstations.

See [`ROADMAP.md`](ROADMAP.md) for the build plan and status tracking.

## Quick start (development)

```bash
./scripts/dev.sh
```

That bootstraps a venv, runs migrations, generates the docx template, and
starts uvicorn with auto-reload at <http://localhost:8000>.

## What it does

- Catalog: MIPs → MRCs (always nested), SPMIGs → hazmat items (always nested),
  many-to-many links between MRCs and items with default qty + sort order.
- New request workflow: search by MIP + MRC, click an MRC to bulk-load all of
  its items as snapshots, edit qty / swap to alternates from the same SPMIG,
  add manual lines, finalize → PDF.
- History: every request persists, draft or finalized; filter by date,
  requestor, workcenter, LPO, location, status.
- Admin: download `.zip` of DB + PDFs, restore from a `.db` upload, health
  page showing DB / unoserver / template / disk status.

## Native Tk app (fastest on the Pi 400)

A native Tkinter front-end that calls the service layer in-process — no
WebKitGTK, no uvicorn, no HTTP. It exists because WebKit was the heaviest
thing on the Pi; dropping it (plus the pure-Python `overlay` PDF backend)
is the performance win. Covers the full app: **new request, history,
catalog management, and admin** (backup/restore, catalog CSV + whole-DB
JSON import/export, health, audit log).

```bash
sudo apt install python3-tk          # Raspberry Pi OS; Void: xbps-install -S python3-tkinter
PYTHONPATH=. python scripts/tk_prototype.py
```

On the Pi 400, the supported launcher is **`deploy/pi400/run.sh`** (provisions
a venv against the system Tk, installs deps, migrates, opens the window). See
[`deploy/pi400/README.md`](deploy/pi400/README.md).

> A self-contained AppImage (`deploy/build_tk_appimage.sh`) is **not**
> recommended: the only Tk-bundling interpreter (python-build-standalone)
> statically links its own libxcb, which collides with Pillow's system libxcb
> and aborts on X (`xcb_xlib_unknown_seq_number`). Use the system Tk via
> `run.sh` instead — details in the Pi 400 README.

## Deployment on a Pi 400 (web UI)

Three paths — pick whichever fits.

### A. Tauri AppImage (recommended: native window, no Chromium)

```bash
./deploy/build_tauri.sh           # cross-builds aarch64 from x86_64
./dist/hazreq-tauri-aarch64.AppImage
```

A single ~145 MB AppImage that bundles **everything** needed to run on a Pi 400 / Void Linux: the Rust Tauri shell, Python 3.11 + every Python dep, the app source, and Void's webkit2gtk stack (lib + auxiliary processes + glib/gtk/cairo/pango/etc). The shell opens a native WebKitGTK window (no browser chrome, fast startup) and spawns uvicorn as a child process; closing the window terminates the backend cleanly.

**Zero systemd**: the bundle uses Void's webkit2gtk which is built against `elogind` (a standalone fork of systemd-logind) and `eudev` (Gentoo's standalone udev), not systemd. No `libsystemd.so.0` anywhere in the AppImage.

Host deps on the Pi: **none for PDF** — the default `overlay` backend is pure Python and the blank chit is bundled. (Install `libreoffice` only if you set `HAZREQ_PDF_BACKEND=docx`.) Everything else is bundled.

The build is fully cross-compilable from x86_64 — `deploy/build_tauri.sh` for the basic AppImage (host webkit), or `deploy/build_tauri_void.sh` to roll the fully-bundled variant by walking Void's xbps dep tree and assembling the AppDir manually.

### B. Plain Python AppImage (browser-based UI)

```bash
./deploy/build_appimage.sh        # run on the SAME arch you're targeting
./dist/hazreq-aarch64.AppImage    # ships in dist/, ~50 MB
```

The AppImage bundles Python 3.11 + every Python dep + the app source + the blank chit. PDF generation uses the pure-Python `overlay` backend by default, so **no LibreOffice is needed**. Only CUPS is expected on the host, and only for printing (`apt install cups-client`). Set `HAZREQ_PDF_BACKEND=docx` if you specifically want LibreOffice conversion.

On first launch the AppImage:

- creates `~/.local/share/hazreq/` for the SQLite DB, generated PDFs, backups
- runs Alembic migrations
- (only under the `docx` backend) generates the starter docx template if missing
- starts uvicorn on `127.0.0.1:8000` (auto-picks a free port if taken) and opens the UI

Override anything via env vars (`HAZREQ_PORT`, `HAZREQ_HOST`, `HAZREQ_DATA_DIR`, etc.). If `HAZREQ_PORT` is unset, the launcher probes 8000–8009 for the first free port and falls back to a kernel-assigned one. By default the UI opens in a chromeless Chromium app window (`chromium --app=URL`); set `HAZREQ_APP_MODE=0` to open in a regular browser tab instead, or `HAZREQ_NO_BROWSER=1` to skip the auto-launch entirely. Build for the Pi 400 by running the script *on* the Pi (cross-compiling AppImages is doable but messier — qemu-static + binfmt).

### C. systemd install (always-on background service)

```bash
sudo ./deploy/install.sh
```

Creates a `hazreq` user, copies the app to `/opt/hazreq`, sets up `/var/lib/hazreq` for data, registers the `hazreq` systemd service (PDF via the bundled pure-Python `overlay` backend — no LibreOffice, no `unoserver` service), advertises the app on mDNS (`hazreq.local:8000`), installs the system menu launcher, and sets up nightly SQLite backups.

## Configuration

All paths are env-driven (see `app/config.py`):

| Env var                     | Default (dev)                     | Purpose                       |
| --------------------------- | --------------------------------- | ----------------------------- |
| `HAZREQ_DATA_DIR`           | `./data`                          | Root for db / templates / pdfs |
| `HAZREQ_DB_URL`             | `sqlite:///./data/hazreq.db`      | SQLAlchemy URL                |
| `HAZREQ_TEMPLATE_PATH`      | `./data/templates/hazmat_chit.docx` | docx template                 |
| `HAZREQ_PDF_DIR`            | `./data/pdfs`                     | Generated PDFs                |
| `HAZREQ_BACKUP_DIR`         | `./data/backups`                  | Nightly cron backups          |
| `HAZREQ_UNOSERVER_HOST`     | `127.0.0.1`                       | unoserver host                |
| `HAZREQ_UNOSERVER_PORT`     | `2003`                            | unoserver port                |
| `HAZREQ_PERSIST_PDFS`       | `1`                               | Set to `0` for ephemeral PDFs |
| `HAZREQ_PDF_BACKEND`        | `overlay`                         | `overlay` \| `docx` \| `fillable_pdf` |
| `HAZREQ_OVERLAY_PDF_PATH`   | `./Hazmat Request Blank.pdf`      | Blank chit for the `overlay` backend |
| `HAZREQ_OVERLAY_FONT_PATH`  | `./data/fonts/Carlito-Regular.ttf` | Fill font (Calibri-compatible) for `overlay` |

A future web port can flip `HAZREQ_DB_URL` to `:memory:` and
`HAZREQ_PERSIST_PDFS` to `0` to run with no server-side persistence.

## Tech notes

- Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, Jinja2, docxtpl, unoserver.
- SQLite WAL mode with `PRAGMA foreign_keys=ON` enforced on every connection.
- No external CDN, no analytics, no telemetry, no network calls. Vanilla JS
  for partial updates (no HTMX/Alpine to vendor).
- `request_line` snapshots SPMIG / nomenclature / NIIN at line creation —
  catalog edits never alter past requests.
- Three PDF backends, picked with `HAZREQ_PDF_BACKEND`:
  - `overlay` (**default**, recommended on the Pi) — pure-Python. Draws the
    request's values onto the static blank chit (`Hazmat Request Blank.pdf`)
    with reportlab and merges with pypdf. **No LibreOffice**; a render is
    ~20 ms vs. LibreOffice's multi-second cold start. Coordinates are
    measured once from the blank form in `app/services/pdf.py`; re-measure
    if the master form is re-laid-out. Overflows onto extra copies of the
    line-item page, 7 rows each, with the signature page kept last.
  - `docx` — fill the docxtpl template, then convert to PDF via `unoserver`
    (warm headless LibreOffice over a local socket) first, falling back to
    spawning `soffice --headless` on demand. Requires LibreOffice on the host.
  - `fillable_pdf` — fill an AcroForm PDF directly via pypdf (needs a
    fillable source form named per the convention in `app/services/pdf.py`).

## Replacing the form template

The bundled template in `data/templates/hazmat_chit.docx` is generated by
`scripts/prepare_template.py` from `BLANK NEW HAZMAT ISSUE CHIT 2.0.docx`
at the repo root. The script clones the source form and injects docxtpl
placeholders into the header cells and a row loop into the line-items
table, preserving the original layout, watermark, and notes exactly.

To swap in a new master form, drop the new `.docx` at the same path,
adjust the cell coordinates in `scripts/prepare_template.py` if the
table layout changed, and re-run the script. To override the template
at runtime without regenerating, point `HAZREQ_TEMPLATE_PATH` (or drop
a hand-prepped docxtpl file at `/var/lib/hazreq/templates/hazmat_chit.docx`).

The render service is template-agnostic: any template works as long as
the placeholders (`{{ name }}`, `{{ workcenter }}`, etc.) and the
`{%tr for line in lines %}…{%tr endfor %}` row loop are present.
