# Deploying a pandm server

A deployed server gives you wandb-style multi-device access: sign in with GitHub, run `pandm login` on each training machine, and every `pandm.init()` dual-writes — local SQLite stays the source of truth, a background thread syncs to the server, offline data is backfilled on reconnect.

Two interchangeable server implementations speak the same HTTP protocol. Pick one:

| | Cloudflare Workers | Docker self-host |
|---|---|---|
| Infra | serverless (D1 + R2), no machine to manage | any box that runs a container |
| Cost | free tier, then ~$/month at heavy logging (see below) | the machine you already have |
| Data | D1 database + R2 bucket | one SQLite file + a media directory in a volume |

## Prerequisite (both options): a GitHub OAuth App

Sign-in is GitHub OAuth; each deployment needs its own app.

1. Create one at <https://github.com/settings/applications/new>
2. **Authorization callback URL** must be exactly `https://<your-domain>/api/auth/callback` — a scheme/host/path mismatch is the most common cause of failed logins
3. After creating, **Generate a new client secret**; you'll need the Client ID and the secret below

## Option A: Cloudflare Workers

Requirements: Node 22+, pnpm, a Cloudflare account. A custom domain is optional — the default `<name>.<account>.workers.dev` URL works too.

```sh
cd workers && pnpm install
npx wrangler login

npx wrangler d1 create pandm           # ① paste the printed database_id into wrangler.jsonc
npx wrangler r2 bucket create pandm-media

npx wrangler secret put GITHUB_CLIENT_ID
npx wrangler secret put GITHUB_CLIENT_SECRET
openssl rand -hex 32 | npx wrangler secret put PANDM_SECRET_KEY   # signs session cookies

npx wrangler d1 migrations apply pandm --remote   # ② create the schema
pnpm run deploy                                   # ③ builds the dashboard, then wrangler deploy
```

**Custom domain** (the domain must be a zone in the same Cloudflare account): edit the `routes` line in `wrangler.jsonc` before deploying —

```jsonc
"routes": [{ "pattern": "pandm.example.com", "custom_domain": true }],
```

`wrangler deploy` provisions the DNS record and certificate automatically. To use only the workers.dev URL instead, delete the `routes` line.

**Updating**: `pnpm run deploy` again. If a release adds a migration file, run `npx wrangler d1 migrations apply pandm --remote` first — migrations are tracked and idempotent.

**Cost note**: D1 bills per row written (100k rows/day free). Logging ~10 metrics/sec around the clock is ~860k rows/day — a few dollars a month on the paid tier. Reads and R2 media are effectively free at personal scale.

## Option B: Docker self-host

```sh
GITHUB_CLIENT_ID=… GITHUB_CLIENT_SECRET=… docker compose up -d
```

This is the same binary as `pandm ui`, in multi-user mode because the OAuth env vars are set. Details:

- **TLS**: put it behind a reverse proxy (Caddy/Traefik) or a Cloudflare tunnel, and set `PANDM_SECURE_COOKIES=1` so session cookies are HTTPS-only
- **Session secret**: defaults to an auto-generated `/data/secret_key`; pin it via `PANDM_SECRET_KEY` if you run more than one replica
- **Backup**: everything lives in the `/data` volume — `pandm.db` (SQLite) plus a `media/` directory. Copying the volume is a complete backup
- **Without OAuth env vars** the server falls back to single-tenant mode (`--api-key` + `PANDM_REMOTE` on clients): no accounts, reads unauthenticated — only for trusted networks

## After deploying (both options)

```sh
# on each training machine
pandm login https://pandm.example.com   # opens the browser, approve the code
python train.py                          # now dual-writes
pandm sync                               # backfill runs whose process already exited
```

- Verify the deployment: `/` serves the dashboard, unauthenticated `GET /api/me` returns 401, `GET /api/auth/login` 302-redirects to github.com
- API keys: avatar menu → Copy / Rotate. Rotating immediately invalidates the old key; re-run `pandm login` on affected machines
- `PANDM_NO_SYNC=1` keeps a quick experiment local-only without logging out

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/api/auth/login` returns 500 | one of the three secrets is missing |
| GitHub shows "redirect_uri mismatch" | OAuth App callback URL ≠ `https://<domain>/api/auth/callback` |
| `pandm login` says request expired | device codes live 10 minutes — rerun and approve promptly |
| signed in but dashboard is empty | runs belong to the account that ingested them; check you're signed in as the same user whose API key the SDK uses |
