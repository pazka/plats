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
