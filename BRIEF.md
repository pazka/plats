# plats.hosh.it — product brief

A **social experiment**: people tell us, one dish at a time, which *category* a French dish is
closest to. No accounts, no prizes — just a colourful, escalating "thank you" for every answer.
Owner: Alexandre (pazka). UI language: **French**. Mobile-first.

## 1. Categories (seed — admin-editable)
`Ingrédient`, `Pain`, `Purée`, `Soupe`, `Sandwich`, `Tarte`, `Gratin`, `Ragoût`, `Salade`.
"Ingrédient" = the dish is essentially one ingredient/product (a cheese, a sauce, a steak…).
Each category: slug, French name, colour (distinct, colour-blind-safe palette), emoji/icon,
display order, active flag. A category with votes can be deactivated/renamed, never hard-deleted.

## 2. The question loop (home page `/`)
- Shows ONE dish: title, short description (+ image if available, see §6), and the 9 category
  buttons. Plus **"Je ne connais pas ce plat"** (skip — recorded as a skip, not a vote, no reward).
- After answering: instant reward (§3), then a small "X % ont répondu comme toi" line with the
  current distribution for that dish, then the next question. Unlimited questions.
- Selection: random among active dishes the visitor hasn't answered/skipped, **weighted toward
  dishes with fewer votes** (so coverage grows evenly). When a visitor has seen everything:
  friendly end screen.
- One vote per (visitor, dish). Visitor = anonymous random id in a signed HttpOnly cookie
  (SameSite=Lax, Secure). No login.

## 3. Rewards (the "thanks")
- Every answer: a colourful burst (confetti/particles, own code — no CDN), the page's colour
  theme drifts further across the hue wheel, a progress meter fills.
- Milestones: small ones at 5, 10, 15, 20; **25 answers = "SUPER !"** — the big celebration;
  **beyond 25 = "GÉNIAL" tier**: rewards keep escalating (every 5 / 10 more: bigger effects,
  new titles/badges, rarer colours). It must never feel like it plateaus.
- Count persists in the visitor cookie/DB (survives reload). Respect `prefers-reduced-motion`
  (swap motion for colour/text). Never block the next question behind an animation.

## 4. Propose a dish (`/proposer`)
- Fields: **Nom du plat** (required, 2–80 chars); **Description** (required, 20–600 chars,
  placeholder guiding: what's in it, *how it's presented / served*); optional **Région/origine**.
  **No category choice** — the dish enters the random pool and gets classified by others
  (the submitter meets it later only by chance, never right after submitting).
- **Look-alike check:** while typing the name (debounced) and again on submit, the server finds
  similar existing titles (accent/case/article/plural-insensitive normalisation + fuzzy ratio,
  e.g. rapidfuzz `token_set_ratio ≥ 85`, also compare against pending submissions). If any:
  "C'est peut-être l'un de ceux-là ?" with their descriptions → "Oui, c'est celui-là" (no new
  dish) / "Non, c'est un autre plat" (then the description must make the difference clear).
- Moderation: banned-word filter (§7) hit → stored as `flagged`, not shown, appears in the admin
  queue (the submitter just sees "Merci !"). Admin setting `require_approval` (default **off**):
  when on, every submission waits for approval.

## 5. Antispam (no third-party captcha, no external calls)
- Every question is served with a **signed single-use token** (HMAC of dish id + visitor id +
  issue time, 10-min TTL); a vote without a valid unused token is rejected. Minimum think time
  (~600 ms) between issue and vote.
- Rate limits per visitor AND per IP (sliding window; e.g. votes 1/s burst, 300/h; submissions
  5/h, 20/day; similar-check 30/min). Tunables in admin settings.
- Proposal form: honeypot field + minimum fill time (≥3 s) + a small self-hosted proof-of-work
  (SHA-256 nonce, ~1 s on a phone) verified server-side.
- **IPs are never stored raw**: HMAC(ip, secret salt) only. Trust `X-Forwarded-For` only from
  Traefik. Admin sees anomaly hints (a visitor/IP-hash with abnormal rate) and can bulk-delete
  its votes. The box already runs CrowdSec at the edge — write JSON logs it could parse later.

