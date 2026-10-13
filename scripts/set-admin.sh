#!/usr/bin/env bash
# Set (or reset) the admin password + TOTP for plats.hosh.it. Run it ON THE SERVER, interactively.
#
#   scripts/set-admin.sh [ENV_FILE]          default ENV_FILE: ./plats.env
#
# - asks for the password twice (hidden input) and stores only its argon2id hash;
# - generates a TOTP secret, shows it as a QR code in this terminal, and checks one code;
# - writes PLATS_ADMIN_PASSWORD_HASH / PLATS_ADMIN_TOTP_SECRET into ENV_FILE (mode 600),
#   keeping every other line. Nothing secret is printed to logs or passed on a command line.
#
# By default it runs the published image (no Python needed on the host), offline (--network none).
#   PLATS_IMAGE=ghcr.io/pazka/plats:v0.3.0 scripts/set-admin.sh /srv/plats/plats.env
#   PLATS_LOCAL=1 scripts/set-admin.sh      # use local python3 + requirements instead of Docker
# Then restart the container so it picks up the new values.
set -euo pipefail

ENV_FILE="${1:-./plats.env}"
# Image: $PLATS_IMAGE, else the PLATS_IMAGE line of the env file, else :latest.
FILE_IMAGE="$(sed -n "s/^PLATS_IMAGE=//p" "$ENV_FILE" 2>/dev/null | tail -n1 | tr -d "'\"")"
IMAGE="${PLATS_IMAGE:-${FILE_IMAGE:-ghcr.io/pazka/plats:latest}}"

if [ ! -t 0 ] || [ ! -t 1 ]; then
  echo "set-admin.sh must be run interactively, in a terminal." >&2
  exit 1
fi

umask 077
dir="$(cd "$(dirname "$ENV_FILE")" && pwd)"
base="$(basename "$ENV_FILE")"
touch "$dir/$base"
chmod 600 "$dir/$base"

if [ "${PLATS_LOCAL:-0}" = "1" ]; then
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$repo"
  exec python3 -m app.cli set-admin --env-file "$dir/$base"
fi

exec docker run --rm -it \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" \
  -v "$dir:/out" \
  "$IMAGE" python -m app.cli set-admin --env-file "/out/$base"
