# CLAUDE.md — pazka/plats

Build the site specified in `BRIEF.md` (read it fully first). Owner: Alexandre (pazka).
Target box rules: **`docs/BOX-CONTRACT.md` is binding** (Traefik file provider, no docker labels,
resolver `le`, loopback port `127.0.0.1:9131`, `/srv/plats`, releases deploy through `hosh-deploy`).

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

## If you are the on-box deployment session (production server)
- Box rules (`/home/pazka/MIGRATION/CLAUDE.md`, `11-BOX-STANDARD.md`): `ACTIONS-LOG.md` intent
  BEFORE + result/revert AFTER; `scripts/smoke.sh` BEFORE and AFTER; root only inside an
  owner-opened sudo window.
- Releases deploy themselves through the box runner (DEPLOY.md §1). You may run
  `sudo hosh-deploy deploy|rollback|status plats …` any time (no sudo window needed), with an
  ACTIONS-LOG line and smoke before/after. A rebuild follows DEPLOY.md §1b inside an owner sudo
  window. Otherwise only read-only diagnostics (`docker ps`, `docker compose … logs`, Traefik API).
- Once the owner has run `set-admin.sh`, **never print, cat, grep, copy or edit `plats.env`**
  (sole exception: the one-line `PLATS_IMAGE` sed of an upgrade, DEPLOY.md §1). If a variable is
  wrong, tell the owner which one (the script's error says it) and stop.
- Never run `scripts/set-admin.sh` yourself (interactive, owner-only), never change code on the
  box, never delete the `plats-data` volume.

## Maintenance (autonomous agent)
Keep Python, pip packages, the base image and Actions current **without changing behaviour**. Read `TESTING.md`.
- Gate = `ci` green: `test (python 3.12|3.13|3.14|3.15)` + `image`. Work on `agent/<topic>` branches (CI runs,
  nothing is pushed to ghcr). Don't push to `main`, tag or release unless the owner asks: a release deploys.
- Dependabot PRs (weekly, 7-day cooldown): green → summarise risk in a PR comment; red → reproduce per
  TESTING.md and fix with the smallest change. When the Dockerfile moves to a new Python, move the test
  matrix to `[new, next…]`.
- Never weaken or delete a test to get green; `tests/fixtures/schema.json` changes only with a migration.
  If behaviour must change, ask the owner.

## Agent system: QA gate, journal, "what happened to X"
**Before any work in this repo, whatever the request:** read the hub `pazka/hosh-management`:
`AGENTS.md` (how the hosh.it agents and the owner work together), `README.md` (every app, site, repo and
journal), `roles/` (the full procedure of each agent: QA, Security, Developer, DevOps, dependency updates).
`gh api repos/pazka/hosh-management/contents/AGENTS.md -H "Accept: application/vnd.github.raw"`
(same for `README.md`, `roles/README.md`, `roles/qa.md`...). Those rules apply here; this file adds the
repo's own.

Canonical rules: `AGENTS.md` in the private hub `pazka/hosh-management`
(`gh api repos/pazka/hosh-management/contents/AGENTS.md -H "Accept: application/vnd.github.raw"`).
Summary, binding for every agent that works in this repo:
- **Nothing is merged without the independent QA agent.** Work on `agent/<topic>-<yyyymmdd>`, get CI
  green, write in the PR body what changed, which use cases it touches and how it was tested, then add
  the label `qa:requested`. Merge only when the PR has `qa:approved` AND a comment
  `QA-VERDICT: APPROVED sha=<current head sha>`. On `qa:changes-requested`: fix on the same branch,
  answer each finding, ask again; QA re-tests the whole PR. Never approve your own PR. After QA's
  approval the agent that opened the PR merges it: this replaces "the owner merges" and "merge when
  CI is green" wherever this file still says so.
- **Owner fast lane**: when the owner asks for a feature in a session he is in, do it end to end in
  that session (discuss, develop, QA by a fresh independent QA subagent or a QA routine run now, merge,
  release, deploy, show the live URL). Rules: AGENTS.md section 2b. The session that wrote the code never
  writes the verdict.
- **QA owns the tests and the visual regression**: every use case a user can reach (listed in
  `TESTING.md`), screenshots at 1400 / 1024 / 390 px, baselines in the repo. A wanted visual change
  must be announced in the PR. Never weaken a test or a gate to get green.
- A NEW third-party dependency always needs the owner: open an issue in `pazka/hosh-management`
  labelled `ready-for-human` (that label is the list of places where the owner is needed).
- Release = Actions → release with `version=vX.Y.Z` on the default branch. Never push a git tag.
- **Journal**: after each event (PR ready, QA verdict, merge, release, deploy, rollback, incident,
  blocker) add one comment to this project's journal, `pazka/hosh-management#7`.
- **When the owner asks "what happened to X"** (this app, another one, or the server): open the index
  in `pazka/hosh-management` `README.md`, read the last comments of X's journal issue
  (`gh issue view <n> -R pazka/hosh-management --comments`), then the open `ready-for-human` issues,
  then PRs / Actions runs if needed. Server facts you cannot see: ask the Box agent (mailbox from a
  laptop session, or an issue labelled `box-question`). Answer with: what happened (dated), the state
  now, where the owner is needed.
- Online checks use only the URLs in `pazka/hosh-management` `uptime/checks.json`.
