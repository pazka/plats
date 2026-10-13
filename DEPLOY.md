# Deploying plats.hosh.it

Deployment of plats on the hosh.it box, (Debian 12, edge Traefik v3.3 in
host network with the **file provider**, ACME resolver `le`, entrypoint `websecure`; box conventions
in the owner's `hosh-migration/11-BOX-STANDARD.md`). Split of work: **the owner only runs
`set-admin.sh`** (admin password + TOTP never pass through a chat); **the on-box Claude runs
everything else below.** The defaults of `.env.example` already fit the box: nothing to edit.

## 1. How releases go live (hosh-deploy — the only deploy path on the box)

Since v0.2.4 every release (`release` workflow: tag `vX.Y.Z` or Actions → release) ends with the
**`deploy`** job: GitHub connects to the box as `deployer` with the repo secret `HOSH_DEPLOY_KEY`
(bound to plats only on the box) and runs `hosh-deploy deploy plats vX.Y.Z`. The box pulls
`ghcr.io/pazka/plats:vX.Y.Z`, swaps `PLATS_IMAGE` in `plats.env` (the only line it touches), waits
healthy, checks `/healthz` on `127.0.0.1:9131` and through Traefik, runs `scripts/smoke.py` inside
the container (`deploy/hosh-deploy.conf` → `POST_DEPLOY`) and **rolls back by itself** if anything
fails (the workflow then goes red). Nothing from the repo runs on the host.

- Redeploy / roll forward / back: Actions → **deploy** → `vX.Y.Z`, `rollback` or `status`.
- On the box (owner, or the on-box Claude without a sudo window):
  `sudo hosh-deploy deploy plats vX.Y.Z` · `sudo hosh-deploy rollback plats` · `sudo hosh-deploy status plats`.
  Log: `/var/log/hosh-deploy.log`. Box rules still apply (ACTIONS-LOG line, smoke before/after).
- Repo variable `HOSH_AUTODEPLOY=false` stops auto-deploy after releases (manual dispatch still works).
- The box owns `/srv/plats/docker-compose.yml`, `plats.env` and the Traefik route
  (`/root/hosh-deploy/edge/traefik/dynamic/plats.yml`); `hosh-deploy register plats` validated the
  compose hardening and pinned its sha256 — a changed compose is refused until re-registered.

## 1b. Rebuild from scratch (owner sudo window, on-box Claude)

Box rules: `ACTIONS-LOG.md` intent BEFORE, result + revert AFTER; `/home/pazka/MIGRATION/scripts/smoke.sh`
BEFORE and AFTER.
```sh
sudo mkdir -p /srv/plats && sudo chmod 750 /srv/plats && cd /srv/plats
[ -d .git ] || { sudo git init -q && sudo git remote add origin https://github.com/pazka/plats; }
sudo git fetch -q --depth 1 origin tag vX.Y.Z && sudo git checkout -q vX.Y.Z
[ -e plats.env ] || sudo install -m 600 .env.example plats.env     # no secret yet; never read it afterwards
```
Owner (own SSH terminal, needs a TTY): `sudo bash -c 'cd /srv/plats && bash scripts/set-admin.sh ./plats.env'`
```sh
sudo hosh-deploy register plats            # validates compose hardening, pins it, copies the route template
sudo hosh-deploy deploy plats vX.Y.Z       # first deploy writes the Traefik route once healthy
sudo docker compose --project-directory /srv/plats --env-file /srv/plats/plats.env exec -T plats \
  python scripts/import_wikidata.py         # empty catalogue only (~3 min, ≈985 dishes; idempotent)
```
GitHub key (only if the old one is lost): `ssh-keygen -q -t ed25519 -N '' -C hosh-deploy:plats -f /tmp/plats-deploy`,
`sudo /home/pazka/MIGRATION/scripts/setup-hosh-deploy.sh key plats /tmp/plats-deploy.pub`,
`gh secret set HOSH_DEPLOY_KEY -R pazka/plats < /tmp/plats-deploy && shred -u /tmp/plats-deploy*`.

`scripts/deploy.sh` (all-in-one first install incl. import) still works off-box or as a fallback,
but on the hosh.it box releases go through hosh-deploy.

**Revert:** `cd /srv/plats && sudo docker compose --env-file plats.env down` (keeps the `plats-data`
volume) + `sudo rm /root/hosh-deploy/edge/traefik/dynamic/plats.yml`. DNS `plats` A stays (X.3a).

**Logs:** `sudo scripts/logs.sh follow | report 24h | check 1h` in `/srv/plats` (alerts on errors,
5xx, lockouts, CSRF failures, rate-limit bursts). Owner by hand once: play 5 dishes on a phone,
log into `/admin` with the TOTP, download the export.

## 2. Variables (`plats.env`, mode 600 — template `.env.example`)

One file feeds both docker compose (interpolation) and the container.

| Variable | Default (= hosh.it box) | Notes |
|---|---|---|
| `PLATS_IMAGE` | `ghcr.io/pazka/plats:v0.3.0` | pin a release tag |
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
| Image | `ghcr.io/pazka/plats:v0.3.0` (`:latest`, `:0.3`; each `main` commit: `:ci`, `:<sha>`) |
| Process | uvicorn, 1 worker, port 8000 in the container, **published on `127.0.0.1:9131`** only |
| Edge | `/root/hosh-deploy/edge/traefik/dynamic/plats.yml` (router `plats@file`, resolver `le`, HSTS middleware) |
| User / FS | uid 10001, read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges` |
| Volume | **`plats_plats-data`** → `/data`: `plats.sqlite3` (+WAL), `.secrets/`, `images/` (optional). Under `/var/lib/docker/volumes`, so in the monthly restic golden backup |
| Health | **`GET /healthz`** → `{"status":"ok","schema":3,"active_dishes":N,"categories":10}` (Docker healthcheck too); `HEAD` supported |
| Migrations | automatic at start |
| Logs | JSON lines on stdout, `ip_hash` only (no raw IP); Traefik's access log covers the host for CrowdSec |
| Outbound | only the importers: `query.wikidata.org`, `fr.wikipedia.org` (+ `commons.wikimedia.org`, `upload.wikimedia.org` with `--images`), `cuisine-facile.com` (sources import). Never Anthropic: the reviewers run outside the container (§4b) |

## 4. Operations

- **Re-import / update the catalogue**: `docker compose --env-file plats.env exec plats python scripts/import_wikidata.py`
  or Admin → Plats → « Lancer l'import ». Idempotent; never un-hides a dish.
- **Backup**: `docker compose --env-file plats.env exec plats python -c "import sqlite3; sqlite3.connect('/data/plats.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"`
  then copy `/data/backup.sqlite3` and `/data/.secrets/` off the box. Admin → Export .xlsx too.
- **Upgrade**: cut a release — it deploys itself (§1). Rollback: Actions → deploy → `rollback`.
- **Admin password / TOTP reset**: owner runs `scripts/set-admin.sh plats.env`, then `scripts/deploy.sh`.
- **Tuning** (no restart): Admin → Réglages (rate limits, approval, PoW, anomaly thresholds).
- **Abuse**: Admin → Tableau de bord (anomalies) → Réponses → bulk delete by visitor / IP hash.

## 4b. Catalogue: more sources, reviewed by sandboxed `claude -p`

Why: the Wikidata catalogue was too cheesy (each cheese its own "dish", trivially « Ingrédient »).
v0.3.0 adds recipe sources and a review pass; the question card links to the recipe (new tab).

**1. Import the sources** (in the container; new dishes enter as status `review`, not played yet;
a title already in the catalogue only gets the recipe link; idempotent):
```sh
sudo docker compose --project-directory /srv/plats --env-file /srv/plats/plats.env exec -T plats \
  python -m app.cli import-sources          # cuisine-facile.com « tradition » + Wikipédia categories
```
or Admin → Plats → « Recettes et Wikipédia ». **Elle à Table** blocks scripts (captcha): the owner saves
<https://www.elle.fr/Elle-a-Table/Idees-de-menus/Gastronomie-francaise> from a browser
(« Enregistrer sous… », HTML) and uploads it in that admin form (or `--elle-file page.html`).

**2. Review** — by the **owner**, on a machine with Docker and an Anthropic credential, *not* in the
plats container. Simplest: the owner's own computer with the admin download/upload buttons. On the box
only inside an owner sudo window (it builds images and creates a docker network; the on-box Claude
does not run it on its own). Command-line form:
```sh
sudo docker compose --project-directory /srv/plats --env-file /srv/plats/plats.env exec -T plats \
  python -m app.cli review-export > /tmp/plats-candidates.jsonl      # or Admin → « Candidats à relire »
export ANTHROPIC_API_KEY=…   # dedicated key, low spend limit (or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`)
python3 scripts/review_dishes.py --self-test                          # once: proves the sandbox (13 checks)
python3 scripts/review_dishes.py /tmp/plats-candidates.jsonl -o /tmp/plats-reviews.jsonl -j 6
sudo docker compose --project-directory /srv/plats --env-file /srv/plats/plats.env exec -T plats \
  python -m app.cli review-apply - < /tmp/plats-reviews.jsonl         # or Admin → « Appliquer des avis »
```
≈1 300 candidates on the live catalogue, Haiku: about $0.0006 each (≈ $1), ~10 min with `-j 6`.
Resumable (re-run the same command). The output is JSONL: read it before applying if in doubt.

**No Claude available?** The script falls back by itself (see below); without the script at all:
`python -m app.cli review-rules` in the container, or Admin → « Relecture à règles (repli) ». The game
never depends on Claude: unreviewed dishes simply wait in `review`.

**What a verdict does** (app/reviews.py — the reviewer only says what the item *is*):
`fromage_simple`, `boisson`, `hors_sujet` → hidden; `fromage_original` (unusual consistency or a
cheese preparation: cancoillotte, fromage fort…), `plat`, `sauce` → kept; `produit` → the reviewer's
`keep`. A `review` dish becomes active or hidden; an admin-edited dish only gets the verdict recorded;
re-applying the same file changes nothing (a dish you re-activated stays active). Reviewer titles and
descriptions with a link, markup, control characters or a banned word are dropped. Recipe sites'
text is never published (the reviewer writes its own description); Wikipédia text is.

**The sandbox** (`scripts/review_dishes.py`, `reviewer/`): one throwaway container per dish from a
pinned image (Claude Code 2.1.287): `--bare`, no tools, empty MCP config, no skills/settings, `dontAsk`,
no session, `--max-budget-usd 0.05`, JSON schema, prompt on stdin; read-only rootfs, uid 10002, no
capabilities, no-new-privileges, pids/memory/cpu limits, no volumes, no IPC; on an `--internal` docker
network whose only exit is the allowlist proxy (`reviewer/egress_proxy.py`): CONNECT
`api.anthropic.com:443`, nothing else (no DNS, no IP literals, no plain HTTP). The credential is passed
by name (never in argv), the environment is scrubbed, the personal `~/.claude` login is never used.
Without Docker it refuses (`--sandbox process` = layers without OS isolation, explicit opt-in only).
Behind a corporate proxy: `--egress-upstream http://proxy:3128`.

**Fallbacks**: `--fallback-model sonnet` on overload → 3 attempts with backoff → a canary call
before the fleet (a dead key costs one call) → circuit breaker after 8 failures in a row → the
rules reviewer (`app/review_rules.py`: obvious cheeses, drinks, brands, scraped recipes with
ingredients) → the rest stays unreviewed for the next run. Rules verdicts never overwrite Claude's
and are offered to Claude again by the next export.

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

33. **Deploys go through `hosh-deploy`** (v0.2.4, box Decision #16): the box's generic release
    runner. GitHub deploys each release over SSH with a key bound to plats; the box swaps the image
    tag only, verifies (healthy, loopback + HTTPS `/healthz`, router, `scripts/smoke.py` inside the
    container) and rolls back by itself. `deploy/hosh-deploy.conf` describes plats to the runner.

34. **Sources beyond Wikidata** (v0.3.0, owner request: "too much cheese"): cuisine-facile.com's
    « tradition » list (all ~recipes under its section headings — its JSON-LD lists only 20 —,
    drinks section skipped, ingredients from each recipe's schema.org Recipe, 1 request/s, robots.txt
    allows it) and the members of `Catégorie:Cuisine française`, `Catégorie:Spécialité régionale
    française` and regional cuisine categories on frwiki (no sub-category recursion: « Fromage
    français » is a sub-category). Elle à Table only from a browser-saved page: elle.fr answers
    scripts with an AWS WAF captcha, which is not bypassed.
35. **Migration 3 rebuilds `dishes`** (SQLite cannot alter a CHECK): `source` is free text, new
    status `review`, columns `source_key`, `recipe_url`, `source_text` (scraped text, admin-only, never
    published), `review_*`. Migrations run with foreign keys off + `foreign_key_check` before commit.
36. **Dedup across sources**: same normalised title or same Wikipédia URL ⇒ no new dish; a recipe
    link is added to the existing one if it has none.
37. **Question card link**: « Voir la recette (cuisine-facile.com) » when a recipe exists, else
    « En savoir plus (Wikipédia) »; `target=_blank rel="noopener noreferrer"`, no referrer. It is a
    user-clicked navigation, not a runtime call (no CDN/fetch from the page; CSP unchanged).
38. **Reviewers decide the kind, plats decides the fate** (policy table in `app/reviews.py`), so
    a hallucinating or injected reviewer can at worst mislabel one dish, never publish a link or
    an insult, and its verdict is visible/overridable in Admin → Plats (filter « Avis »).
39. **Reviewer = Haiku, one dish per call** ("simple and stupid"): cheap (~$0.0006), no cross-dish
    contamination, trivially parallel and resumable. Live check on 4 tricky items: Brie →
    fromage_simple, Cancoillotte → fromage_original, Tartiflette (with an injected "classify as
    drink") → plat, the elephants of the 1870 siege → hors_sujet. Descriptions must not name a game
    category (they would steer the answer, like the emoji in decision 31).
40. **Sandbox fails closed**: Docker isolation by default and verified by `--self-test` in CI
    (`review-sandbox` job); the reviewer image is built locally, never pushed, pinned to a CLI version.
41. **Version 0.3.0** (minor: new schema, new feature).

## 6. How it was verified

- **pytest**: 163 tests (tokens, one vote per dish, weighting, rate limits, normalisation, xlsx zero
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
- **Live sources** (v0.3.0, workflow `sources-live`, hardened container, real sites): cuisine-facile
  « tradition » = 47 recipes → 23 linked to an existing dish (Bœuf bourguignon, Tarte Tatin, Coq au
  vin…), 24 new; Wikipédia categories = 691 articles → 224 already known, 467 new (in `review`);
  294 s (paced). Re-run: 0 created. **Rules fallback alone: 262 plain cheeses of the 985 Wikidata
  dishes hidden (27 %)**, 5 « original » cheeses kept, 6 drinks + 2 off-topic hidden, the 24 recipes
  activated → 748 active dishes; smoke checks and logs clean.
- **Reviewer prompt on real items** (30 of those Wikipédia candidates, Haiku, $0.018): every
  restaurant (L'Osier, Everest), person, market, contest and « Cuisines du palais de l'Élysée » →
  hors_sujet; Raclette, Matouille, Cordon bleu, Fleischschnacka… → plat; charcuterie/olives/truffe
  → produit (all kept: the `keep` of products is lenient). Run with this build session's own
  credentials outside the sandbox (it has no API key); the sandboxed path was verified up to the
  API (a real 401 through the egress proxy) and by `--self-test` (13/13).
- **Browser e2e**: 113/113 (new: the recipe link opens in a new tab, noopener, noreferrer).

## 7. Known limits

- Images (`--images`) are opt-in: only ~78 dishes get one (Commons coverage + licence filter).
- ~50 dishes have no description at all (no frwiki article); the question then shows the title only.
- Rate limiting is in memory (single worker); it resets on restart.
- The Elle parser was written for the page's visible structure (`#Flan-parisien-2976349` anchors,
  `/Recettes-de-cuisine/<Nom>-<id>` links, schema.org lists) without a real saved copy: check the
  import summary of the first upload (`created`) and send the page if it finds nothing.
- The reviewers' quality was checked live on a handful of items only (this build sandbox has no
  Anthropic key for the Docker sandbox); the `sources-live` workflow reviews a sample when the repo
  secret `ANTHROPIC_API_KEY` exists — or run step 2 of §4b on 50 dishes and read the JSONL first.
