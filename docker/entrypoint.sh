#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="${CELLIER_TRUSTED_PROXIES:-127.0.0.1}"

