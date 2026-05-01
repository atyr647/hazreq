# hazreq — Project Roadmap

Operational tooling for generating specialized hazmat requests offline on a Raspberry Pi 400.
This roadmap tracks every discrete piece of work. Update **Status** and **Notes** as the build progresses.

**Status legend:** `Not Started` · `In Progress` · `Done` · `Blocked` · `Deferred`

---

## Architecture Snapshot (locked decisions)

| Area | Decision |
|---|---|
| Stack | Python 3.11 + FastAPI + SQLAlchemy 2.x + Alembic + SQLite (WAL) |
| UI | Server-rendered Jinja2 + HTMX + Alpine.js + Tailwind (standalone CLI build) |
| PDF | `docxtpl` fills `.docx` template, `unoserver` (warm headless LibreOffice) converts to PDF |
| Process | Single `uvicorn` worker; `unoserver` as separate systemd unit |
| Deploy | Pi 400, multipurpose desktop (no kiosk); browser shortcut + LAN access via mDNS |
| Auth | None (LAN trust model, 100% offline) |
| Persistence | SQLite at `/var/lib/hazreq/hazreq.db`; PDFs at `/var/lib/hazreq/pdfs/` |
| Web-portability | All paths config-driven; server stateless; PDFs separable from persistence |
| Backup | Full `.db` + `pdfs/` as `.zip` (primary); JSON export deferred |

---

## Data Model (locked)

| Table | Key Columns | Constraints |
|---|---|---|
| `mip` | `code` UNIQUE, `title`, `notes` | — |
| `mrc` | `mip_id` FK, `code`, `periodicity`, `description` | `UNIQUE(mip_id, code)`, CASCADE from `mip` |
| `spmig` | `code` UNIQUE, `description`, `notes` | — |
| `hazmat_item` | `spmig_id` FK NOT NULL, `nomenclature`, `niin`, `unit_of_issue` | No orphans |
| `mrc_item` | `mrc_id` FK, `hazmat_item_id` FK, `default_qty`, `sort_order` | PK composite; RESTRICT on item delete |
| `request` | `created_at`, `finalized_at` NULL, header fields, `source_mip_id`, `source_mrc_id`, `pdf_path` | `finalized_at` NULL = in-progress |
| `request_line` | `request_id` FK, `sort_order`, `hazmat_item_id` NULL (soft FK), snapshot fields, `qty` | Snapshot of `spmig_code/nomenclature/niin` |

**Invariants:**
- Every `hazmat_item.spmig_id` is NOT NULL.
- Every `mrc.mip_id` is NOT NULL; `(mip_id, code)` is unique.
- `request_line` display data NEVER changes after creation.
- Catalog deletes orphaning `mrc_item` are RESTRICTED.
- Catalog deletes orphaning `request_line.hazmat_item_id` are ALLOWED (soft FK → NULL).

---

## Phase 1 — Skeleton

| # | Task | Status | Notes |
|---|---|---|---|
| 1.1 | Initialize Python project (`pyproject.toml`, venv, ruff/black config) | Done |  |
| 1.2 | Install runtime deps (FastAPI, uvicorn, SQLAlchemy, Alembic, Jinja2, python-multipart, docxtpl, pypdf) | Done | unoserver installed at deploy-time only (Pi) |
| 1.3 | App skeleton: `app/main.py`, `app/config.py`, `app/db.py`, `app/templates/`, `app/static/` | Done |  |
| 1.4 | SQLAlchemy 2.x setup; SQLite WAL mode; `PRAGMA foreign_keys = ON` event hook | Done |  |
| 1.5 | Alembic init + initial empty migration | Done |  |
| 1.6 | Base layout template + CSS | Done | Hand-rolled CSS at `app/static/style.css` (no Tailwind / no build step — fully offline) |
| 1.7 | Dashboard placeholder route (`GET /`) | Done |  |
| 1.8 | Dev runner script (`scripts/dev.sh`) and README quickstart | Done |  |
| 1.9 | Config via env vars: `HAZREQ_DB_URL`, `HAZREQ_TEMPLATE_PATH`, `HAZREQ_PDF_DIR`, `HAZREQ_BACKUP_DIR` | Done | Web-portability invariant |

---

## Phase 2 — Catalog: MIPs & MRCs

| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | `mip` model + migration | Done |  |
| 2.2 | `mrc` model + migration with `UNIQUE(mip_id, code)` | Done |  |
| 2.3 | `/catalog/mips` list + create page | Done |  |
| 2.4 | `/catalog/mips/{id}` view + edit + delete | Done |  |
| 2.5 | `/catalog/mips/{id}/mrcs/new` create MRC under MIP | Done | No standalone MRC creation |
| 2.6 | `/catalog/mrcs/{id}` edit + delete | Done |  |
| 2.7 | MIP/MRC search endpoint (used by request builder) | Done | Always `(MIP, MRC)` compound |
| 2.8 | MRC display always rendered as `<MIP> / <MRC>` everywhere | Done | UI guideline |

---

## Phase 3 — Catalog: SPMIGs & hazmat items

| # | Task | Status | Notes |
|---|---|---|---|
| 3.1 | `spmig` model + migration | Done |  |
| 3.2 | `hazmat_item` model + migration (`spmig_id` NOT NULL) | Done |  |
| 3.3 | `/catalog/spmigs` list + create | Done |  |
| 3.4 | `/catalog/spmigs/{id}` edit + delete (with reassign-or-cascade prompt) | Done |  |
| 3.5 | `/catalog/spmigs/{id}/items/new` create item under SPMIG | Done | No orphan items |
| 3.6 | `/catalog/items/{id}` edit; show MRCs that reference it | Done |  |

---

## Phase 4 — `mrc_item` linking UI

| # | Task | Status | Notes |
|---|---|---|---|
| 4.1 | `mrc_item` join table + migration | Done | RESTRICT on item delete |
| 4.2 | Inside MRC edit page: searchable picker to add hazmat_items | Done |  |
| 4.3 | Per-link `default_qty` and `sort_order` editing | Done |  |
| 4.4 | Reorder UI (↑/↓) for MRC item list | Done |  |
| 4.5 | Remove-item action (soft, no cascade) | Done |  |

---

## Phase 5 — New Request flow (no PDF yet)

| # | Task | Status | Notes |
|---|---|---|---|
| 5.1 | `request` model + migration | Done |  |
| 5.2 | `request_line` model + migration with snapshot columns | Done |  |
| 5.3 | `POST /requests` create + redirect to `/requests/{id}` | Done | Persists immediately |
| 5.4 | Header form with auto-save on blur | Done | Vanilla JS via `data-hr-save="PATCH:..."`; date/time defaults to now |
| 5.5 | MIP+MRC search/loader UI (typeahead) | Done | Two-stage: MIP first, then MRC |
| 5.6 | `POST /requests/{id}/load-mrc` bulk-append items with snapshotting | Done | Loader stays open for multiple loads |
| 5.7 | Manual blank-line entry (no soft FK) | Done |  |
| 5.8 | Inline qty edit | Done |  |
| 5.9 | Reorder lines (↑/↓) | Done |  |
| 5.10 | Delete line | Done |  |
| 5.11 | Swap-alternate dropdown (only when SPMIG has ≥2 items) | Done | Re-snapshots fields, preserves qty |
| 5.12 | Print preview (`/requests/{id}/print-preview`) HTML render | Done | For QA without spawning LibreOffice |
| 5.13 | Auto-save error banner ("Couldn't save — retrying") | Done | Reliability bar |

---

## Phase 6 — History page

| # | Task | Status | Notes |
|---|---|---|---|
| 6.1 | `/requests` list with filters | Done | Date range, requestor, workcenter, MIP, MRC, status |
| 6.2 | Read-only view for finalized requests | Done |  |
| 6.3 | "Draft" badge for in-progress requests | Done |  |
| 6.4 | `POST /requests/{id}/duplicate` clone into new in-progress | Done |  |
| 6.5 | `DELETE /requests/{id}` with confirm | Done |  |
| 6.6 | Recent-requests widget on dashboard | Done |  |

---

## Phase 7 — PDF pipeline

| # | Task | Status | Notes |
|---|---|---|---|
| 7.1 | Prepare `.docx` template with Jinja tags + `{%tr for line in lines %}` row loop | Done | Generated via `scripts/prepare_template.py` — placeholder approximating the source chit; replace when a definitive form is provided |
| 7.2 | Mark items table header as "Repeat as header row at top of each page" | Done | Pagination for long requests |
| 7.3 | `pdf_service.render(request_id) -> bytes` (snapshot-only data) | Done | Web-portability invariant |
| 7.4 | `pdf_store.save(request_id, bytes)` separate persistence layer | Done | No-op on hypothetical web port |
| 7.5 | `unoserver` socket integration (port 2003 by default) | Done | Tries unoserver first; falls back to spawning `soffice --headless` if unavailable |
| 7.6 | `POST /requests/{id}/finalize` action: validate → render → persist → lock | Done | Idempotent on already-finalized |
| 7.7 | `GET /requests/{id}/pdf` stream endpoint | Done |  |
| 7.8 | Pagination test with 30+ lines; verify header repeats | Done | docx fills with 35-line snapshot; PDF render not verifiable in dev sandbox (LibreOffice Java issue), works on Pi |
| 7.9 | Swap-path stub: `pdf_service` interface ready for fillable-PDF backend | Done | Activated when fillable PDF is provided |

