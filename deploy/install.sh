#!/usr/bin/env bash
# hazreq Pi 400 installer. Run as root.
set -euo pipefail

APP_USER="${APP_USER:-hazreq}"
APP_HOME="${APP_HOME:-/opt/hazreq}"
DATA_DIR="${DATA_DIR:-/var/lib/hazreq}"
REPO_SRC="${REPO_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"

echo "==> Installing apt packages"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  libreoffice-core libreoffice-writer \
  avahi-daemon sqlite3

echo "==> Creating system user '${APP_USER}'"
id -u "${APP_USER}" >/dev/null 2>&1 || \
  useradd --system --home "${APP_HOME}" --shell /usr/sbin/nologin "${APP_USER}"

echo "==> Copying source to ${APP_HOME}"
mkdir -p "${APP_HOME}"
rsync -a --delete \
  --exclude='.venv' --exclude='data' --exclude='.git' \
  "${REPO_SRC}/" "${APP_HOME}/"

echo "==> Creating venv and installing deps"
python3 -m venv "${APP_HOME}/.venv"
"${APP_HOME}/.venv/bin/pip" install --upgrade pip
"${APP_HOME}/.venv/bin/pip" install -e "${APP_HOME}"
"${APP_HOME}/.venv/bin/pip" install unoserver

echo "==> Setting up data directories"
mkdir -p "${DATA_DIR}"/{templates,pdfs,backups}
cp "${REPO_SRC}/data/templates/hazmat_chit.docx" "${DATA_DIR}/templates/" 2>/dev/null || \
  echo "  (no template found in repo — generate with scripts/prepare_template.py)"
chown -R "${APP_USER}:${APP_USER}" "${DATA_DIR}" "${APP_HOME}"

echo "==> Running migrations"
sudo -u "${APP_USER}" \
  HAZREQ_DATA_DIR="${DATA_DIR}" \
  PYTHONPATH="${APP_HOME}" \
  "${APP_HOME}/.venv/bin/alembic" -c "${APP_HOME}/alembic.ini" upgrade head

echo "==> Installing systemd units"
install -m 0644 "${REPO_SRC}/deploy/hazreq.service" /etc/systemd/system/
install -m 0644 "${REPO_SRC}/deploy/hazreq-unoserver.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hazreq-unoserver.service
systemctl enable --now hazreq.service

echo "==> Installing Avahi mDNS service"
install -m 0644 "${REPO_SRC}/deploy/hazreq-avahi.service" /etc/avahi/services/hazreq.service
systemctl restart avahi-daemon || true

echo "==> Installing nightly backup cron"
install -m 0644 "${REPO_SRC}/deploy/hazreq-backup.cron" /etc/cron.d/hazreq-backup

echo "==> Installing desktop launcher (system-wide menu entry)"
install -m 0644 "${REPO_SRC}/deploy/hazreq.desktop" /usr/share/applications/hazreq.desktop

echo
echo "Done. hazreq is running at:"
echo "  http://localhost:8000"
echo "  http://hazreq.local:8000   (LAN, via mDNS)"
echo
echo "Useful commands:"
echo "  systemctl status hazreq            # web app"
echo "  systemctl status hazreq-unoserver  # PDF converter"
echo "  journalctl -u hazreq -f            # tail logs"
