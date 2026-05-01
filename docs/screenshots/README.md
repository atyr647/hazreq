# hazreq screenshots

Captured against the `dist/hazreq-x86_64.AppImage` build on this branch with a
small seeded catalog: one MIP (`MIP-5821/045-14`, "Hydraulic Power Plant —
Quarterly PMS"), one MRC under it (`Q-1`, quarterly), two SPMIGs
(`SPMIG-7510` adhesives/cleaners, `SPMIG-9150` lubricants) totalling 10 hazmat
items, and two example requests:

- **#1** — finalized: requestor ET2 Alvarez, J. (workcenter EE03, LPO Chen,
  locker B-204). Built by loading MRC `Q-1` to bulk-add 7 lines, then
  appending one manual line ("Shop Towel, Lint-Free, Pack of 200"). Two
  quantities tweaked off the qty=1 default, then finalized → PDF rendered
  via LibreOffice.
- **#2** — draft: requestor EM3 Ortega, R. (workcenter MA02, locker A-118).
  Same MRC loaded, still editable.

| # | Page | URL | Screenshot |
|---|---|---|---|
| 01 | Dashboard                             | `/`                        | [01-dashboard.png](01-dashboard.png) |
| 02 | Request history (filterable)          | `/requests/`               | [02-request-history.png](02-request-history.png) |
| 03 | Finalized request — read-only view    | `/requests/1`              | [03-finalized-request.png](03-finalized-request.png) |
| 04 | Print preview                         | `/requests/1/print-preview`| [04-print-preview.png](04-print-preview.png) |
| 05 | Draft request — editable line items   | `/requests/2`              | [05-draft-request.png](05-draft-request.png) |
| 06 | Catalog: MIPs index                   | `/catalog/mips`            | [06-catalog-mips.png](06-catalog-mips.png) |
| 07 | Catalog: MIP detail (with MRCs)       | `/catalog/mips/1`          | [07-catalog-mip-detail.png](07-catalog-mip-detail.png) |
| 08 | Catalog: MRC detail (item bundle)     | `/catalog/mrcs/1`          | [08-catalog-mrc-detail.png](08-catalog-mrc-detail.png) |
| 09 | Catalog: SPMIGs index                 | `/catalog/spmigs`          | [09-catalog-spmigs.png](09-catalog-spmigs.png) |
| 10 | Catalog: SPMIG detail (items)         | `/catalog/spmigs/1`        | [10-catalog-spmig-detail.png](10-catalog-spmig-detail.png) |
| 11 | Catalog: hazmat item detail           | `/catalog/items/1`         | [11-catalog-item-detail.png](11-catalog-item-detail.png) |
| 12 | Admin home (health, backup, restore)  | `/admin/`                  | [12-admin-home.png](12-admin-home.png) |
| 13 | Admin: health JSON                    | `/admin/health`            | [13-admin-health.png](13-admin-health.png) |
| 14 | Admin: catalog audit log              | `/admin/log`               | [14-admin-log.png](14-admin-log.png) |
| 15 | Generated PDF for request #1          | `/requests/1/pdf`          | [15-generated-pdf.png](15-generated-pdf.png) — original [PDF](15-generated-pdf.pdf) |

Captured at 1280×900 viewport, 2× device-scale, full-page, headless Chromium
via Playwright. The "Printing (CUPS) — OFFLINE" badge on the admin health
page just reflects this build host having no `lp` binary; on a real Pi 400
with `cups-client` installed it goes green.