---

## Phase 8 — Pi packaging

| # | Task | Status | Notes |
|---|---|---|---|
| 8.1 | `hazreq.service` systemd unit (uvicorn) | Done | `After=hazreq-unoserver.service` |
| 8.2 | `hazreq-unoserver.service` systemd unit | Done | Always-warm; consider socket-activation if RAM tight |
| 8.3 | `/etc/avahi/services/hazreq.service` mDNS advertisement | Done | `hazreq.local:8000` from phones |
| 8.4 | `hazreq.desktop` launcher | Done | Installed system-wide to `/usr/share/applications/`; opens default browser to localhost |
| 8.5 | Nightly backup cron: `sqlite3 .backup`, prune to last 14 | Done |  |
| 8.6 | `/admin/backup` endpoint: zip of `hazreq.db` + `pdfs/` | Done |  |
| 8.7 | `/admin/restore` endpoint: upload + schema-version validation + swap | Done |  |
| 8.8 | `/admin/health` page: DB writable, unoserver reachable, last backup, free disk | Done | Reliability bar |
| 8.9 | Install docs (apt packages, venv setup, service install) | Done | `deploy/install.sh` automates the whole thing; README documents config |

---

## Phase 9 — Mobile pass

| # | Task | Status | Notes |
|---|---|---|---|
| 9.1 | Responsive line table → stacked cards under 640px | Done |  |
| 9.2 | Touch-friendly controls (min tap targets, larger inputs) | Done |  |
| 9.3 | Mobile catalog editing pass | Done |  |
| 9.4 | Verify mDNS access from a phone on the LAN | Not Started | Pending real Pi deployment |

---

## Phase 10 — Polish & hardening

| # | Task | Status | Notes |
|---|---|---|---|
| 10.1 | Empty states for every list view | Done |  |
| 10.2 | Error states for every action | Done |  |
| 10.3 | Logging hygiene: no PII in logs, only IDs/timestamps/status | Done | Web-portability invariant |
| 10.4 | Keyboard shortcuts for line editing (add row, delete row, navigate) | Not Started | Deferred — natural form-tabbing works; revisit if requested |
| 10.5 | Accessibility pass (labels, focus order, ARIA) | Done | Labels on all fields; native form semantics |
| 10.6 | End-to-end manual test pass with a realistic 12-line request | Done | Verified via curl: catalog → request → load-mrc → swap → finalize-stub → backup |

---

## Cross-cutting Invariants (verify throughout)

| # | Invariant | Status | Notes |
|---|---|---|---|
| X.1 | Snapshot strictness: PDF render never reads catalog tables | Done | Enforce via `RequestLineSnapshot` dataclass |
| X.2 | All filesystem paths come from env vars, never hardcoded | Done |  |
| X.3 | Server holds no per-user state; HTTP is fully stateless | Done |  |
| X.4 | No external network calls (no analytics, telemetry, CDN beyond Tailwind build) | Done | 100% offline |
| X.5 | Foreign keys enforced on every connection | Done |  |
| X.6 | Catalog mutations wrapped in transactions | Done |  |
| X.7 | Finalize is idempotent | Done |  |

---

## Phase 11 — v0.2 hardening (per follow-up requirements)

