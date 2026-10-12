# Deploying plats.hosh.it

Everything the server needs. The cloud builder had no access to the box; this file is the
hand-off for the on-box session.

## 1. What runs

| Item | Value |
|---|---|
| Image | `ghcr.io/pazka/plats:v0.1.0` (also `:latest`, `:0.1`; every `main` commit: `:ci`, `:<sha>`) |
| Process | `uvicorn --factory app.main:run`, 1 worker, listens on **port 8000** (HTTP, behind Traefik) |
| User | uid/gid **10001**, read-only rootfs OK, needs a writable `/tmp` (tmpfs) and `/data` |
| Volume | **`/data`** — `plats.sqlite3` (+ `-wal`/`-shm`), `images/` (optional Commons thumbnails), `.secrets/` (generated keys) |
| Health | **`GET /healthz`** → `{"status":"ok","schema":1,"active_dishes":N,"categories":9}` (also the Docker `HEALTHCHECK`) |
| Migrations | applied automatically at start (`PRAGMA user_version`), or `python -m app.cli migrate` |
| Logs | JSON lines on stdout (`event: http`, `vote_rejected`, `rate_limited`, `honeypot`, `admin_login_failed`, …) |
| Outbound network | none at runtime; **only the Wikidata importer** calls `query.wikidata.org`, `fr.wikipedia.org` (and `commons.wikimedia.org`, `upload.wikimedia.org` with `--images`) |

`docker-compose.example.yml` is the reference deployment (Traefik labels, `read_only`,
`tmpfs /tmp`, `cap_drop: [ALL]`, `no-new-privileges`, healthcheck, no published port).

## 2. Environment variables (`plats.env`, mode 600 — template: `.env.example`)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PLATS_ADMIN_PASSWORD_HASH` | yes (for /admin) | — | argon2id hash. **Written by `scripts/set-admin.sh`**, never by hand. Empty ⇒ admin login disabled. |
| `PLATS_ADMIN_TOTP_SECRET` | yes (for /admin) | — | base32 TOTP secret. **Written by `scripts/set-admin.sh`**. |
| `PLATS_SECRET_KEY` | no | generated in `/data/.secrets/secret_key` | signs visitor cookies, vote/form tokens. Changing it resets every visitor. |
| `PLATS_IP_SALT` | no | generated in `/data/.secrets/ip_salt` | HMAC key for IP hashes. Changing it breaks IP-hash continuity. |
| `PLATS_TRUSTED_PROXIES` | no | `127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | `X-Forwarded-For` is only honoured when the TCP peer is in these CIDRs. Narrow it to Traefik's docker network if you like (e.g. `172.18.0.0/16`). |
| `PLATS_COOKIE_SECURE` | no | `1` | keep `1` in production (HTTPS via Traefik). `0` only for plain-http local tests. |
| `PLATS_BASE_URL` | no | `https://plats.hosh.it` | |
| `PLATS_CONTACT` | no | (GitHub repo link) | shown on `/a-propos`. |
| `PLATS_POW_DIFFICULTY` | no | `16` | proposal proof-of-work bits (≈1 s on a phone); overridable in /admin → Réglages. |
| `PLATS_LOG_CLIENT_IP` | no | `0` | `1` adds the raw client IP to stdout security lines (never to the DB) — e.g. if CrowdSec should parse app logs. |
| `PLATS_DATA_DIR` | no | `/data` (set in the image) | |

Compose reads single-quoted values literally, so the `$` signs in the argon2 hash are safe. If
the file is passed to `docker run --env-file` instead, the app strips the quotes itself.

## 3. First run (on the server)

```sh
mkdir -p /srv/plats && cd /srv/plats
# deployment files from the release tag
for f in docker-compose.example.yml scripts/set-admin.sh scripts/smoke.py .env.example; do
  curl -fsSL "https://raw.githubusercontent.com/pazka/plats/v0.1.0/$f" -o "$(basename "$f")"
done
cp docker-compose.example.yml docker-compose.yml     # adjust Traefik network/entrypoint/certresolver names
install -m 600 .env.example plats.env                # optional values; admin values come next

# 1) admin password + TOTP — interactive, run by the owner in a real terminal
PLATS_IMAGE=ghcr.io/pazka/plats:v0.1.0 bash set-admin.sh ./plats.env
#    → asks the password twice, shows a QR code to scan, asks one 6-digit code, writes plats.env

# 2) start
docker compose pull && docker compose up -d
docker compose ps                                     # wait for "healthy"

# 3) import the catalogue from Wikidata (CC0) — idempotent, safe to re-run
docker compose exec plats python scripts/import_wikidata.py
#    options: --images (download Commons thumbnails with licence), --no-enrich (skip frwiki summaries)
#    same thing as the "Lancer l'import" button in /admin/plats. Expect ≈1 000 dishes, a few minutes.
```

