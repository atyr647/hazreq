#!/usr/bin/env bash
# Local development runner. Bootstraps venv, runs migrations, starts uvicorn with reload.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e .
fi

PYTHONPATH=. .venv/bin/alembic upgrade head

if [ ! -f data/templates/hazmat_chit.docx ]; then
  PYTHONPATH=. .venv/bin/python scripts/prepare_template.py
fi

exec PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
