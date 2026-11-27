#!/usr/bin/env bash
# Container entrypoint for the ModelScope Docker Studio.
set -euo pipefail

RUNTIME=/app/runtime
PERSIST=/mnt/workspace/qf

mkdir -p "$RUNTIME/data" "$RUNTIME/qdrant" "$PERSIST/models"

# Restore the last consistent snapshot when the container disk is fresh.
if [ -f "$PERSIST/runtime.tar.gz" ] && [ -z "$(ls -A "$RUNTIME/data" 2>/dev/null)" ]; then
	echo "[start] restoring runtime snapshot from persistent volume"
	tar -xzf "$PERSIST/runtime.tar.gz" -C "$RUNTIME" || true
fi

# Keep the FastEmbed cache on the persistent volume, not the container disk.
rm -rf "$RUNTIME/data/models"
ln -sfn "$PERSIST/models" "$RUNTIME/data/models"

python -c "from app.database import init_db; init_db(); print('[start] database ready')"

echo "[start] launching supervisord"
exec /usr/bin/supervisord -n -c /app/deploy/ms/supervisord.conf