## 6. Dish source — start from an external dataset
- **Wikidata (CC0)** via SPARQL, importer = CLI command + admin button, idempotent by QID
  (re-run = update, never duplicate; never resurrect an item the admin hid). Tested 2026-10-12:
  ```sparql
  SELECT ?item ?label ?desc ?img ?article WHERE {
    { ?item wdt:P495 wd:Q142 } UNION { ?item wdt:P2012 wd:Q6661 } UNION { ?item wdt:P361 wd:Q6661 }
    { ?item wdt:P31/wdt:P279* wd:Q2095 } UNION { ?item wdt:P279* wd:Q2095 }   # food
    ?item rdfs:label ?label FILTER(lang(?label)="fr")
    OPTIONAL { ?item schema:description ?desc FILTER(lang(?desc)="fr") }
    OPTIONAL { ?item wdt:P18 ?img }
    OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://fr.wikipedia.org/> }
  }
  ```
  ≈1 100–1 300 items (P31-only "dish" gave just 138 — too narrow). Exclude obvious non-dishes
  (beverages/wines/spirits subclasses). Send a descriptive User-Agent. Optional enrichment: the
  first sentences of the frwiki article (REST `page/summary`) as description when Wikidata's is
  empty or too short. Store `source` (wikidata / user / manual) + QID + frwiki URL.
- Images: only if downloaded at import time and **served locally** (no hotlinking — privacy + CSP),
  thumbnail ≤ 480 px, with author + licence attribution from Commons `extmetadata` shown under the
  image. Skip images that lack licence info. (Nice-to-have; text-only is acceptable for v1.)
- "We'll see how long that takes us": the stats page shows coverage progress (dishes with
  ≥1 / ≥5 / ≥20 votes, % of the catalogue).

## 7. Banned-word filter
- Admin-managed list (add/remove/import, per-word: substring vs whole-word). Matching on a
  normalised text (lowercase, accents stripped, basic leetspeak map 0→o 1→i 3→e 4→a @→a $→s,
  repeated letters collapsed). Applied to title + description + region of submissions.
- Seed with a reasonable French + English list of slurs/insults (kept in the DB, not in templates).
- Admin "test a text" box + "rescan existing submissions" action.

## 8. Public pages
- `/plats` — **search** (accent-insensitive, SQLite FTS5 or equivalent) + browse/sort (most
  voted, most consensual, most contested, newest).
- `/plat/<slug>` — dish card + **vote distribution** across categories (bars in category colours,
  counts + %), total votes, consensus/contestation indicator (e.g. top share & entropy), source link.
- `/stats` — totals (answers, participants, dishes, coverage), per-category leaderboard, most
  consensual / most contested dishes, answers per day. No per-person data, ever.
- `/a-propos` — what the experiment is, what's stored (anonymous cookie id, hashed IP, answers),
  no trackers, contact. The cookie is strictly functional → no consent banner needed.

## 9. Admin panel (`/admin`)
- Auth: single admin, **password (argon2id hash) + TOTP**, both set by the owner with an
  interactive `scripts/set-admin.sh` run on the box (secrets never pass through the agent chat);
  session cookie, CSRF on every form, login rate limit + lockout, audit log of admin actions.
- Dashboard: totals, last 24 h activity, pending/flagged queue size, anomaly hints.
- **Dishes**: list/filter (source, status, votes, flagged), edit, hide/activate, bulk actions,
  **merge duplicates** (moves votes; one vote per visitor kept), run Wikidata import.
- **Submissions** queue: approve / reject / edit / merge into existing.
- **Answers/votes**: browse + filter (dish, category, visitor, IP-hash, date), delete, bulk delete
  by visitor or IP-hash.
- **Categories**, **Banned words**, **Settings** (require_approval, rate limits).
- **Export → Excel (.xlsx)** at any time: sheets dishes, votes, categories, per-dish stats,
  submissions, banned words. **Formula-injection safe**: every user-originated value is written as
  a *string cell* (e.g. xlsxwriter `strings_to_formulas=False, strings_to_urls=False,
  strings_to_numbers=False` + `write_string`), so `=HYPERLINK(...)`, `+cmd`, `-1+1`, `@SUM` stay
  literal text. A test must load the exported file and assert zero formula cells.

## 10. Non-functional
- Stack (keep it simple, one container): **Python 3.12 + FastAPI + Jinja2 templates + SQLite
  (WAL) + vanilla JS/CSS**. No CDN, no external fonts, no analytics. Strict CSP (no inline JS
  unless nonce'd), security headers, HSTS handled by Traefik.
- Container: non-root uid, read-only rootfs + `/data` volume (SQLite + cached images),
  `no-new-privileges`, `cap_drop: [ALL]`, healthcheck. DB migrations on start.
- Tests (pytest): vote token single-use + TTL, one-vote-per-dish, weighting, rate limits,
  look-alike normalisation, banned-word normalisation, xlsx zero-formula, admin auth/CSRF/lockout,
  importer idempotency (fixture, no live network in tests).
- Accessibility: keyboard-playable (1–9 keys answer), proper contrast, focus states.
