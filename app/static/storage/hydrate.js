// First-load recovery prompt.
//
// If the server's session DB is empty (counts = 0 across the board)
// AND the browser has a saved snapshot from a previous session,
// surface a non-modal prompt asking the user to restore. Per
// ShoreCalc's rule: never auto-restore. Stale mirrors can clobber
// legitimate deletions; the user has to confirm.

import { readMirror } from './mirror.js';

const KEYS = [
  'mips', 'spmigs', 'hazmat_items', 'mrcs', 'mrc_items',
  'requests', 'request_lines', 'audit_log',
];

function isEmpty(snapshot) {
  if (!snapshot) return true;
  return KEYS.every((k) => Array.isArray(snapshot[k]) && snapshot[k].length === 0);
}

function summarise(snapshot) {
  const parts = [];
  const r = (snapshot.requests || []).length;
  const m = (snapshot.mips || []).length;
  const sp = (snapshot.spmigs || []).length;
  const it = (snapshot.hazmat_items || []).length;
  if (r) parts.push(`${r} request${r === 1 ? '' : 's'}`);
  if (m || sp || it) {
    const cat = [];
    if (m) cat.push(`${m} MIP${m === 1 ? '' : 's'}`);
    if (sp) cat.push(`${sp} SPMIG${sp === 1 ? '' : 's'}`);
    if (it) cat.push(`${it} item${it === 1 ? '' : 's'}`);
    parts.push(`catalog (${cat.join(', ')})`);
  }
  return parts.join(' + ') || 'saved data';
}

function buildBanner(snapshot, onRestore, onDismiss) {
  const wrap = document.createElement('div');
  wrap.id = 'hazreq-hydrate';
  wrap.setAttribute('role', 'dialog');
  wrap.style.cssText = [
    'position:fixed', 'left:0', 'right:0', 'top:0',
    'z-index:9999', 'padding:0.75rem 1rem',
    'background:#fff7e0', 'border-bottom:2px solid #d4a000',
    'display:flex', 'gap:1rem', 'align-items:center',
    'justify-content:center', 'flex-wrap:wrap',
    'font-size:0.95rem',
  ].join(';');

  const msg = document.createElement('span');
  msg.textContent = `Found ${summarise(snapshot)} saved in this browser. Restore?`;
  wrap.appendChild(msg);

  const restore = document.createElement('button');
  restore.textContent = 'Restore';
  restore.className = 'btn primary';
  restore.style.padding = '0.35rem 0.85rem';
  restore.addEventListener('click', () => {
    restore.disabled = true;
    restore.textContent = 'Restoring…';
    onRestore().catch((e) => {
      restore.disabled = false;
      restore.textContent = 'Restore';
      alert('Restore failed: ' + (e && e.message ? e.message : 'unknown error'));
    });
  });
  wrap.appendChild(restore);

  const dismiss = document.createElement('button');
  dismiss.textContent = 'Dismiss';
  dismiss.className = 'btn';
  dismiss.style.padding = '0.35rem 0.85rem';
  dismiss.addEventListener('click', () => {
    wrap.remove();
    onDismiss();
  });
  wrap.appendChild(dismiss);

  return wrap;
}

async function postRestore(snapshot) {
  const blob = new Blob([JSON.stringify(snapshot)], { type: 'application/json' });
  const fd = new FormData();
  fd.append('file', blob, 'browser-snapshot.json');
  const resp = await fetch('/admin/import.json', {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
    redirect: 'manual',
  });
  // Server redirects on success; treat 3xx and 2xx alike.
  if (resp.status >= 400) {
    const txt = await resp.text().catch(() => '');
    throw new Error('HTTP ' + resp.status + ': ' + txt.slice(0, 200));
  }
  window.location.reload();
}

export async function maybeOfferHydrate(serverSnapshot) {
  // Already-dismissed-this-session check
  if (sessionStorage.getItem('hazreq:hydrate-dismissed')) return;

  if (!isEmpty(serverSnapshot)) return;
  const stored = await readMirror();
  if (!stored || isEmpty(stored)) return;

  const banner = buildBanner(
    stored,
    () => postRestore(stored),
    () => sessionStorage.setItem('hazreq:hydrate-dismissed', '1'),
  );
  document.body.appendChild(banner);
}
