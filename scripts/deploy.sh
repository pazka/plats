#!/usr/bin/env bash
# One-shot, idempotent deployment of plats on the production box.
#
#   scripts/deploy.sh [--env-file plats.env] [--no-import] [--images] [--skip-external]
#
# Run from a checkout of the repo (e.g. /srv/plats at a release tag). The owner prepares
# plats.env beforehand (template: .env.example; admin values via scripts/set-admin.sh).
# This script never prints secret values. Steps:
#   1 preflight  2 pull + up  3 wait healthy  4 Wikidata import if the catalogue is empty
#   5 smoke checks (inside the container, then through Traefik)  6 log report
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="plats.env"
DO_IMPORT=1
IMPORT_ARGS=()
EXTERNAL=1
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --no-import) DO_IMPORT=0; shift ;;
    --images) IMPORT_ARGS+=(--images); shift ;;
    --skip-external) EXTERNAL=0; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { red "ERROR: $*"; exit 1; }
# Read KEY from the env file without sourcing it (values may contain $), strip one level of quotes.
var() { sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1=//p" "$ENV_FILE" | tail -n1 | sed -e "s/^'\(.*\)'$/\1/" -e 's/^"\(.*\)"$/\1/'; }

step "1/6 preflight"
command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin is missing"
[ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Owner: cp .env.example $ENV_FILE, fill it, run scripts/set-admin.sh $ENV_FILE"
if [ "$(stat -c %a "$ENV_FILE")" != "600" ]; then chmod 600 "$ENV_FILE"; echo "fixed permissions of $ENV_FILE to 600"; fi
[ -f docker-compose.yml ] || { cp docker-compose.example.yml docker-compose.yml; echo "created docker-compose.yml from the example"; }
COMPOSE=(docker compose --env-file "$ENV_FILE")

case "$(var PLATS_ADMIN_PASSWORD_HASH)" in
  '$argon2id$'*) echo "admin password hash: present (argon2id)" ;;
  *) die "PLATS_ADMIN_PASSWORD_HASH missing or not argon2id. Owner: run scripts/set-admin.sh $ENV_FILE" ;;
esac
[[ "$(var PLATS_ADMIN_TOTP_SECRET)" =~ ^[A-Z2-7]{16,}=*$ ]] || die "PLATS_ADMIN_TOTP_SECRET missing/invalid. Owner: run scripts/set-admin.sh $ENV_FILE"
echo "admin TOTP secret: present"
[ "$(var PLATS_COOKIE_SECURE)" = "0" ] && red "warning: PLATS_COOKIE_SECURE=0 — cookies will not be Secure (OK only for plain-http tests)"

IMAGE="$(var PLATS_IMAGE)"; IMAGE="${IMAGE:-ghcr.io/pazka/plats:v0.2.0}"
HOST="$(var PLATS_HOST)"; HOST="${HOST:-plats.hosh.it}"
NET="$(var TRAEFIK_NETWORK)"; NET="${NET:-traefik}"
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  red "docker network '$NET' (TRAEFIK_NETWORK) does not exist. Networks on this host:"
  docker network ls --format '  {{.Name}}'
  die "set TRAEFIK_NETWORK in $ENV_FILE to the network Traefik is attached to"
fi
echo "image: $IMAGE   host: $HOST   traefik network: $NET"
"${COMPOSE[@]}" config -q || die "docker compose config is invalid"

step "2/6 pull + up"
if ! "${COMPOSE[@]}" pull plats; then
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "cannot pull $IMAGE"
  red "warning: pull failed, using the local copy of $IMAGE"
fi
"${COMPOSE[@]}" up -d plats

step "3/6 wait for healthy"
CID="$("${COMPOSE[@]}" ps -q plats)"
for i in $(seq 1 60); do
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID")"
  [ "$state" = healthy ] && break
  [ "$state" = exited ] || [ "$state" = dead ] && break
  sleep 2
done
if [ "$state" != healthy ]; then
  "${COMPOSE[@]}" logs --tail 50 plats
  die "container is '$state', not healthy (logs above)"
fi
green "healthy"
EXEC=("${COMPOSE[@]}" exec -T plats)
"${EXEC[@]}" python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz')))"
test "$("${EXEC[@]}" id -u)" = 10001 || die "container is not running as uid 10001"
"${EXEC[@]}" sh -c 'touch /app/.rw-test 2>/dev/null' && die "root filesystem is writable (read_only missing?)" || echo "root filesystem: read-only"

step "4/6 catalogue"
dishes="$("${EXEC[@]}" python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz'))['active_dishes'])")"
echo "active dishes: $dishes"
MIN=1
if [ "$DO_IMPORT" = 1 ] && [ "$dishes" -lt 100 ]; then
  echo "importing from Wikidata (a few minutes; idempotent, safe to re-run)…"
  if ! out="$("${EXEC[@]}" python scripts/import_wikidata.py "${IMPORT_ARGS[@]}" 2>/dev/null)"; then
    echo "$out" | tail -n1
    die "Wikidata import failed. Check that the container has outbound HTTPS to query.wikidata.org and fr.wikipedia.org, then re-run scripts/deploy.sh (the import is idempotent)"
  fi
  echo "$out" | tail -n1
  dishes="$("${EXEC[@]}" python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz'))['active_dishes'])")"
  echo "active dishes after import: $dishes"
  [ "$dishes" -ge 500 ] || die "import produced only $dishes dishes (expected ≈1000) — see logs"
fi
[ "$dishes" -ge 500 ] && MIN=500

step "5/6 smoke checks"
"${EXEC[@]}" python scripts/smoke.py http://127.0.0.1:8000 --min-dishes "$MIN" || die "internal smoke checks failed"
if [ "$EXTERNAL" = 1 ]; then
  echo
  echo "through Traefik: https://$HOST"
  if "${EXEC[@]}" python scripts/smoke.py "https://$HOST" --min-dishes "$MIN"; then
    hsts="$(curl -fsSI "https://$HOST/" 2>/dev/null | grep -i '^strict-transport-security' || true)"
    [ -n "$hsts" ] && green "HSTS: $hsts" || red "warning: no Strict-Transport-Security header from Traefik"
  else
    red "external smoke checks failed (DNS, Traefik router/labels or certificate?) — internal checks passed"
    EXT_FAILED=1
  fi
fi

step "6/6 log report (last 15 min)"
"${COMPOSE[@]}" logs --no-log-prefix --since 15m plats 2>&1 | "${EXEC[@]}" python scripts/monitor_logs.py || true

echo
if [ "${EXT_FAILED:-0}" = 1 ]; then die "deployed and healthy internally, but not reachable correctly at https://$HOST"; fi
green "plats is deployed: https://$HOST  (admin: https://$HOST/admin)"
echo "watch the logs: scripts/logs.sh follow"
