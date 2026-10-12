#!/usr/bin/env bash
# Browser end-to-end test of a built image, with log monitoring.
#   e2e/run.sh [IMAGE]      (default plats:local)
# Needs: docker, node + the `playwright` package (PLAYWRIGHT_MODULE=/path/to/playwright to override),
# a Chromium usable by Playwright, python3 (stdlib only).
# The container's outbound HTTP goes to a dead proxy on purpose: the admin "import" button is
# exercised offline (the failure must be reported cleanly); the live import has its own workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${1:-plats:local}"
PORT="${E2E_PORT:-18200}"
NAME="plats-e2e-$$"
OUT="${E2E_OUT:-e2e/out}"
mkdir -p "$OUT"
rm -f "$OUT"/*.png "$OUT"/*.xlsx "$OUT"/*.log 2>/dev/null || true

ADMIN_PASSWORD="e2e-admin-password-$RANDOM$RANDOM"
TOTP_SECRET="$(docker run --rm --network none "$IMAGE" python -c 'import pyotp; print(pyotp.random_base32())')"
HASH="$(docker run --rm --network none -e P="$ADMIN_PASSWORD" "$IMAGE" python -c 'import os; from app.auth import hash_password; print(hash_password(os.environ["P"]))')"
ENVFILE="$(mktemp)"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -f "$ENVFILE"' EXIT
chmod 600 "$ENVFILE"
cat > "$ENVFILE" <<ENV
PLATS_ADMIN_PASSWORD_HASH='$HASH'
PLATS_ADMIN_TOTP_SECRET='$TOTP_SECRET'
PLATS_COOKIE_SECURE=0
PLATS_POW_DIFFICULTY=12
HTTPS_PROXY=http://127.0.0.1:9
HTTP_PROXY=http://127.0.0.1:9
ENV

docker run -d --name "$NAME" --read-only --tmpfs /tmp:size=16m --cap-drop ALL \
  --security-opt no-new-privileges:true --env-file "$ENVFILE" -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null
for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
done
docker exec -i "$NAME" python - < e2e/seed.py

status=0
BASE="http://127.0.0.1:$PORT" ADMIN_PASSWORD="$ADMIN_PASSWORD" TOTP_SECRET="$TOTP_SECRET" OUT="$OUT" \
  node e2e/browser.cjs || status=$?

echo
echo "--- export check (zero formula cells) ---"
python3 - "$OUT/export.xlsx" <<'PY' || status=1
import re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
formulas = sum(len(re.findall(rb"<f[ >]", z.read(n))) for n in sheets)
print(f"{len(sheets)} sheets, {formulas} formula cells")
sys.exit(1 if formulas or len(sheets) != 6 else 0)
PY

echo
echo "--- container logs ---"
docker logs "$NAME" > "$OUT/container.log" 2>&1
python3 scripts/monitor_logs.py --fail-on-error --ignore 'wikidata import failed' \
  < "$OUT/container.log" || status=1
grep -q '"import_wikidata_failed"\|wikidata import failed' "$OUT/container.log" \
  && echo "offline import failure was logged as expected" || { echo "expected an import failure log line"; status=1; }
if grep -Eq '"client_ip"|127\.0\.0\.1|172\.1[6-9]\.' "$OUT/container.log"; then
  echo "raw IP found in logs!"; status=1
fi
test "$(docker exec "$NAME" id -u)" = 10001 || { echo "not running as uid 10001"; status=1; }
exit $status
