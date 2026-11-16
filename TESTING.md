# Testing

The suite is the safety net for upgrades (Python, FastAPI/Starlette, argon2-cffi, pyotp, base image,
Actions): a Dependabot PR is mergeable on a green `ci` run alone.

## Run it

In the image's base family, so nothing runs on the host:

```bash
for v in 3.12 3.14; do
  docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/app:ro -w /app -v plats_venv$v:/venv \
    python:$v-slim-trixie sh -c "[ -x /venv/bin/python ] || python -m venv /venv;
      /venv/bin/pip install -q -r requirements-dev.txt && /venv/bin/python -m pytest -p no:cacheprovider"
done
```

(Windows Git Bash: prefix with `MSYS_NO_PATHCONV=1`, mount `"$(cygpath -w "$PWD")"`.)

Image on the next Python before Dependabot proposes it:

```bash
sed 's/python:3.12-slim-trixie/python:3.14-slim-trixie/g' Dockerfile | docker build -f - -t plats:next .
```

then run the `Container smoke test` step of `ci.yml` against `plats:next`.

## CI (`.github/workflows/ci.yml`)

PRs, `main` and `agent/**` branches. `test (python 3.12|3.13|3.14)` runs pytest inside
`python:<v>-slim-trixie`. `image`: JS syntax check, build, Trivy CRITICAL gate, hardened container smoke
(`scripts/smoke.py`, uid 10001, read-only rootfs), browser e2e. Only `main` pushes to ghcr.

## What is covered

Existing suites: admin auth (argon2id + TOTP, replay, lockout, CSRF, session expiry), votes and tokens,
proposer anti-abuse, rate limits, selection, text/moderation, export (no formula cells), importer against
recorded Wikidata/frwiki fixtures (no network), security headers and **no external resources in pages**,
migrations 1→2.

`tests/test_upgrade.py` adds:
- every admin route (GET and POST) → 303 to `/admin/login` without a session;
- every public route → pinned status + content type; `/api/question` payload shape;
- fresh-DB schema snapshot (`tests/fixtures/schema.json`), `user_version` 2, WAL, foreign keys,
  idempotent migrate;
- an admin hash made before the upgrade still verifies (else the owner is locked out); new hashes argon2id;
- TOTP with a fixed secret and time gives the same code, accepted once.

Regenerate the schema snapshot only together with a new migration:
`UPDATE_SCHEMA_SNAPSHOT=1 python -m pytest tests/test_upgrade.py`.

## Deliberately not covered

- Live Wikidata/Wikimedia (`wikidata-live.yml`, manual dispatch only).
- Real `set-admin.sh` (interactive, owner-only).

## Upgrades checked

- **Python 3.12 → 3.14** (2026-10-19): 178/178 tests on 3.12.15, 3.13.16, 3.14.8; image rebuilt on
  `python:3.14-slim-trixie` healthy, uid 10001, `scripts/smoke.py` all checks passed → go.
- Known upcoming break: Starlette warns that `TestClient` over `httpx` is deprecated (`httpx2`). When a
  Starlette bump removes it, the test client import is what fails, not the app.

## Visual use cases

Owned by the QA agent (hosh-management `AGENTS.md` §3). Every page and every state a visitor or the
admin can reach. Each one is shot at **desktop 1400, laptop 1024 and phone 390** wide (full page), and in
light mode, plus dark mode where marked. Data: a fresh container seeded with `e2e/seed.py` (45 dishes, no
votes, default categories), `PLATS_POW_DIFFICULTY=12`, outbound HTTP to a dead proxy. Live-site checks after a
deploy use only the read-only cases marked **live**.

Determinism the harness must provide, because a diff should only mean a real change: the server picks the
next dish at random (`services.next_question`), so the harness answers the same fixed sequence of dishes by
slug and masks the dish card where it cannot. `/stats` "par jour" depends on today's date, so mask that
block. Turn animations off (`prefers-reduced-motion: reduce`, already honoured by `style.css`).

