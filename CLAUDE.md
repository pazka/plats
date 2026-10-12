# CLAUDE.md — pazka/plats

Build the site specified in `BRIEF.md` (read it fully first). Owner: Alexandre (pazka).

## Scope of the cloud builder
- You work on this GitHub repo only: code, tests, Dockerfile, CI. You have **no access to the
  production server**; deployment is done later by a separate on-box session. Don't try to deploy.
- Deliverables: the app per `BRIEF.md`; green `pytest`; a Dockerfile that runs as a non-root uid
  with a read-only rootfs and a `/data` volume; `docker-compose.example.yml` (no secrets) showing
  env vars, volume and hardening (`read_only`, `no-new-privileges`, `cap_drop: [ALL]`, healthcheck);
  `.env.example`; `scripts/set-admin.sh` (interactive, run by the owner on the server: admin password
  → argon2id hash, TOTP secret → QR in the terminal, writes the env file — secrets never printed to
  logs or committed); `scripts/import_wikidata.py` (also callable from the admin panel).
- CI in `.github/workflows/`, modelled on the owner's other repos (e.g. read
  `pazka/DrawTogether/.github/workflows/` with `gh api`): push to `main` → build → **Trivy CRITICAL
  gate** → push `ghcr.io/pazka/plats:ci` + `:<sha>`; `release.yml` on tag `v*` → `:vX.Y.Z` + `:latest`.
- Finish with: all tests green, CI green on `main`, a `v0.1.0` tag/release built, and `DEPLOY.md`
  listing exactly what the server needs (env vars, volume, port, health URL, first-run commands,
  smoke checks proving full capability — not just a 200).

## Rules
- Never commit secrets, `.env`, databases, exports. Never install MCP servers.
- No external runtime calls from the browser (no CDN, fonts, analytics). Wikidata/Wikimedia calls
  happen server-side in the importer only, with a descriptive User-Agent.
- Commit in small, clearly described steps on `main`.
