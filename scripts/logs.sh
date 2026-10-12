#!/usr/bin/env bash
# Log monitoring for the deployed container (uses the container's own python; nothing to install).
#   scripts/logs.sh report [SINCE]   summary of the last SINCE (default 1h): requests, 4xx/5xx,
#                                    latency, security events, errors
#   scripts/logs.sh follow           live: alerts immediately (errors, 5xx, lockouts, CSRF failures,
#                                    rate-limit bursts) + a summary every 5 minutes. Ctrl-C to stop.
#   scripts/logs.sh check [SINCE]    like report, exit code 1 if any error or 5xx (for cron/monitoring)
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${PLATS_ENV_FILE:-plats.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE")
MON=("${COMPOSE[@]}" exec -T plats python scripts/monitor_logs.py)
case "${1:-report}" in
  report) "${COMPOSE[@]}" logs --no-log-prefix --since "${2:-1h}" plats 2>&1 | "${MON[@]}" ;;
  check) "${COMPOSE[@]}" logs --no-log-prefix --since "${2:-1h}" plats 2>&1 | "${MON[@]}" --fail-on-error ;;
  follow) "${COMPOSE[@]}" logs -f --no-log-prefix --since 1m plats 2>&1 | "${MON[@]}" --follow --interval "${INTERVAL:-300}" ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
