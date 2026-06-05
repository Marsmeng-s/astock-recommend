#!/bin/sh
set -e
PORT="${PORT:-10000}"
echo "[START] binding 0.0.0.0:${PORT}" >&2
exec gunicorn cloud_app:app \
  --bind "0.0.0.0:${PORT}" \
  --workers 1 \
  --worker-class gthread \
  --threads 4 \
  --timeout 180 \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  --enable-stdio-inheritance \
  --log-level info
