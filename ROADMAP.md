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
| 1.1 | Initialize Python project (`pyproject.toml`, venv, ruff/black config) | Not Started |  |
| 1.2 | Install runtime deps (FastAPI, uvicorn, SQLAlchemy, Alembic, Jinja2, python-multipart, docxtpl, unoserver, pypdf) | Not Started |  |
| 1.3 | App skeleton: `app/main.py`, `app/config.py`, `app/db.py`, `app/templates/`, `app/static/` | Not Started |  |
| 1.4 | SQLAlchemy 2.x setup; SQLite WAL mode; `PRAGMA foreign_keys = ON` event hook | Not Started |  |
| 1.5 | Alembic init + initial empty migration | Not Started |  |
| 1.6 | Base layout template + Tailwind standalone CLI build pipeline | Not Started |  |
| 1.7 | Dashboard placeholder route (`GET /`) | Not Started |  |
| 1.8 | Dev runner script (`scripts/dev.sh`) and README quickstart | Not Started |  |
| 1.9 | Config via env vars: `HAZREQ_DB_URL`, `HAZREQ_TEMPLATE_PATH`, `HAZREQ_PDF_DIR`, `HAZREQ_BACKUP_DIR` | Not Started | Web-portability invariant |

---

## Phase 2 — Catalog: MIPs & MRCs

| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | `mip` model + migration | Not Started |  |
| 2.2 | `mrc` model + migration with `UNIQUE(mip_id, code)` | Not Started |  |
| 2.3 | `/catalog/mips` list + create page | Not Started |  |
| 2.4 | `/catalog/mips/{id}` view + edit + delete | Not Started |  |
| 2.5 | `/catalog/mips/{id}/mrcs/new` create MRC under MIP | Not Started | No standalone MRC creation |
| 2.6 | `/catalog/mrcs/{id}` edit + delete | Not Started |  |
| 2.7 | MIP/MRC search endpoint (used by request builder) | Not Started | Always `(MIP, MRC)` compound |
| 2.8 | MRC display always rendered as `<MIP> / <MRC>` everywhere | Not Started | UI guideline |

---

## Phase 3 — Catalog: SPMIGs & hazmat items

| # | Task | Status | Notes |
|---|---|---|---|
| 3.1 | `spmig` model + migration | Not Started |  |
| 3.2 | `hazmat_item` model + migration (`spmig_id` NOT NULL) | Not Started |  |
| 3.3 | `/catalog/spmigs` list + create | Not Started |  |
| 3.4 | `/catalog/spmigs/{id}` edit + delete (with reassign-or-cascade prompt) | Not Started |  |
| 3.5 | `/catalog/spmigs/{id}/items/new` create item under SPMIG | Not Started | No orphan items |
| 3.6 | `/catalog/items/{id}` edit; show MRCs that reference it | Not Started |  |

---

## Phase 4 — `mrc_item` linking UI

| # | Task | Status | Notes |
|---|---|---|---|
| 4.1 | `mrc_item` join table + migration | Not Started | RESTRICT on item delete |
| 4.2 | Inside MRC edit page: searchable picker to add hazmat_items | Not Started |  |
| 4.3 | Per-link `default_qty` and `sort_order` editing | Not Started |  |
| 4.4 | Reorder UI (↑/↓) for MRC item list | Not Started |  |
| 4.5 | Remove-item action (soft, no cascade) | Not Started |  |

---

## Phase 5 — New Request flow (no PDF yet)

| # | Task | Status | Notes |
|---|---|---|---|
| 5.1 | `request` model + migration | Not Started |  |
| 5.2 | `request_line` model + migration with snapshot columns | Not Started |  |
| 5.3 | `POST /requests` create + redirect to `/requests/{id}` | Not Started | Persists immediately |
| 5.4 | Header form with HTMX auto-save on blur | Not Started | Date/time defaults to now |
| 5.5 | MIP+MRC search/loader UI (typeahead) | Not Started | Two-stage: MIP first, then MRC |
| 5.6 | `POST /requests/{id}/load-mrc` bulk-append items with snapshotting | Not Started | Loader stays open for multiple loads |
| 5.7 | Manual blank-line entry (no soft FK) | Not Started |  |
| 5.8 | Inline qty edit | Not Started |  |
| 5.9 | Reorder lines (↑/↓) | Not Started |  |
| 5.10 | Delete line | Not Started |  |
| 5.11 | Swap-alternate dropdown (only when SPMIG has ≥2 items) | Not Started | Re-snapshots fields, preserves qty |
| 5.12 | Print preview (`/requests/{id}/print-preview`) HTML render | Not Started | For QA without spawning LibreOffice |
| 5.13 | Auto-save error banner ("Couldn't save — retrying") | Not Started | Reliability bar |

---

## Phase 6 — History page

| # | Task | Status | Notes |
|---|---|---|---|
| 6.1 | `/requests` list with filters | Not Started | Date range, requestor, workcenter, MIP, MRC, status |
| 6.2 | Read-only view for finalized requests | Not Started |  |
| 6.3 | "Draft" badge for in-progress requests | Not Started |  |
| 6.4 | `POST /requests/{id}/duplicate` clone into new in-progress | Not Started |  |
| 6.5 | `DELETE /requests/{id}` with confirm | Not Started |  |
| 6.6 | Recent-requests widget on dashboard | Not Started |  |

