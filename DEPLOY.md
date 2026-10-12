# Deploying plats.hosh.it

Hand-off for the on-box session, **written for the hosh.it box** (Debian 12, edge Traefik v3.3 in
host network with the **file provider**, ACME resolver `le`, entrypoint `websecure`; box conventions
in the owner's `hosh-migration/11-BOX-STANDARD.md`). Split of work: **the owner only runs
`set-admin.sh`** (admin password + TOTP never pass through a chat); **the on-box Claude runs
everything else below.** The defaults of `.env.example` already fit the box: nothing to edit.

## 1. On-box runbook (root needed: a `claude-sudo-grant` window opened by the owner)

Box rules apply: an `ACTIONS-LOG.md` intent line BEFORE, result + revert AFTER; full smoke
(`/home/pazka/MIGRATION/scripts/smoke.sh`) BEFORE and AFTER.

### Step A — Claude: get the release into `/srv/plats`
```sh
sudo mkdir -p /srv/plats && sudo chmod 750 /srv/plats && cd /srv/plats
[ -d .git ] || { sudo git init -q && sudo git remote add origin https://github.com/pazka/plats; }
sudo git fetch -q --depth 1 origin tag v0.2.3 && sudo git checkout -q v0.2.3
```
(Repository and image `ghcr.io/pazka/plats` are public: no credentials needed.)

### Step B — Claude: create `plats.env` from the template (no secret in it yet)
```sh
cd /srv/plats && [ -e plats.env ] || sudo install -m 600 .env.example plats.env
```
From here on **never read, print or edit `plats.env`** (it will hold the admin secrets).

### Step C — owner: admin password + TOTP (interactive, in the owner's terminal)
```sh
sudo bash -c 'cd /srv/plats && bash scripts/set-admin.sh ./plats.env'   # password ×2, scan the QR, type one code
```

### Step D — Claude: deploy, verify, report
```sh
cd /srv/plats && sudo scripts/deploy.sh
```
`deploy.sh` is idempotent and never prints secrets. It does, and stops with an explicit message at
the first problem:
1. **preflight** — docker + compose v2; `plats.env` exists (forces mode 600); admin hash (argon2id)
   and TOTP secret present (checked, not printed); `PLATS_BIND` is loopback and not taken by another
   container; compose config valid; the Traefik route is rendered from `deploy/traefik-route.yml.tmpl`;
2. **pull + up** (`docker compose --env-file plats.env`, service `plats`, published on `127.0.0.1:9131`);
3. **wait healthy**, checks uid 10001 and the read-only root filesystem, `http://127.0.0.1:9131/healthz`
   from the host, then **writes `$TRAEFIK_DYNAMIC_DIR/plats.yml`** (only if changed; the previous one
   is kept as `.plats.yml.prev`) and confirms through the Traefik API (`127.0.0.1:8899`) that router
   `plats@file` is `enabled`;
4. **Wikidata import** if the catalogue has < 100 dishes (~3 minutes, ≈985 dishes; fails below 500);
5. **smoke checks** inside the container, then **through Traefik** at `https://$PLATS_HOST`
   (retried while the new certificate is issued) + HSTS header check;
6. **log report** of the last 15 minutes (errors, 5xx, security events).

Options: `--no-import`, `--images` (also download Commons thumbnails), `--skip-external`,
`--env-file FILE`. Exit code 0 = deployed and verified. Then run the box smoke again
(`scripts/smoke.sh plats tls edge` at least, `all` for the AFTER record).

**Revert:** `sudo docker compose --env-file plats.env down` in `/srv/plats` (keeps the `plats-data`
volume) + `sudo rm /root/hosh-deploy/edge/traefik/dynamic/plats.yml` (hot-reload). The DNS record
`plats` A 54.37.70.57 stays (X.3a).

### Step E — Claude: watch the logs after going live
```sh
sudo scripts/logs.sh follow       # live alerts (errors, 5xx, lockouts, CSRF failures, rate-limit bursts)
                                  # + a summary every 5 min; Ctrl-C to stop
sudo scripts/logs.sh report 24h   # one-shot summary
sudo scripts/logs.sh check 1h     # same, exit 1 on any error/5xx (cron-able)
```
Then the owner, by hand (2 minutes): play 5 dishes on a phone, log into `/admin` with the TOTP,
download the export once.

If something fails: `sudo docker compose --env-file plats.env logs --tail 100 plats`, fix the
variable that the error names (owner, if it is an admin value), re-run `scripts/deploy.sh`.

**Upgrade:** `sudo git fetch -q --depth 1 origin tag vX.Y.Z && sudo git checkout -q vX.Y.Z`, set
`PLATS_IMAGE` to the same tag (the owner, or `sudo sed -i "s|^PLATS_IMAGE=.*|PLATS_IMAGE='ghcr.io/pazka/plats:vX.Y.Z'|" plats.env`
— touches only that line), `sudo scripts/deploy.sh`. Rollback = same with the previous tag.

## 2. Variables (`plats.env`, mode 600 — template `.env.example`)

One file feeds both docker compose (interpolation) and the container.

| Variable | Default (= hosh.it box) | Notes |
|---|---|---|
| `PLATS_IMAGE` | `ghcr.io/pazka/plats:v0.2.3` | pin a release tag |
| `PLATS_HOST` | `plats.hosh.it` | Traefik `Host()` rule and the external smoke check |
| `PLATS_ROUTING` | `file` | `file` = loopback port + Traefik file-provider route; `labels` = `docker-compose.labels.yml` (docker provider, Traefik ≥ v3.7) |
| `PLATS_BIND` | `127.0.0.1:9131` | loopback `host:port` published for Traefik (file mode); must be loopback |
| `TRAEFIK_DYNAMIC_DIR` | `/root/hosh-deploy/edge/traefik/dynamic` | where deploy.sh writes `plats.yml` (file mode); empty = print it instead |
| `TRAEFIK_API` | `http://127.0.0.1:8899` | read-only Traefik API, to confirm the router is enabled; empty = skip |
| `TRAEFIK_ENTRYPOINT` | `websecure` | Traefik's HTTPS entrypoint |
| `TRAEFIK_CERTRESOLVER` | `le` | ACME resolver; **empty** = no resolver (file/wildcard certificate) |
| `TRAEFIK_NETWORK` | `traefik` | labels mode only: docker network Traefik is attached to |
| `PLATS_ADMIN_PASSWORD_HASH` | written by `set-admin.sh` | argon2id; empty ⇒ admin login disabled |
| `PLATS_ADMIN_TOTP_SECRET` | written by `set-admin.sh` | base32 |
| `PLATS_SECRET_KEY` | generated in `/data/.secrets/` | signs cookies/tokens; changing it resets every visitor |
| `PLATS_IP_SALT` | generated in `/data/.secrets/` | HMAC key of IP hashes |
| `PLATS_TRUSTED_PROXIES` | `127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | `X-Forwarded-For` honoured only from these peers. File mode: Traefik reaches the container through docker's port proxy, so the peer is the compose network gateway (172.x) — covered |
| `PLATS_COOKIE_SECURE` | `1` | keep 1 behind HTTPS |
| `PLATS_CONTACT` | GitHub link | shown on `/a-propos` |
| `PLATS_POW_DIFFICULTY` | `16` | proposal proof-of-work bits (≈1 s on a phone) |
| `PLATS_LOG_CLIENT_IP` | `0` | 1 = raw IP in stdout security lines (never in the DB) |

Single-quoted values are literal for compose (the `$` of the argon2 hash is safe); the app also
strips quotes itself if the file is used with `docker run --env-file`.

## 3. What runs

| Item | Value |
|---|---|
| Image | `ghcr.io/pazka/plats:v0.2.3` (`:latest`, `:0.2`; each `main` commit: `:ci`, `:<sha>`) |
| Process | uvicorn, 1 worker, port 8000 in the container, **published on `127.0.0.1:9131`** only |
| Edge | `/root/hosh-deploy/edge/traefik/dynamic/plats.yml` (router `plats@file`, resolver `le`, HSTS middleware) |
| User / FS | uid 10001, read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges` |
| Volume | **`plats_plats-data`** → `/data`: `plats.sqlite3` (+WAL), `.secrets/`, `images/` (optional). Under `/var/lib/docker/volumes`, so in the monthly restic golden backup |
| Health | **`GET /healthz`** → `{"status":"ok","schema":2,"active_dishes":N,"categories":10}` (Docker healthcheck too); `HEAD` supported |
| Migrations | automatic at start |
| Logs | JSON lines on stdout, `ip_hash` only (no raw IP); Traefik's access log covers the host for CrowdSec |
| Outbound | only the importer: `query.wikidata.org`, `fr.wikipedia.org` (+ `commons.wikimedia.org`, `upload.wikimedia.org` with `--images`) |

## 4. Operations

- **Re-import / update the catalogue**: `docker compose --env-file plats.env exec plats python scripts/import_wikidata.py`
  or Admin → Plats → « Lancer l'import ». Idempotent; never un-hides a dish.
- **Backup**: `docker compose --env-file plats.env exec plats python -c "import sqlite3; sqlite3.connect('/data/plats.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"`
  then copy `/data/backup.sqlite3` and `/data/.secrets/` off the box. Admin → Export .xlsx too.
- **Upgrade**: see §1 « Upgrade » (checkout the tag, set `PLATS_IMAGE`, `scripts/deploy.sh`).
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
31. **No emoji on the answer buttons** (owner request, v0.2.1): a picture steers the answer, so
    the buttons show the category name (and its colour) only. Emoji still appear *after* the
    answer (result line, dish pages, stats) and stay editable in Admin → Catégories.

32. **hosh.it box routing = file provider** (v0.2.2): the box's edge Traefik is v3.3 in host
    network with the file provider (its docker provider needs ≥ v3.7), so the default is
    `PLATS_ROUTING=file` — container published on loopback `127.0.0.1:9131`, `deploy.sh` renders
    `deploy/traefik-route.yml.tmpl` into the edge's dynamic directory after the container is healthy
    and checks the router through the Traefik API. The label setup is kept as
    `docker-compose.labels.yml` (`PLATS_ROUTING=labels`) for when the edge moves to the docker provider.

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
