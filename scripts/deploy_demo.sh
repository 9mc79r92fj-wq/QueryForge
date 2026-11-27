#!/usr/bin/env bash
# Deploy the QueryForge demo stack on a single Linux host.
#
# Usage (from the repository root):
#   bash scripts/deploy_demo.sh            # configure, build, start, wait
#   bash scripts/deploy_demo.sh --seed     # additionally seed the demo corpus
#
# Requirements: Docker Engine with Compose v2.24.4+, curl, python3.
# The script never writes the model key; edit .env.demo before enabling RAG.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SEED=0
if [ "${1:-}" = "--seed" ]; then
	SEED=1
fi

if ! command -v docker >/dev/null 2>&1; then
	echo "error: docker is not installed" >&2
	exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
	echo "error: docker compose v2 is required" >&2
	exit 1
fi

COMPOSE=(docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml)

if [ ! -f .env.demo ]; then
	cp .env.demo.example .env.demo
	chmod 600 .env.demo
	TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
	python3 - "$TOKEN" <<'PY'
import pathlib
import sys

path = pathlib.Path(".env.demo")
text = path.read_text(encoding="utf-8")
text = text.replace("DEMO_ADMIN_TOKEN=", "DEMO_ADMIN_TOKEN=" + sys.argv[1], 1)
path.write_text(text, encoding="utf-8")
PY
	echo "created .env.demo with a generated administrator token"
else
	echo "using existing .env.demo"
fi

if ! grep -qE '^LLM_API_KEY=.+' .env.demo; then
	echo "note: LLM_API_KEY is empty; RAG and tool calls stay disabled until it is configured"
fi

echo "== validating compose configuration =="
"${COMPOSE[@]}" config --quiet

echo "== building api image =="
"${COMPOSE[@]}" build api

echo "== starting services =="
"${COMPOSE[@]}" up -d --no-build --wait

PORT="$(grep -E '^DEMO_API_BIND_PORT=' .env.demo | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8001}"

echo "== waiting for readiness on port ${PORT} =="
for _ in $(seq 1 60); do
	if curl -fsS "http://127.0.0.1:${PORT}/api/v1/ready" >/dev/null 2>&1; then
		break
	fi
	sleep 2
done
curl -fsS "http://127.0.0.1:${PORT}/api/v1/ready"
echo

if [ "$SEED" = "1" ]; then
	echo "== seeding demo corpus =="
	IMAGE="${QUERYFORGE_IMAGE:-queryforge-local:latest}"
	TOKEN="$(grep -E '^DEMO_ADMIN_TOKEN=' .env.demo | cut -d= -f2 | tr -d '[:space:]')"
	docker run --rm --network host -v "${ROOT}:/src" -w /src \
		-e DEMO_API="http://127.0.0.1:${PORT}" \
		-e DEMO_ADMIN_TOKEN="${TOKEN}" \
		"${IMAGE}" python scripts/seed_demo_corpus.py
	echo
	echo "Add every returned version id to DEMO_PUBLIC_VERSION_IDS in .env.demo,"
	echo "then run: ${COMPOSE[*]} up -d --no-deps --force-recreate api"
fi

echo
echo "demo API: http://127.0.0.1:${PORT}/"
echo "next: configure TLS with deploy/Caddyfile, then verify /docs returns 404 and anonymous upload returns 401"
