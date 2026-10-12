# Deploying plats.hosh.it

Hand-off for the on-box session. Split of work: **the owner prepares `plats.env`** (variables and
admin credentials, which never pass through a chat); **the on-box Claude runs the commands below and
nothing else.** Everything here was rehearsed on a simulated box (real Traefik, HTTPS, `deploy.sh`,
then the full browser suite through Traefik — see §6).

## 1. On-box runbook

### Step A — Claude: get the release (any directory; `/srv/plats` below)
```sh
mkdir -p /srv/plats && cd /srv/plats
if [ ! -d .git ]; then git init -q && git remote add origin https://github.com/pazka/plats; fi
git fetch -q --depth 1 origin tag v0.2.0 && git checkout -q v0.2.0
```
(The repository and the image `ghcr.io/pazka/plats` are public: no credentials needed. Works even if
`plats.env` was already put in the directory.)

### Step B — owner: prepare `plats.env` (Claude must not read or print it)
```sh
cd /srv/plats
install -m 600 .env.example plats.env      # then edit: PLATS_HOST, TRAEFIK_* (see §2)
bash scripts/set-admin.sh ./plats.env      # interactive: password ×2, scan the QR, type one code
```

### Step C — Claude: deploy, verify, report
```sh
cd /srv/plats && scripts/deploy.sh
```
`deploy.sh` is idempotent and never prints secrets. It does, and stops with an explicit message at
the first problem:
1. **preflight** — docker + compose v2; `plats.env` exists (forces mode 600); the admin hash
   (argon2id) and TOTP secret are present (checked, not printed); the Traefik network exists
   (lists the host's networks if not); compose config valid;
2. **pull + up** (`docker compose --env-file plats.env`, service `plats`);
3. **wait healthy**, then checks uid 10001 and the read-only root filesystem;
4. **Wikidata import** if the catalogue has < 100 dishes (~3 minutes, ≈985 dishes; fails below 500);
5. **smoke checks** inside the container, then **through Traefik** at `https://$PLATS_HOST`
   (+ HSTS header check);
6. **log report** of the last 15 minutes (errors, 5xx, security events).

Options: `--no-import`, `--images` (also download Commons thumbnails), `--skip-external`,
`--env-file FILE`. Exit code 0 = deployed and verified.

### Step D — Claude: watch the logs after going live
```sh
scripts/logs.sh follow        # live alerts (errors, 5xx, lockouts, CSRF failures, rate-limit bursts)
                              # + a summary every 5 min; Ctrl-C to stop
scripts/logs.sh report 24h    # one-shot summary
scripts/logs.sh check 1h      # same, exit 1 on any error/5xx (cron-able)
```
Then the owner, by hand (2 minutes): play 5 dishes on a phone, log into `/admin` with the TOTP,
download the export once.

If something fails: `docker compose --env-file plats.env logs --tail 100 plats`, fix the variable
that the error names, re-run `scripts/deploy.sh`.

## 2. Variables (`plats.env`, mode 600 — template `.env.example`)

One file feeds both docker compose (interpolation) and the container.

| Variable | Who | Default | Notes |
|---|---|---|---|
| `PLATS_IMAGE` | owner | `ghcr.io/pazka/plats:v0.2.0` | pin a release tag |
| `PLATS_HOST` | owner | `plats.hosh.it` | Traefik `Host()` rule and the external smoke check |
| `TRAEFIK_NETWORK` | owner | `traefik` | docker network Traefik is attached to (`docker network ls`) |
| `TRAEFIK_ENTRYPOINT` | owner | `websecure` | Traefik's HTTPS entrypoint name |
| `TRAEFIK_CERTRESOLVER` | owner | `letsencrypt` | ACME resolver name; **empty** = no resolver (file/wildcard certificate) |
| `PLATS_ADMIN_PASSWORD_HASH` | `set-admin.sh` | — | argon2id; empty ⇒ admin login disabled |
| `PLATS_ADMIN_TOTP_SECRET` | `set-admin.sh` | — | base32 |
| `PLATS_SECRET_KEY` | optional | generated in `/data/.secrets/` | signs cookies/tokens; changing it resets every visitor |
| `PLATS_IP_SALT` | optional | generated in `/data/.secrets/` | HMAC key of IP hashes |
| `PLATS_TRUSTED_PROXIES` | optional | `127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | `X-Forwarded-For` honoured only from these peers (Traefik) |
| `PLATS_COOKIE_SECURE` | optional | `1` | keep 1 behind HTTPS |
| `PLATS_CONTACT` | optional | GitHub link | shown on `/a-propos` |
| `PLATS_POW_DIFFICULTY` | optional | `16` | proposal proof-of-work bits (≈1 s on a phone) |
| `PLATS_LOG_CLIENT_IP` | optional | `0` | 1 = raw IP in stdout security lines (never in the DB) |

Single-quoted values are literal for compose (the `$` of the argon2 hash is safe); the app also
strips quotes itself if the file is used with `docker run --env-file`.

## 3. What runs

| Item | Value |
|---|---|
| Image | `ghcr.io/pazka/plats:v0.2.0` (`:latest`, `:0.2`; each `main` commit: `:ci`, `:<sha>`) |
| Process | uvicorn, 1 worker, **port 8000** (HTTP behind Traefik, not published) |
| User / FS | uid 10001, read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges` |
| Volume | **`/data`**: `plats.sqlite3` (+WAL), `.secrets/`, `images/` (optional) |
| Health | **`GET /healthz`** → `{"status":"ok","schema":2,"active_dishes":N,"categories":10}` (Docker healthcheck too); `HEAD` supported |
| Migrations | automatic at start |
| Logs | JSON lines on stdout, `ip_hash` only (no raw IP) |
| Outbound | only the importer: `query.wikidata.org`, `fr.wikipedia.org` (+ `commons.wikimedia.org`, `upload.wikimedia.org` with `--images`) |

## 4. Operations

- **Re-import / update the catalogue**: `docker compose --env-file plats.env exec plats python scripts/import_wikidata.py`
  or Admin → Plats → « Lancer l'import ». Idempotent; never un-hides a dish.
- **Backup**: `docker compose --env-file plats.env exec plats python -c "import sqlite3; sqlite3.connect('/data/plats.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"`
  then copy `/data/backup.sqlite3` and `/data/.secrets/` off the box. Admin → Export .xlsx too.
- **Upgrade**: set `PLATS_IMAGE` to the new tag, `scripts/deploy.sh`.
- **Admin password / TOTP reset**: owner runs `scripts/set-admin.sh plats.env`, then `scripts/deploy.sh`.
- **Tuning** (no restart): Admin → Réglages (rate limits, approval, PoW, anomaly thresholds).
- **Abuse**: Admin → Tableau de bord (anomalies) → Réponses → bulk delete by visitor / IP hash.

## 5. Decisions (where the brief was silent) — open to change

1. **One process, one worker.** Rate limits are an in-memory sliding window, checked per visitor
   AND per IP hash, which is correct only with a single worker; they reset on restart. Login lockout is
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
    image read-only, non-root, without capabilities, runs `scripts/smoke.py` inside it, then the
    Chromium e2e suite. Releases can be cut from the Actions tab (`release` → version) because the
    build sandbox could not push git tags; pushing a `v*` tag works the same.
    The owner's other repos (e.g. DrawTogether) were not readable from this session, so the
    workflows follow the brief rather than copying them.
21. **Dates/timezone**: timestamps stored as UTC epoch seconds; admin shows UTC.
22. **Rate-limit defaults** — per visitor: votes `3/3s,300/1h`, questions `120/1m`, submissions
    `5/1h,20/1d`, look-alike checks `30/1m`; **per IP hash, 10× looser** (`30/3s,3000/1h`,
    `1200/1m`, `20/1h,60/1d`, `300/1m`) because a whole office/school/mobile NAT shares one IP —
    found by the browser test; admin login `5/15m`. All editable in Admin → Réglages.
23. **One env file** (`plats.env`) feeds compose interpolation (image tag, host, Traefik network /
    entrypoint / resolver) and the app, so the owner edits a single file. HSTS comes from a
    Traefik middleware declared in the compose labels.
24. **Deployment is a script** (`scripts/deploy.sh`) so the on-box session runs one command with
    built-in verification; it imports the catalogue only when it is (nearly) empty.
25. **`HEAD` is answered like `GET`** (uptime monitors, `curl -I`) — found during the rehearsal.
26. **Logs monitoring** is a stdlib script run with the container's own Python
    (`scripts/logs.sh` → `monitor_logs.py`): no agent or extra service on the box.
27. **The import runs synchronously in `deploy.sh`** but in a background thread from the admin
    button (status shown on the dashboard).
28. **Import clean-up from the live run** (vetoable): French labels are capitalised ("coq au vin" →
    "Coq au vin"), trailing qualifiers dropped ("Liaison (cuisine)" → "Liaison"), and items whose
    description starts with *marque, entreprise, société, restaurant, enseigne, cultivar, race,
    ustensile, label…* are skipped (66 live: cheese brands, an olive cultivar, cattle breeds…).
    Cheeses, charcuterie, sauces and produce are **kept** — they are what "Ingrédient" is for.
    "McBaguette" and a few borderline items remain: hide them in the admin if unwanted.
29. **frwiki enrichment is paced** (~20 req/s, serial, honours `Retry-After` on 429/503); unpaced,
    Wikimedia throttled it to 53 successes out of ~800. A re-run never replaces a longer
    (e.g. enriched) description by a shorter one.
30. **10th category « Saucisse » 🌭** (owner request, v0.2.0), colour `#D55E00` (Okabe-Ito
    vermillion, distinct from the nine muted colours), placed last. Added by **migration 2**, so
    an existing database gets it on upgrade too (skipped if a `saucisse` category already exists).
    Keyboard: **1–9 and 0** answer (0 = 10th category), **P** = « Je ne connais pas » (was 0).

## 6. How it was verified

- **pytest**: 122 tests (tokens, one vote per dish, weighting, rate limits, normalisation, xlsx zero
  formula, admin auth/CSRF/lockout/TOTP replay, importer idempotency on fixtures…).
- **CI on every push**: tests → image → Trivy CRITICAL gate → container smoke → **browser e2e
  (Chromium, 111 checks: play loop, milestones 5/25/30, reduced motion, no-JS, mobile, dark mode,
  proposals with look-alikes and PoW, search/sort/stats, every admin screen, export, lockout) with
  the container logs monitored (no error, no 5xx, no raw IP)** → push to GHCR. Screenshots are kept
  as a CI artifact.
- **Rehearsal of the box**: Traefik v3.6 on a `traefik` network + `plats.env` prepared as above →
  `scripts/deploy.sh` (first run, re-run, variable change) → full browser suite **through Traefik
  over HTTPS** with production cookie settings: 111/111; client IP correctly taken from Traefik's
  `X-Forwarded-For`; HSTS from the Traefik middleware.
- **Live Wikidata** (workflow `wikidata-live`, real endpoints, hardened container): 1 229 items
  fetched → 178 drinks and 66 non-dishes skipped → **985 dishes** in ~3 min; 652 descriptions
  enriched from frwiki (all HTTP 200), 114 still under 40 characters (50 empty); with `--images`,
  78 thumbnails with licence. Re-run: 0 created, 0 changed. Smoke checks pass on that catalogue.

## 7. Known limits

- Images (`--images`) are opt-in: only ~78 dishes get one (Commons coverage + licence filter).
- ~50 dishes have no description at all (no frwiki article); the question then shows the title only.
- Rate limiting is in memory (single worker); it resets on restart.
