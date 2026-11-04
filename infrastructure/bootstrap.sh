#!/usr/bin/env bash
# Repeatable synthetic catalogue setup. No cloud calls or real ingestion.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null || { echo "ERROR: Docker is missing" >&2; exit 2; }
docker compose version >/dev/null || { echo "ERROR: Docker Compose v2 required" >&2; exit 2; }
COMPOSE=(docker compose -f docker-compose.yml -f infrastructure/compose.tools.yml)
echo "Starting disposable PostgreSQL..."
"${COMPOSE[@]}" up -d --wait postgres
echo "Building isolated application image..."
"${COMPOSE[@]}" build tools
echo "Loading synthetic company catalogue..."
"${COMPOSE[@]}" run --rm tools seed
echo "Bootstrap complete."