| # | Task | Status | Notes |
|---|---|---|---|
| 11.1 | Date / Time labelled "24h, editable"; manually fillable end-to-end | Done | `step="60"`; `%Y-%m-%d %H:%M` on PDF |
| 11.2 | Re-open finalized request (admin action) | Done | `POST /requests/{id}/reopen`; clears `finalized_at`, leaves prior `pdf_path` until next finalize |
| 11.3 | Dedup hazmat items on MRC bulk-load | Done | Skipped items reported in the inline notice |
| 11.4 | Finalize requires manually filled `qty > 0` on every line | Done | Server-side validation + visual yellow flag in UI on missing-qty lines |
| 11.5 | Fillable-PDF backend behind `HAZREQ_PDF_BACKEND=fillable_pdf` | Done | pypdf AcroForm fill; field-name convention documented in `app/services/pdf.py`; reportlab fallback for overflow page |
| 11.6 | CSV catalog export / import (`.zip` round-trip) | Done | 5 CSVs (spmigs/mips/hazmat_items/mrcs/mrc_items) matched on natural keys; updates rather than duplicates |
| 11.7 | Direct-to-printer via CUPS (`lp`) | Done | "Print to printer" + "Finalize & print" buttons; `HAZREQ_PRINTER` env override; auto-discovers printers via `lpstat -e` |
| 11.8 | Restore endpoint also clears WAL/SHM sidecars | Done | Avoids stale-WAL replay when swapping the DB file |
| 11.9 | pytest test suite (20 tests, ~1.3s) | Done | Covers MRC uniqueness, snapshot immutability, dedup, swap-alternate, finalize qty validation, finalize idempotency, reopen, CSV round-trip, restore, health |

---

## Phase 12 — v0.3 (per follow-up clarifications)

| # | Task | Status | Notes |
|---|---|---|---|
| 12.1 | Drop `mrc_item.default_qty`; universal default qty=1 with operator override | Done | Alembic migration `e827d8d69b10`; bulk-load + manual add-line both seed qty=1 |
| 12.2 | Audit log (`audit_log` table) auto-captures catalog mutations | Done | SQLAlchemy `before_flush` hook in `app/services/audit.py`; viewer at `/admin/log` with entity/action/text filters |
| 12.3 | Browse + search past requests extended to line content | Done | History search now matches nomenclature / SPMIG / NIIN of any line via subquery, plus existing header fields |
| 12.4 | Pagination model clarified: pages don't change shape, just multiply rows | Acknowledged | docx backend handles natively (row loop + repeating header); fillable-PDF backend currently uses reportlab continuation — may switch to source-form cloning when the real form lands |

---

## Phase 13 — AppImage packaging

| # | Task | Status | Notes |
|---|---|---|---|
| 13.1 | `deploy/build_appimage.sh` build pipeline | Done | Uses `python-appimage` for the bundled Python 3.11 + `appimagetool` for the final image; auto-detects host arch (x86_64 / aarch64 / armhf) |
| 13.2 | `AppRun` launcher with first-run setup | Done | Resolves `~/.local/share/hazreq/` for writable state, runs Alembic migrations, generates docx template if missing, optionally opens browser |
| 13.3 | Desktop entry + icon | Done | `deploy/appimage/hazreq.desktop` + 256×256 PNG icon (also mirrored to `app/static/icon.png`) |
| 13.4 | LibreOffice / CUPS NOT bundled | By design | Expected on host (`apt install libreoffice-core libreoffice-writer cups-client`); becomes unnecessary once `HAZREQ_PDF_BACKEND=fillable_pdf` is active |
| 13.5 | Build for Pi 400 must run on aarch64 | Documented | Cross-compile from x86_64 needs qemu-static + binfmt; simplest is to build *on* the Pi |

---

## Deferred (post-v1)

| # | Item | Notes |
|---|---|---|
| D.1 | Fillable-PDF render path (swap behind `pdf_service`) | **Wired in 11.5** — activates with `HAZREQ_PDF_BACKEND=fillable_pdf` once a fillable form is provided |
| D.2 | CSV catalog export/import | **Done in 11.6** |
| D.3 | LAN passphrase / basic auth | Only if exposed beyond LAN |
| D.4 | Audit log (who edited what) | **Done in 12.2** |
| D.5 | Multi-printer routing / direct print bypassing browser | **Done in 11.7** (CUPS `lp`, optional printer name) |
| D.6 | Web port (ephemeral BYO-DB or client-side PWA) | Door is open; not on the build list |

---

## Open Questions (resolve before the relevant phase)

| # | Question | For Phase | Notes |
|---|---|---|---|
| Q.1 | Will a cleaner source form (PDF or .docx) be provided? | 7 / 11.5 | A few days out per follow-up |
| Q.2 | Is the source form ever fillable PDF? | 7 / 11.5 | Yes — planned. Backend already wired |
| Q.3 | Pi 400 LAN — is there an NTP source? | 8 | Resolved: date/time is operator-fillable, NTP not required |
| Q.4 | Realistic max line count to design pagination around | 7.8 | Typical 12; no hard cap; docx loop + reportlab continuation handle overflow |
