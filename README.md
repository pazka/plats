# plats

**plats.hosh.it** — a social experiment: which category (Pain, Purée, Soupe, Sandwich, Tarte,
Gratin, Ragoût, Salade, Saucisse, Ingrédient) is a French dish closest to? One question at a time, colourful
thanks for every answer, public statistics per dish.

- Product spec: [`BRIEF.md`](BRIEF.md)
- Builder/agent instructions: [`CLAUDE.md`](CLAUDE.md)
- Dish data: [Wikidata](https://www.wikidata.org) (CC0), Wikipédia categories, recipe sites
  (cuisine-facile.com, Elle à Table from a saved page) — each question links to its recipe.
- Catalogue review: one sandboxed `claude -p` per dish (`scripts/review_dishes.py`, DEPLOY.md §4b).

## Stack

Python 3.12 · FastAPI · Jinja2 · SQLite (WAL, FTS5) · vanilla JS/CSS — one container, no CDN,
no external fonts, no analytics.

## Develop

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # test suite (no network needed)
PLATS_DATA_DIR=./data PLATS_COOKIE_SECURE=0 .venv/bin/uvicorn --factory app.main:run --reload
.venv/bin/python scripts/import_wikidata.py      # optional: load the Wikidata catalogue
.venv/bin/python -m app.cli import-sources       # optional: recipe sites + Wikipédia (status 'review')
.venv/bin/python -m app.cli review-rules         # review without Claude (obvious cases only)
python3 scripts/review_dishes.py --self-test     # prove the reviewers' Docker sandbox
```

Admin credentials for local use: `PLATS_LOCAL=1 scripts/set-admin.sh ./.env` then export the
values (or use `--env-file` with uvicorn).

## Deploy

See [`DEPLOY.md`](DEPLOY.md) (env vars, volume, health URL, first-run commands, smoke checks,
and the decisions taken where the brief was silent). CI: `.github/workflows/ci.yml`
(tests → build → Trivy CRITICAL gate → `ghcr.io/pazka/plats:ci` + `:<sha>`) and
`release.yml` (tag `v*` → `:vX.Y.Z` + `:latest`).
