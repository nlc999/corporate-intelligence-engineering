#!/usr/bin/env bash
# Fail-fast synthetic database and search integration verification.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null || { echo "ERROR: Docker is missing" >&2; exit 2; }
docker compose version >/dev/null || { echo "ERROR: Docker Compose v2 required" >&2; exit 2; }
COMPOSE=(docker compose -f docker-compose.yml -f infrastructure/compose.tools.yml)
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
"${COMPOSE[@]}" exec -T postgres pg_isready -U postgres -d postgres >/dev/null
COUNT="$("${COMPOSE[@]}" exec -T postgres psql -U postgres -d postgres -tA -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) FROM osint.company_catalogue;')"
[[ "$COUNT" == "6" ]] || { echo "ERROR: expected 6 synthetic registrations; got $COUNT" >&2; exit 1; }
"${COMPOSE[@]}" run --rm tools search "GREENLEAF ENGINEERING LIMITED" >"$TMP"
grep -Fq "00000006" "$TMP" || { echo "ERROR: current-name match missing" >&2; exit 1; }
grep -Fq "00000001" "$TMP" || { echo "ERROR: former-name match missing" >&2; exit 1; }
echo "Synthetic catalogue and search validation passed."