If `docker compose pull` is denied, the GHCR package is private: either make it public
(GitHub → Packages → plats → Package settings → visibility) or `docker login ghcr.io` with a
token that has `read:packages`.

Re-running `set-admin.sh` resets the password/TOTP (then `docker compose up -d` to apply).

## 4. Smoke checks (prove full capability, not just a 200)

```sh
# inside the container (plain http to itself)
docker compose exec plats python scripts/smoke.py http://127.0.0.1:8000 --min-dishes 900
# from outside, through Traefik/TLS
python3 smoke.py https://plats.hosh.it --min-dishes 900
```

`smoke.py` checks: health + 9 categories + catalogue size; CSP/nosniff headers; HttpOnly visitor
cookie; question served with a signed token; an answer **before** the 600 ms think time is refused;
an answer is recorded (a single *skip*, not a real vote) and returns the distribution; the same token
is refused on replay (single-use); dish page, search, stats, proposal form (token + PoW + honeypot),
look-alike API, admin login page; export not public; static assets local; no external
script/style/image on any page. Exit code 0 = all good.

Then by hand (2 minutes):

1. `curl -sI https://plats.hosh.it/ | grep -iE 'strict-transport|content-security'` → HSTS (Traefik) and CSP present.
2. Open https://plats.hosh.it on a phone: answer 5 dishes (burst + "Bien joué !"), keys 1–9 work on desktop.
3. https://plats.hosh.it/admin → password + TOTP → dashboard shows the counts, "Dernier import" summary.
4. Admin → Export .xlsx downloads and opens (6 sheets).
5. Admin → Réponses: find your test answers (filter by date) and delete them if you want a clean start.
6. `docker compose logs plats | tail` → JSON lines, no raw IPs (only `ip_hash`).
7. Wrong admin password 5× → "Trop d'échecs" lockout (15 min), visible in Admin → Journal afterwards.

## 5. Operations

- **Backup** (online, consistent):
  `docker compose exec plats python -c "import sqlite3; sqlite3.connect('/data/plats.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"`
  then copy `/data/backup.sqlite3` (and `/data/.secrets/`) off the box. Also: Admin → Export .xlsx.
- **Upgrade**: change the tag in `docker-compose.yml`, `docker compose pull && docker compose up -d`;
  migrations run on start.
- **Rate limits / approval / PoW / anomaly thresholds**: Admin → Réglages (no restart needed).
- **Abuse**: Admin → Tableau de bord lists anomalous visitors / IP hashes; Admin → Réponses deletes
  by visitor or IP hash in bulk. The rate limiter is in memory (reset on restart).

## 6. Decisions (where the brief was silent)

1. **One process, one worker.** Rate limits are an in-memory sliding window (per visitor AND per IP
   hash), which is correct only with a single worker; they reset on restart. Login lockout is
   stored in SQLite and survives restarts.
2. **Selection weight** = `1 / (1 + votes)²` over active dishes the visitor hasn't answered or
   skipped. A visitor never gets their own submission during the hour after submitting it.
3. **Skips** ("Je ne connais pas ce plat") are rows in `votes` with a NULL category: they count for
   one-per-dish, never for rewards or distributions; the dish page shows them separately.
4. **Submissions are dishes** with `source='user'` and a status (`active`/`pending`/`flagged`/
   `rejected`/`merged`). Every submitter sees the same "Merci !" whatever the outcome, so a flagged
   submission is indistinguishable from an accepted one.
5. **Look-alikes**: normalised title (lowercase, accents, articles, simple plural stripped) +
   rapidfuzz `token_set_ratio ≥ 85` against active and pending dishes (not flagged ones, to avoid
   echoing insults back), top 5 ordered by score then plain ratio.
6. **Tokens**: vote token = `dish.issued_ms.HMAC(dish|visitor|issued)`, 10 min TTL, 600 ms minimum
   think time (admin-tunable), single use recorded in `used_tokens`. The proposal form token binds
   visitor + issue time, is single-use, enforces the 3 s minimum fill time and is also the
   proof-of-work challenge (SHA-256 with 16 leading zero bits by default).
7. **JavaScript**: the question loop works without JS (plain form POST, result shown on the next
   page); proposals require JS for the proof-of-work (a `<noscript>` notice says so).
