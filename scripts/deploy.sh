#!/usr/bin/env bash
# One-shot, idempotent deployment of plats on the production box.
#
#   scripts/deploy.sh [--env-file plats.env] [--no-import] [--images] [--skip-external]
#
# Run from a checkout of the repo (e.g. /srv/plats at a release tag). plats.env = copy of
# .env.example (defaults fit the hosh.it box) + admin values written by the owner's scripts/set-admin.sh.
# This script never prints secret values. Steps:
#   1 preflight + routing (PLATS_ROUTING=file: render the Traefik file-provider route)  2 pull + up
#   3 wait healthy  4 Wikidata import if the catalogue is empty
#   5 smoke checks (inside the container, loopback port, then through Traefik)  6 log report
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
[ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Create it: install -m 600 .env.example $ENV_FILE ; then the owner runs scripts/set-admin.sh $ENV_FILE"
if [ "$(stat -c %a "$ENV_FILE")" != "600" ]; then chmod 600 "$ENV_FILE"; echo "fixed permissions of $ENV_FILE to 600"; fi
[ -f docker-compose.yml ] || { cp docker-compose.example.yml docker-compose.yml; echo "created docker-compose.yml from the example"; }
ROUTING="$(var PLATS_ROUTING)"; ROUTING="${ROUTING:-file}"
case "$ROUTING" in
  file) COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml) ;;
  labels) COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.labels.yml) ;;
  *) die "PLATS_ROUTING must be 'file' or 'labels' (got '$ROUTING')" ;;
esac

case "$(var PLATS_ADMIN_PASSWORD_HASH)" in
  '$argon2id$'*) echo "admin password hash: present (argon2id)" ;;
  *) die "PLATS_ADMIN_PASSWORD_HASH missing or not argon2id. Owner: run scripts/set-admin.sh $ENV_FILE" ;;
esac
[[ "$(var PLATS_ADMIN_TOTP_SECRET)" =~ ^[A-Z2-7]{16,}=*$ ]] || die "PLATS_ADMIN_TOTP_SECRET missing/invalid. Owner: run scripts/set-admin.sh $ENV_FILE"
echo "admin TOTP secret: present"
[ "$(var PLATS_COOKIE_SECURE)" = "0" ] && red "warning: PLATS_COOKIE_SECURE=0 — cookies will not be Secure (OK only for plain-http tests)"

IMAGE="$(var PLATS_IMAGE)"; IMAGE="${IMAGE:-ghcr.io/pazka/plats:v0.3.0}"
HOST="$(var PLATS_HOST)"; HOST="${HOST:-plats.hosh.it}"
ENTRY="$(var TRAEFIK_ENTRYPOINT)"; ENTRY="${ENTRY:-websecure}"
TAPI="$(var TRAEFIK_API)"
if [ "$ROUTING" = labels ]; then
  NET="$(var TRAEFIK_NETWORK)"; NET="${NET:-traefik}"
  if ! docker network inspect "$NET" >/dev/null 2>&1; then
    red "docker network '$NET' (TRAEFIK_NETWORK) does not exist. Networks on this host:"
    docker network ls --format '  {{.Name}}'
    die "set TRAEFIK_NETWORK in $ENV_FILE to the network Traefik is attached to (or PLATS_ROUTING=file)"
  fi
  echo "image: $IMAGE   host: $HOST   routing: labels on network $NET"
else
  BIND="$(var PLATS_BIND)"; BIND="${BIND:-127.0.0.1:9131}"
  case "$BIND" in 127.0.0.1:*|'[::1]':*) ;; *) die "PLATS_BIND=$BIND is not a loopback address (box standard: 127.0.0.1:PORT)" ;; esac
  other="$(docker ps --format '{{.Names}} {{.Ports}}' | awk -v b="$BIND->" '$1 != "plats" && index($0, b) {print $1}')"
  [ -z "$other" ] || die "PLATS_BIND=$BIND is already published by container '$other' — pick a free loopback port"
  echo "image: $IMAGE   host: $HOST   routing: file provider -> http://$BIND"
fi
"${COMPOSE[@]}" config -q || die "docker compose config is invalid"

if [ "$ROUTING" = file ]; then
  # Render the Traefik route now (validation); it is written only once the container is healthy.
  DYN="$(var TRAEFIK_DYNAMIC_DIR)"
  RESOLVER="$(var TRAEFIK_CERTRESOLVER)"
  if [ -n "$RESOLVER" ]; then TLS="{ certResolver: $RESOLVER }"; else TLS="{}"; fi
  route="$(sed -e "s|__HOST__|$HOST|" -e "s|__BIND__|$BIND|" -e "s|__ENTRYPOINT__|$ENTRY|" -e "s|__TLS__|$TLS|" deploy/traefik-route.yml.tmpl)"
  [ -z "$DYN" ] || [ -d "$DYN" ] || die "TRAEFIK_DYNAMIC_DIR=$DYN does not exist (the edge Traefik file-provider directory)"
fi

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

if [ "$ROUTING" = file ]; then
  curl -fsS --max-time 5 "http://$BIND/healthz" >/dev/null || die "the container is healthy but http://$BIND/healthz does not answer from the host (port mapping?)"
  echo "loopback: http://$BIND/healthz OK"
  # Traefik route (file provider). Idempotent: only rewritten when the content changes.
  if [ -z "$DYN" ]; then
    red "warning: TRAEFIK_DYNAMIC_DIR is empty — install this route in Traefik's file-provider directory yourself:"
    echo "$route"
  elif [ -f "$DYN/plats.yml" ] && [ "$(cat "$DYN/plats.yml")" = "$route" ]; then
    echo "traefik route: $DYN/plats.yml up to date"
  else
    [ -f "$DYN/plats.yml" ] && cp -p "$DYN/plats.yml" "$DYN/.plats.yml.prev" && echo "previous route kept as $DYN/.plats.yml.prev"
    printf '%s\n' "$route" > "$DYN/plats.yml.tmp" && chmod 644 "$DYN/plats.yml.tmp" && mv "$DYN/plats.yml.tmp" "$DYN/plats.yml"
    echo "traefik route: wrote $DYN/plats.yml (Traefik hot-reloads it)"
  fi
  if [ -n "$TAPI" ]; then
    for i in $(seq 1 10); do
      rstatus="$(curl -fsS --max-time 3 "$TAPI/api/http/routers/plats@file" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
      [ "$rstatus" = enabled ] && break
      sleep 1
    done
    if [ "$rstatus" = enabled ]; then echo "traefik: router plats@file enabled"
    else red "warning: Traefik API ($TAPI) does not report router plats@file as enabled (status '${rstatus:-unreachable}')"; fi
  fi
fi

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
  # A brand-new route needs a few seconds for its ACME certificate: retry before failing.
  ext_ok=0
  for attempt in 1 2 3 4; do
    if "${EXEC[@]}" python scripts/smoke.py "https://$HOST" --min-dishes "$MIN"; then ext_ok=1; break; fi
    [ "$attempt" = 4 ] || { echo "retrying in 20 s (certificate / router may still be loading)…"; sleep 20; }
  done
  if [ "$ext_ok" = 1 ]; then
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
