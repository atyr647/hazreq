// IDB-primary + OPFS-mirror + localStorage-fallback for the
// hazreq backup blob. One JSON envelope per cookie/session, written
// to all available tiers so any single-tier failure is recoverable.
//
// Server is the authoritative state during a session; this module
// just makes sure the JSON envelope from /admin/export.json never
// disappears from the user's browser between visits.
//
// The mirror does NOT auto-restore. If the server's session DB is
// empty and the browser has saved state, the hydrate module prompts
// the user before re-importing.

import { probe, requestPersistenceOnce } from './probe.js';

const IDB_DB = 'hazreq';
const IDB_VERSION = 1;
const STORE = 'state';
const KEY = 'snapshot';
const LS_KEY = 'hazreq:snapshot';
const OPFS_DIR = 'hazreq';
const OPFS_FILE = 'snapshot.json';

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_DB, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

async function idbWrite(snapshot) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(snapshot, KEY);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

async function idbRead() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).get(KEY);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function opfsWrite(snapshot) {
  const root = await navigator.storage.getDirectory();
  const dir = await root.getDirectoryHandle(OPFS_DIR, { create: true });
  const file = await dir.getFileHandle(OPFS_FILE, { create: true });
  const w = await file.createWritable();
  await w.write(JSON.stringify(snapshot));
  await w.close();
}

async function opfsRead() {
  try {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle(OPFS_DIR);
    const file = await dir.getFileHandle(OPFS_FILE);
    const f = await file.getFile();
    const txt = await f.text();
    return JSON.parse(txt);
  } catch (e) {
    return null;
  }
}

function lsWrite(snapshot) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(snapshot));
    return true;
  } catch (e) {
    return false;
  }
}

function lsRead() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function isValidSnapshot(s) {
  return (
    s &&
    typeof s === 'object' &&
    s.type === 'hazreq-backup' &&
    s.schemaVersion === 1
  );
}

// Read the best-available cached snapshot. Tier order: IDB → OPFS → LS.
// Returns null if every tier is empty or unhealthy.
export async function readMirror() {
  const caps = await probe();
  if (caps.idb) {
    const v = await idbRead().catch(() => null);
    if (isValidSnapshot(v)) return v;
  }
  if (caps.opfs) {
    const v = await opfsRead();
    if (isValidSnapshot(v)) return v;
  }
  if (caps.localStorage) {
    const v = lsRead();
    if (isValidSnapshot(v)) return v;
  }
  return null;
}

// Write to every available tier. IDB synchronously, OPFS+LS in the
// background — page nav doesn't wait on disk.
let opfsScheduled = false;
function scheduleOpfsFlush(snapshot) {
  if (opfsScheduled) return;
  opfsScheduled = true;
  const cb = () => {
    opfsScheduled = false;
    opfsWrite(snapshot).catch(() => {});
  };
  if (typeof requestIdleCallback === 'function') {
    requestIdleCallback(cb, { timeout: 2000 });
  } else {
    setTimeout(cb, 1000);
  }
}

export async function writeMirror(snapshot) {
  if (!isValidSnapshot(snapshot)) return;
  const caps = await probe();
  // Persistent-storage prompt is a one-shot — first time we have real
  // data to protect, ask the browser to keep it. Fire-and-forget.
  if (caps.idb || caps.opfs) requestPersistenceOnce();

  const writes = [];
  if (caps.idb) writes.push(idbWrite(snapshot).catch(() => {}));
  if (caps.localStorage) lsWrite(snapshot);  // sync, cheap
  if (caps.opfs) scheduleOpfsFlush(snapshot);
  await Promise.all(writes);
}

// Ask the server for the canonical state and mirror it locally.
// Debounced so a flurry of mutations doesn't thrash.
let pending = null;
let lastFetch = 0;
const MIN_INTERVAL_MS = 300;

export function refreshFromServer({ force = false } = {}) {
  if (pending) return pending;
  const now = Date.now();
  const wait = force ? 0 : Math.max(0, MIN_INTERVAL_MS - (now - lastFetch));
  pending = new Promise((resolve) => {
    setTimeout(async () => {
      lastFetch = Date.now();
      pending = null;
      try {
        const resp = await fetch('/admin/export.json', { credentials: 'same-origin' });
        if (!resp.ok) return resolve(null);
        const snap = await resp.json();
        if (isValidSnapshot(snap)) {
          await writeMirror(snap);
          window.dispatchEvent(new CustomEvent('hazreq:mirrored', { detail: snap }));
          resolve(snap);
        } else {
          resolve(null);
        }
      } catch (e) {
        resolve(null);
      }
    }, wait);
  });
  return pending;
}