8. **IP handling**: client IP = socket peer, or — only if the peer is a trusted proxy — the
   right-most `X-Forwarded-For` entry that is not itself a trusted proxy. Stored only as
   `HMAC-SHA256(ip, PLATS_IP_SALT)` truncated to 80 bits.
9. **Secrets** not given by env are generated once into `/data/.secrets/` (0600) so a fresh deploy
   works without extra steps and keys survive container re-creation.
10. **Admin**: one form (password + TOTP), TOTP ±1 step and never the same step twice (replay);
    lockout after 5 failures/15 min per IP hash (from the `rl_login` setting) plus a global brake
    at 50 failures/hour; session 8 h, cookie `Path=/admin; HttpOnly; Secure; SameSite=Strict`;
    CSRF token per session on every admin form; every admin action goes to the audit log
    (Admin → Journal). Login uses a separate double-submit CSRF cookie.
11. **Banned words**: seeded in the first migration (95 French + English slurs/insults), each
    "word" (whole word) or "substring". Matching also joins spaced single letters (`p.u.t.e`) and
    collapses repeats after joining. Deliberately not seeded: `con` (chili con carne), `bâtard`
    (pain bâtard), `cock`, `dick`.
12. **Importer**: the brief's SPARQL query; beverages excluded via a second query
    (`P31/P279* Q40050`, "drink") plus a description head-word heuristic (vin, liqueur, bière…).
    French description shorter than 40 chars ⇒ replaced by the first sentences of the frwiki
    summary (`--no-enrich` to skip). Re-runs update title/description/wiki URL only for dishes the
    admin never edited, and **never touch the status** (hidden stays hidden, merged stays merged).
    Images are **off by default** (`--images` / admin checkbox): Commons thumbnail ≤ 480 px stored
    in `/data/images/<QID>.<ext>`, skipped when Commons has no licence; author + licence shown
    under the image.
13. **Slugs** are fixed at creation (editing a title doesn't break links); merged dishes 301 to
    their target. Merge keeps the target's vote when a visitor answered both.
14. **Consensus metrics**: share of the leading category + Shannon entropy normalised by
    log(#categories). Rankings (most consensual/contested) need ≥ 3 answers.
15. **Distributions are public** on `/plat/<slug>` even before the visitor answered that dish
    (the game itself never shows the distribution before the answer).
16. **Rewards**: burst on every answer (size ∝ `1 + log2(1 + n/5)`, grows forever); milestones
    5/10/15/20, **SUPER !** at 25, then every 5 a new escalating word (GÉNIAL, FORMIDABLE, …
    then ×2, ×3…) and every 10 a new badge (Commis → Trois étoiles → MOF → … → "Gastronome
    cosmique ×n"); colour palettes unlock at 25 (néon), 50 (or), 100 (holographique),
    200 (cosmique). `prefers-reduced-motion` ⇒ no particles, colour pulse + text only. The theme
    hue drifts with every answer and is remembered in `localStorage` (cosmetic only).
17. **Per-day stats** are in UTC over the last 60 days.
18. **Export** includes anonymous visitor ids and IP hashes (admin-only download); every text cell
    is written with `write_string` and `strings_to_formulas/urls/numbers=False`.
19. **CSP** is `default-src 'self'` with no inline script or style; per-category colours come from a
    generated `/categories.css`, bar widths are set from `data-` attributes by the local JS.
20. **CI**: Trivy runs from its image pinned by digest (`aquasec/trivy:0.75.0`) rather than through
    `aquasecurity/trivy-action`; gate = CRITICAL with `--ignore-unfixed`. CI also boots the built
    image read-only, non-root, without capabilities, and runs `scripts/smoke.py` inside it.
    The owner's other repos (e.g. DrawTogether) were not readable from this session, so the
    workflows follow the brief rather than copying them.
21. **Dates/timezone**: timestamps stored as UTC epoch seconds; admin shows UTC.
22. **Rate-limit defaults**: votes `3/3s,300/1h`, questions via API `120/1m`, submissions
    `5/1h,20/1d`, look-alike checks `30/1m`, admin login `5/15m` (all in Admin → Réglages).

## 7. Known limits / left for later

- The Wikidata import was tested only against fixtures (the build sandbox had no access to
  Wikidata); the first real run is part of the deploy checklist above. If the SPARQL endpoint times
  out, simply re-run it (idempotent).
- Images are opt-in and untested against the live Commons API for the same reason.
- No automated browser test of the JS animations (logic is unit-tested server-side; the JS
  SHA-256 was checked against Python's `hashlib`).
