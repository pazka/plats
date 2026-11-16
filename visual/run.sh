#!/usr/bin/env bash
# Visual regression of a built image (hosh-management AGENTS.md §3, use cases in TESTING.md).
#   visual/run.sh [IMAGE] [--update]      (default plats:local)
# Starts the image hardened (read-only, no caps, uid 10001), seeds it with e2e/seed.py, then
# visual/shoot.mjs shoots every use case at 1400 / 1024 / 390 and compares with visual/baseline/.
# Needs: docker, node >= 22, a Chrome ($CHROME, default google-chrome). No package is installed.
# Outbound HTTP of the container goes to a dead proxy: nothing leaves the machine.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${1:-plats:local}"
shift || true
PORT="${VISUAL_PORT:-18300}"
NAME="plats-visual-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$NAME" --read-only --tmpfs /tmp:size=16m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -e PLATS_COOKIE_SECURE=0 -e PLATS_POW_DIFFICULTY=12 \
  -e HTTPS_PROXY=http://127.0.0.1:9 -e HTTP_PROXY=http://127.0.0.1:9 \
  -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null
for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || { docker logs "$NAME"; exit 1; }
docker exec -i "$NAME" python - < e2e/seed.py
test "$(docker exec "$NAME" id -u)" = 10001

node visual/shoot.mjs "http://127.0.0.1:$PORT" "$@"