| # | Use case | Steps to reach it | Notes |
|---|---|---|---|
| P1 | Play: first question | `GET /` with a fresh visitor cookie | dish card, category buttons with `kbd` hints (hidden on touch), skip; **live**; dark |
| P2 | Play: after an answer | P1, click a category (also the no-JS `POST /voter` path) | `?apres=<slug>`: "x % ont répondu comme toi" panel + distribution bars, next dish |
| P3 | Play: reward reached | answer 5 dishes (`SMALL` milestone "Bien joué !"), 25 ("SUPER !", palette `neon`), 30 (first badge "🥄 Commis de cuisine"; 45 seeded dishes are enough), see `app/rewards.py` | reward rendered by `static/app.js`; animations off |
| P4 | Play: no question left | visitor who has answered or skipped every active dish (or an empty catalogue) | `#end` section shown, dish card hidden |
| P5 | Play: dish with an image and credit | dish with `image`, `image_author` and licence set (admin edit) | `figure` + `figcaption` |
| B1 | Browse: list A→Z | `GET /plats` | count line, chips with top category, pagination (45 dishes > 1 page); **live** |
| B2 | Browse: page 2 | `GET /plats?page=2` | "← Précédent" link |
| B3 | Browse: each sort | `GET /plats?tri=<key>` for every key of `services.SORTS` | keys `az`, `votes`, `consensus`, `dispute`, `recent`; note in the count line for `consensus`/`dispute` |
| B4 | Browse: search hit | `GET /plats?q=GRATIN+DAUPHINOIS` | "pour « … »" |
| B5 | Browse: no result | `GET /plats?q=garbure` | empty-list message |
| D1 | Dish page, no answer yet | `GET /plat/cassoulet` | 0 réponse, no consensus line; **live** (any active slug) |
| D2 | Dish page, answered by me | P2 on that dish, then `GET /plat/<slug>` | my row highlighted "(votre réponse)", skips line |
| D3 | Dish page, ≥ 3 answers | three visitors answer the same dish | consensus label |
| D4 | Dish page with image + Wikipédia link | Wikidata-sourced dish (importer fixture) | source line "Wikidata (CC0)" |
| D5 | Merged dish | admin merges A into B, `GET /plat/<A>` | 301 to B (shot = B) |
| D6 | 404 | `GET /plat/nexiste-pas` | `error.html` |
| S1 | Stats, empty | `GET /stats` on the fresh seed | "Pas encore assez de réponses" ×2, "Pas encore de réponses"; **live** (filled); dark |
| S2 | Stats, filled | after a few answers by several visitors | coverage bars, leaderboard, consensual/contested, per day (masked) |
| F1 | Propose: empty form | `GET /proposer` | **live** |
| F2 | Propose: similar dishes found | type a name close to an existing dish (e.g. "Quiche lorainne") | similar list, submit button hidden |
| F3 | Propose: validation errors | submit empty / too short | `.alert` with the messages |
| F4 | Propose: thank you | valid new dish, proof of work solved | `merci.html`, normal branch |
| F5 | Propose: duplicate | propose the same pending dish again | `merci.html`, `duplicate` branch |
| A1 | About | `GET /a-propos` | contact; **live** |
| N1 | Nav on phone | any page at 390 | header nav wraps, `aria-current` on the active entry |
| E1 | Rate limited | exceed the rate limit on the no-JS `POST /voter` form or on `POST /proposer` | 429 page "Doucement ! …" (`main.py`), proposer form with its 429 error |
| M1 | Admin login | `GET /admin/login` | password + TOTP fields; never on the live site |
| M2 | Admin login error | wrong password or code | error message, lockout counter |
| M3 | Dashboard | log in (`e2e/run.sh` creates a throwaway admin) | counters, import status (offline import failure shown cleanly) |
| M4 | Dishes list, filters, search | `/admin/plats`, `?source=user`, `?q=garbure` | bulk actions, merge form |
| M5 | Dish edit | `/admin/plats/<id>` | form with image fields |
| M6 | Submissions | `/admin/propositions` (after F4) | accept / reject / merge |
| M7 | Votes | `/admin/votes`, `?categorie=tarte` | table + delete form |
| M8 | Categories | `/admin/categories` | list + add form |
| M9 | Words (moderation) | `/admin/mots` | lists + forms |
| M10 | Settings | `/admin/reglages` | toggles |
| M11 | Audit log | `/admin/journal` | entries from the session |

Status (2026-11-16): **partial**. The harness `visual/` covers P1, B1–B5, D1, D6, S1, F1, F3, A1, M1 (and N1
through every 390 px shot): 19 cases, 57 pictures. The others (P2–P5, D2–D5, S2, F2, F4, F5, E1, M2–M11)
need answers, a login or admin data and come in a next QA PR.

How it works:
- `visual/run.sh IMAGE` starts the built image hardened (read-only, no caps, uid 10001, outbound HTTP to a
  dead proxy), seeds it with `e2e/seed.py`, then `node visual/shoot.mjs` drives the runner's Chrome over the
  DevTools protocol (Node built-ins only, no package). Each case is shot full page at 1400 / 1024 / 390 wide
  (viewport height 900, dark where marked), animations and transitions off. 1400 and 1024 are shot in a
  Chrome with a mouse (`hover: hover`, so the play page's key hints show), 390 in a touch Chrome without one.
- Masks (`visual/cases.mjs`): P1's dish title, description and region are replaced by fixed text and the
  region is always shown (the server picks the dish at random); F3 hides `#pow-status`. S1 is the empty
  seed, so its "par jour" block has no date in it. Below 900 px the page background of a full-page shot
  stops: that is the screenshot method, not the page.
- Comparison in Chrome (canvas): a pixel differs when a channel moves by more than 24; a case fails when
  more than 0.1 % of its pixels differ, when the page size changes, on a page error, or when a baseline has
  no case any more. CI (`image` job, step "Visual regression") uploads `visual-<sha>`: every shot, a red
  diff picture per difference and `report.json`.
- Baselines in `visual/baseline/`, made by the CI runner's Chrome (fonts differ elsewhere, so a local run
  against them shows font differences; `visual/baseline/chrome.txt` names the Chrome). A wanted visual
  change: the PR says so, takes the new shots from the `visual-<sha>` artifact into `visual/baseline/`, and
  QA compares before and after. A new Chrome on the runner that moves pixels is a QA baseline PR of its own.
  CI runs it with `VISUAL_PRINT_NEW=1`: every failing shot is also printed in the job log (chunked base64 +
  sha256), and `node visual/from-log.mjs <job log> [dir]` writes them back, for an agent whose sandbox
  cannot download Actions artifacts. Look at each picture before committing it.
- Local: `docker build -t plats:local . && visual/run.sh plats:local` (needs node ≥ 22 and Chrome,
  `CHROME=/path`); `visual/run.sh plats:local --update` rewrites the baselines (QA only).
