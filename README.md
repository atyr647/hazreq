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

## Deployment on a Pi 400

Three paths — pick whichever fits.

### A. Tauri AppImage (recommended: native window, no Chromium)

```bash
./deploy/build_tauri.sh           # run on the target arch (no cross-build)
./dist/hazreq-tauri-aarch64.AppImage
```

A single ~55 MB AppImage that bundles the Rust Tauri shell + Python 3.11 + every Python dep + the app source. The shell opens a native WebKitGTK window (no browser chrome, fast startup) and spawns uvicorn as a child process; closing the window terminates the backend cleanly. Host deps on Void Linux: `sudo xbps-install -S webkit2gtk gtk+3 libreoffice` — `webkit2gtk` is the renderer, `libreoffice` does the docx→PDF conversion.

Build prerequisites on the Pi (one-time): Rust toolchain (`xbps-install -S rust cargo`) and `webkit2gtk-devel gtk+3-devel pkg-config`. Then `cargo install tauri-cli --version "^2.0"` and run `deploy/build_tauri.sh`. Cross-building the Tauri shell from x86_64 isn't supported — the Rust code links against webkit2gtk, which needs an aarch64 sysroot.

### B. Plain Python AppImage (browser-based UI)

```bash
./deploy/build_appimage.sh        # run on the SAME arch you're targeting
./dist/hazreq-aarch64.AppImage    # ships in dist/, ~50 MB
```

The AppImage bundles Python 3.11 + every Python dep + the app source. It does **not** bundle LibreOffice or CUPS — those are expected on the host (`apt install libreoffice-core libreoffice-writer cups-client`). If you switch `HAZREQ_PDF_BACKEND=fillable_pdf` once a fillable form is provided, LibreOffice becomes optional.

On first launch the AppImage:

- creates `~/.local/share/hazreq/` for the SQLite DB, generated PDFs, backups
- runs Alembic migrations
- generates the starter docx template if missing
- starts uvicorn on `127.0.0.1:8000` (auto-picks a free port if taken) and opens the UI

Override anything via env vars (`HAZREQ_PORT`, `HAZREQ_HOST`, `HAZREQ_DATA_DIR`, etc.). If `HAZREQ_PORT` is unset, the launcher probes 8000–8009 for the first free port and falls back to a kernel-assigned one. By default the UI opens in a chromeless Chromium app window (`chromium --app=URL`); set `HAZREQ_APP_MODE=0` to open in a regular browser tab instead, or `HAZREQ_NO_BROWSER=1` to skip the auto-launch entirely. Build for the Pi 400 by running the script *on* the Pi (cross-compiling AppImages is doable but messier — qemu-static + binfmt).

### C. systemd install (always-on background service)

```bash
sudo ./deploy/install.sh
```

Installs LibreOffice (for PDF conversion), creates a `hazreq` user, copies the app to `/opt/hazreq`, sets up `/var/lib/hazreq` for data, registers two systemd services (`hazreq` and `hazreq-unoserver`), advertises the app on mDNS (`hazreq.local:8000`), installs the system menu launcher, and sets up nightly SQLite backups.

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

A future web port can flip `HAZREQ_DB_URL` to `:memory:` and
`HAZREQ_PERSIST_PDFS` to `0` to run with no server-side persistence.

## Tech notes

- Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, Jinja2, docxtpl, unoserver.
- SQLite WAL mode with `PRAGMA foreign_keys=ON` enforced on every connection.
- No external CDN, no analytics, no telemetry, no network calls. Vanilla JS
  for partial updates (no HTMX/Alpine to vendor).
- `request_line` snapshots SPMIG / nomenclature / NIIN at line creation —
  catalog edits never alter past requests.
- The PDF pipeline tries `unoserver` (warm headless LibreOffice over a local
  socket) first, falling back to spawning `soffice --headless` on demand.

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
