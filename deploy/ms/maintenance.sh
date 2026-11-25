#!/usr/bin/env bash
# Waits for the API, publishes the bundled corpus once, then snapshots.
set -uo pipefail

API="http://127.0.0.1:7860"
PERSIST=/mnt/workspace/qf
RUNTIME=/app/runtime

mkdir -p "$PERSIST"

snapshot() {
	python - <<'PY'
import sqlite3
from pathlib import Path

path = Path("/app/runtime/data/queryforge.db")
if path.exists():
    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
PY
	tar -czf "$PERSIST/runtime.tar.gz.tmp" -C "$RUNTIME" data qdrant 2>/dev/null || true
	mv -f "$PERSIST/runtime.tar.gz.tmp" "$PERSIST/runtime.tar.gz"
	echo "[maintenance] snapshot written $(date -u +%FT%TZ)"
}

echo "[maintenance] waiting for the API"
for _ in $(seq 1 100); do
	if curl -fsS "$API/api/v1/health" >/dev/null 2>&1; then
		break
	fi
	sleep 3
done

echo "[maintenance] bootstrapping the demo corpus"
python /app/scripts/ms_bootstrap.py || echo "[maintenance] bootstrap failed; continuing"
snapshot

while true; do
	sleep 300
	snapshot
done
