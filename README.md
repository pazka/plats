# plats

**plats.hosh.it** — a social experiment: which category (Pain, Purée, Soupe, Sandwich, Tarte,
Gratin, Ragoût, Salade, Ingrédient) is a French dish closest to? One question at a time, colourful
thanks for every answer, public statistics per dish.

- Product spec: [`BRIEF.md`](BRIEF.md)
- Builder/agent instructions: [`CLAUDE.md`](CLAUDE.md)
- Dish seed data: [Wikidata](https://www.wikidata.org) (CC0).

## Stack

Python 3.12 · FastAPI · Jinja2 · SQLite (WAL, FTS5) · vanilla JS/CSS — one container, no CDN,
no external fonts, no analytics.

## Develop

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # test suite (no network needed)
PLATS_DATA_DIR=./data PLATS_COOKIE_SECURE=0 .venv/bin/uvicorn --factory app.main:run --reload
.venv/bin/python scripts/import_wikidata.py      # optional: load the Wikidata catalogue
```

Admin credentials for local use: `PLATS_LOCAL=1 scripts/set-admin.sh ./.env` then export the
values (or use `--env-file` with uvicorn).

## Deploy

See [`DEPLOY.md`](DEPLOY.md) (env vars, volume, health URL, first-run commands, smoke checks,
and the decisions taken where the brief was silent). CI: `.github/workflows/ci.yml`
(tests → build → Trivy CRITICAL gate → `ghcr.io/pazka/plats:ci` + `:<sha>`) and
`release.yml` (tag `v*` → `:vX.Y.Z` + `:latest`).
