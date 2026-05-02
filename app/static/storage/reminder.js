// Backup-reminder banner + beforeunload prompt.
//
// Tracks two counters in localStorage:
//   - hazreq:lastExportHash — hash of the snapshot at last download
//   - hazreq:lastExportAt   — timestamp of last download
// On every successful mirror write, compute a hash of the snapshot
// and compare. If they don't match, the session is "dirty" and we
// nudge the user to export.

const HASH_KEY = 'hazreq:lastExportHash';
const TS_KEY = 'hazreq:lastExportAt';

function fnv1a(str) {
  // Tiny, deterministic, and good enough to detect snapshot changes.
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

function snapshotHash(snapshot) {
  if (!snapshot) return '0';
  const copy = { ...snapshot };
  delete copy.exportedAt;  // changes every call; not meaningful
  return fnv1a(JSON.stringify(copy));
}

function isDirty(snapshot) {
  if (!snapshot) return false;
  const last = localStorage.getItem(HASH_KEY);
  if (!last) {
    // No prior export. Only consider dirty if there's actually data.
    const KEYS = ['mips', 'spmigs', 'hazmat_items', 'mrcs', 'mrc_items',
                  'requests', 'request_lines', 'audit_log'];
    return KEYS.some((k) => Array.isArray(snapshot[k]) && snapshot[k].length > 0);
  }
  return snapshotHash(snapshot) !== last;
}

let banner = null;
function showBanner(snapshot) {
  if (banner && banner.isConnected) {
    banner.dataset.snapshotHash = snapshotHash(snapshot);
    return;
  }
  banner = document.createElement('div');
  banner.id = 'hazreq-reminder';
  banner.dataset.snapshotHash = snapshotHash(snapshot);
  banner.style.cssText = [
    'position:fixed', 'right:1rem', 'bottom:1rem',
    'z-index:9998', 'padding:0.6rem 0.85rem',
    'background:#fff7e0', 'border:1px solid #d4a000',
    'border-radius:6px',
    'font-size:0.85rem', 'max-width:22rem',
    'box-shadow:0 2px 8px rgba(0,0,0,.08)',
  ].join(';');
  banner.innerHTML = `
    <div style="margin-bottom:.35rem;font-weight:600">Unsaved changes</div>
    <div style="margin-bottom:.5rem">You've changed data since your last backup. Export before closing the tab.</div>
    <a href="/admin/export.json" class="btn primary" style="padding:.3rem .7rem;font-size:.85rem">Download backup</a>
    <button type="button" style="background:none;border:none;color:#666;cursor:pointer;margin-left:.5rem;font-size:.85rem" data-dismiss>Hide</button>
  `;
  banner.querySelector('[data-dismiss]').addEventListener('click', () => banner.remove());
  document.body.appendChild(banner);
}

function clearBanner() {
  if (banner && banner.isConnected) banner.remove();
  banner = null;
}

let currentSnapshot = null;

export function init() {
  window.addEventListener('hazreq:mirrored', (ev) => {
    currentSnapshot = ev.detail;
    if (isDirty(currentSnapshot)) showBanner(currentSnapshot);
    else clearBanner();
  });

  // Hook the existing /admin/export.json link clicks: when a user
  // downloads, mark the snapshot as exported.
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[href$="/admin/export.json"]');
    if (!a) return;
    if (currentSnapshot) {
      localStorage.setItem(HASH_KEY, snapshotHash(currentSnapshot));
      localStorage.setItem(TS_KEY, String(Date.now()));
      // Dismiss the banner shortly after the download fires.
      setTimeout(clearBanner, 250);
    }
  });

  window.addEventListener('beforeunload', (ev) => {
    if (isDirty(currentSnapshot)) {
      ev.preventDefault();
      ev.returnValue = '';
      return '';
    }
  });
}