---

## Phase 7 — PDF pipeline

| # | Task | Status | Notes |
|---|---|---|---|
| 7.1 | Prepare `.docx` template with Jinja tags + `{%tr for line in lines %}` row loop | Not Started | Source form may be replaced with a cleaner one later |
| 7.2 | Mark items table header as "Repeat as header row at top of each page" | Not Started | Pagination for long requests |
| 7.3 | `pdf_service.render(request_id) -> bytes` (snapshot-only data) | Not Started | Web-portability invariant |
| 7.4 | `pdf_store.save(request_id, bytes)` separate persistence layer | Not Started | No-op on hypothetical web port |
| 7.5 | `unoserver` socket integration (port 2003 by default) | Not Started |  |
| 7.6 | `POST /requests/{id}/finalize` action: validate → render → persist → lock | Not Started | Idempotent on already-finalized |
| 7.7 | `GET /requests/{id}/pdf` stream endpoint | Not Started |  |
| 7.8 | Pagination test with 30+ lines; verify header repeats | Not Started |  |
| 7.9 | Swap-path stub: `pdf_service` interface ready for fillable-PDF backend | Not Started | Activated when fillable PDF is provided |

---

## Phase 8 — Pi packaging

| # | Task | Status | Notes |
|---|---|---|---|
| 8.1 | `hazreq.service` systemd unit (uvicorn) | Not Started | `After=hazreq-unoserver.service` |
| 8.2 | `hazreq-unoserver.service` systemd unit | Not Started | Always-warm; consider socket-activation if RAM tight |
| 8.3 | `/etc/avahi/services/hazreq.service` mDNS advertisement | Not Started | `hazreq.local:8000` from phones |
| 8.4 | `~/Desktop/hazreq.desktop` launcher | Not Started | Opens default browser to localhost |
| 8.5 | Nightly backup cron: `sqlite3 .backup`, prune to last 14 | Not Started |  |
| 8.6 | `/admin/backup` endpoint: zip of `hazreq.db` + `pdfs/` | Not Started |  |
| 8.7 | `/admin/restore` endpoint: upload + schema-version validation + swap | Not Started |  |
| 8.8 | `/admin/health` page: DB writable, unoserver reachable, last backup, free disk | Not Started | Reliability bar |
| 8.9 | Install docs (apt packages, venv setup, service install) | Not Started |  |

---

## Phase 9 — Mobile pass

| # | Task | Status | Notes |
|---|---|---|---|
| 9.1 | Responsive line table → stacked cards under 640px | Not Started |  |
| 9.2 | Touch-friendly controls (min tap targets, larger inputs) | Not Started |  |
| 9.3 | Mobile catalog editing pass | Not Started |  |
| 9.4 | Verify mDNS access from a phone on the LAN | Not Started |  |

---

## Phase 10 — Polish & hardening

| # | Task | Status | Notes |
|---|---|---|---|
| 10.1 | Empty states for every list view | Not Started |  |
| 10.2 | Error states for every action | Not Started |  |
| 10.3 | Logging hygiene: no PII in logs, only IDs/timestamps/status | Not Started | Web-portability invariant |
| 10.4 | Keyboard shortcuts for line editing (add row, delete row, navigate) | Not Started |  |
| 10.5 | Accessibility pass (labels, focus order, ARIA) | Not Started |  |
| 10.6 | End-to-end manual test pass with a realistic 12-line request | Not Started |  |

---

## Cross-cutting Invariants (verify throughout)

| # | Invariant | Status | Notes |
|---|---|---|---|
| X.1 | Snapshot strictness: PDF render never reads catalog tables | Not Started | Enforce via `RequestLineSnapshot` dataclass |
| X.2 | All filesystem paths come from env vars, never hardcoded | Not Started |  |
| X.3 | Server holds no per-user state; HTTP is fully stateless | Not Started |  |
| X.4 | No external network calls (no analytics, telemetry, CDN beyond Tailwind build) | Not Started | 100% offline |
| X.5 | Foreign keys enforced on every connection | Not Started |  |
| X.6 | Catalog mutations wrapped in transactions | Not Started |  |
| X.7 | Finalize is idempotent | Not Started |  |

---

## Deferred (post-v1)

| # | Item | Notes |
|---|---|---|
| D.1 | Fillable-PDF render path (swap behind `pdf_service`) | When a clean fillable PDF source is provided |
| D.2 | JSON catalog export/import | `.db` round-trip is primary |
| D.3 | LAN passphrase / basic auth | Only if exposed beyond LAN |
| D.4 | Audit log (who edited what) | Not requested; easy to add |
| D.5 | Multi-printer routing / direct print bypassing browser | If browser print proves clunky |
| D.6 | Web port (ephemeral BYO-DB or client-side PWA) | Door is open; not on the build list |

---

## Open Questions (resolve before the relevant phase)

| # | Question | For Phase | Notes |
|---|---|---|---|
| Q.1 | Will a cleaner source form (PDF or .docx) be provided? | 7 | Affects template prep effort |
| Q.2 | Is the source form ever fillable PDF? | 7 / D.1 | Activates simpler render path |
| Q.3 | Pi 400 LAN — is there an NTP source? | 8 | If not, install chrony or accept operator-set time |
| Q.4 | Realistic max line count to design pagination around (worst case) | 7.8 | Plan default: test with 30+ |
