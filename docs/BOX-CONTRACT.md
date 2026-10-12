# hosh.it box contract — what an app must look like to deploy first try

Snapshot of the owner's box standard (source of truth: private `hosh-migration/11-BOX-STANDARD.md`).
Public-safe: no secrets, no internal security details. If this copy and the template disagree,
ask the owner. Last synced: 2026-10-12.

## The box in one paragraph
One Debian 12 VPS (4 vCPU, 15 GiB RAM, IP 54.37.70.57), Docker 29 + compose v2. A single **edge
Traefik v3.3** runs in host network and terminates TLS for every site (ACME resolver **`le`**,
entrypoint **`websecure`**, global HTTP→HTTPS redirect). It routes with its **file provider only**:
apps do **not** use Traefik docker labels (the docker provider is unavailable until the edge moves
to ≥ v3.7). Each app is a compose project **published on a loopback port** `127.0.0.1:<PORT>` and
gets one Traefik route file. Intrusion detection reads Traefik's access log, so apps don't need
their own. Secrets live in a mode-600 env file next to the compose file on the box, never in git.

## The app MUST
1. **Ship as one image** `ghcr.io/pazka/<app>` built by GitHub Actions (CI on `main`: tests →
   build → **Trivy CRITICAL gate** (fixable) → hardened container smoke → push `:ci`, `:<sha>`;
   release on tag `vX.Y.Z` or manual dispatch: same gate → `:vX.Y.Z`, `:X.Y`, `:latest` + GitHub
   release). Templates: `.github/workflows/ci.yml`, `release.yml`.
2. **Run hardened, unchanged**: non-root (uid **10001**), `read_only: true` rootfs with tmpfs `/tmp`,
   `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit 128`, `mem_limit 512m` (raise with a reason).
   Nothing written outside `/data` and `/tmp`.
3. **Keep all state in one named volume at `/data`** (DB, uploads, generated secrets). The box backs
   up `/var/lib/docker/volumes` monthly. Prefer SQLite (WAL) over a separate DB container.
4. **Listen on plain HTTP, one port (8000 by convention)** inside the container. TLS, HSTS and
   HTTP→HTTPS are done by Traefik. WebSockets work through Traefik without extra config.
5. **Expose `GET /healthz`** (and `HEAD`) → `200` JSON `{"status":"ok", …}` that proves the app
   actually works (e.g. DB reachable, row counts), not just that the process is up. The compose
   healthcheck and `deploy.sh` both use it.
6. **Be configured only by env vars** from `<app>.env` (one file for compose interpolation and the
   container). `.env.example` is committed, has **box defaults that work unchanged**, and lists
   the owner-provided secrets on a `# required:` line. Secrets the app can generate itself (signing
   keys, salts) are generated once into `/data/.secrets/` (0600).
7. **Trust `X-Forwarded-For` only from proxies** (`127.0.0.0/8`, `::1`, `172.16.0.0/12`). Traefik
   reaches the container through Docker's port proxy, so the peer IP is a 172.x gateway.
8. **Log JSON lines to stdout** (no raw client IPs unless needed; hash them).
9. **Never need the agent to see a secret.** Admin credentials etc. are produced by an
   interactive owner-run script (e.g. `scripts/set-admin.sh`: hidden prompt → hash written into
   the env file).
10. **Ship `scripts/deploy.sh` + `deploy/traefik-route.yml.tmpl` + `DEPLOY.md`** from the template
    (rendered for this app). `deploy.sh` is idempotent: preflight → pull + up → healthy → loopback
    `/healthz` → writes the Traefik route → confirms the router via the Traefik API → optional
    `scripts/post-deploy.sh` (migrations, imports, capability smoke) → `https://<host>/healthz` +
    HSTS → log report.
11. **Prove full capability, not a 200.** `DEPLOY.md` lists the checks that prove each feature works
    (API shape, a write round-trip, websocket echo …). The on-box session turns them into the box's
    smoke harness.

## The app SHOULD
- Serve everything from its own origin (no CDN/fonts/analytics), with a strict CSP.
- Answer `HEAD` like `GET`. Return 404 for dotfiles (`/.env`, `/.git`); the edge already blocks them.
- Keep one worker/process unless it really needs more (the box is shared by ~20 services).
- Name things after the app everywhere: container `<app>`, volume `<app>-data`, router `<app>`,
  env file `<app>.env`, checkout `/srv/<app>`.

## Given by the owner (don't guess)
- Subdomain `<app>.hosh.it` (DNS A → 54.37.70.57, created before deploy).
- Loopback port `127.0.0.1:<PORT>` from the box's port registry.
- Repo `github.com/pazka/<app>`; who runs the deploy (an on-box Claude session, never the builder).
