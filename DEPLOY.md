# Auto DM — Deploy Guide (Phase 26d)

This guide walks through deploying the **Auto DM** web backend to a
Debian VPS that already runs Postgres + Redis (in Docker), with nginx
as a TLS-terminating reverse proxy in front. The static frontend is
hosted on Vercel and talks to the backend over HTTPS.

```
┌────────────────────────┐         HTTPS          ┌──────────────────────────────┐
│  Vercel (frontend)     │ ─────────────────────► │  nginx :443                  │
│  vanilla HTML/CSS/JS   │                         │  proxy.storsistemas.com.br   │
└────────────────────────┘                         │  TLS cert (Let's Encrypt)    │
                                                   └──────────┬───────────────────┘
                                                              │ HTTP (localhost:4004)
                                                              ▼
                                                   ┌──────────────────────────────┐
                                                   │  docker: auto-dm             │
                                                   │  FastAPI / uvicorn           │
                                                   └──────────┬───────────────────┘
                                                              │ asyncpg / redis
                                                              ▼
                                                   ┌──────────────────────────────┐
                                                   │  docker: postgres + redis    │
                                                   └──────────────────────────────┘
```

## 0. Prereqs

- Debian 11+ VPS with Docker + Docker Compose plugin installed.
- A registered domain pointing an A record at the VPS (we'll use
  `proxy.storsistemas.com.br`).
- Postgres 14+ container reachable from the backend container (the
  user's existing setup; reused here).
- Redis 7+ container reachable from the backend container.
- nginx already installed and configured with a Let's Encrypt cert
  for `proxy.storsistemas.com.br` (or follow §4 below to set it up).
- A Minimax API key (`sk-...`).
- A Vercel account for the frontend.

---

## 1. Backend directory on the VPS

Create the deploy dir and clone the repo (or copy the source):

```bash
sudo mkdir -p /opt/auto-dm
sudo chown $USER:$USER /opt/auto-dm
cd /opt/auto-dm
git clone <your-git-url> .
# or: scp -r src/ pyproject.toml Dockerfile docker-compose.yml .env.example /opt/auto-dm/
```

The backend lives in `/opt/auto-dm` and ships:

- `Dockerfile` — multi-purpose image (single stage, python:3.11-slim).
- `docker-compose.yml` — backend service only; expects Postgres + Redis
  to be reachable from the host's network.
- `.env` — **your** secrets (created next).
- `src/auto_dm/web/static/` — the static frontend; also deployed to
  Vercel in §5. The backend itself can serve these if you don't want
  a separate Vercel project (see §5 alternative).

---

## 2. Backend `.env`

Copy the template and fill it in:

```bash
cd /opt/auto-dm
cp .env.example .env       # if you have one; otherwise create from scratch
chmod 600 .env             # owner-only — these are secrets
```

Required keys (lines):

```env
# --- Backend ---
ENVIRONMENT=production
LOG_LEVEL=INFO

# --- Postgres ---
# Use the existing container or a TCP-reachable host.
#   asyncpg://user:password@host:5432/dbname
DATABASE_URL=postgresql+asyncpg://auto_dm:STRONG_PASSWORD@10.0.0.5:5432/auto_dm

# --- Redis ---
REDIS_URL=redis://10.0.0.6:6379/0

# --- JWT secret (≥32 chars; one-time set, never rotated without invalidating users) ---
JWT_SECRET=$(openssl rand -hex 32)        # paste the output here
JWT_EXPIRES_MINUTES=10080                # 7 days
SESSION_TTL_SECONDS=86400                # 24h active session TTL in Redis

# --- CORS ---
# Comma-separated origins. The Vercel URL is required; add localhost for testing.
FRONTEND_URL=https://seu-app.vercel.app,http://localhost:3000

# --- Invite-code gate (Phase 26e) ---
# When set, ``POST /api/auth/signup`` requires ``invite_code`` in the
# body to match this value. Leave empty to keep signup open (dev only).
# Generate a strong random string for production:
#   openssl rand -base64 24
INVITE_CODE=

# --- LLM (Minimax) ---
AUTO_DM_PROVIDER=minimax
AUTO_DM_API_KEY=sk-...                   # your real key
AUTO_DM_BASE_URL=                        # leave empty to use provider default
AUTO_DM_MODEL=MiniMax-Text-01
AUTO_DM_TEMPERATURE=0.8
AUTO_DM_MAX_TOKENS=2048
```

> **Compose passthrough:** the `docker-compose.yml` references these
> via `${VAR}` interpolation. The YAML's `ports` line only exposes
> the backend to the host loopback (`127.0.0.1:4004:4004`) — nginx
> connects to it from the same host, never from the public internet.

---

## 3. Postgres schema

The backend creates the schema on first start (idempotent
`Base.metadata.create_all`). You only need to ensure the database and
role exist.

```bash
# Inside the existing Postgres container, or via psql from the host:
psql -h 10.0.0.5 -U postgres -c "CREATE USER auto_dm WITH PASSWORD 'STRONG_PASSWORD';"
psql -h 10.0.0.5 -U postgres -c "CREATE DATABASE auto_dm OWNER auto_dm;"
psql -h 10.0.0.5 -U postgres -d auto_dm -c "GRANT ALL ON SCHEMA public TO auto_dm;"
```

You can confirm the schema is wired up by hitting the health check
once the backend is up (§6).

---

## 4. nginx reverse proxy

We're proxying `https://proxy.storsistemas.com.br/*` →
`http://127.0.0.1:4004/*`. The TLS cert was provisioned earlier; if
not, run `certbot` once:

```bash
sudo certbot --nginx -d proxy.storsistemas.com.br
```

Then add the API proxy snippet to `/etc/nginx/sites-available/proxy.storsistemas.com.br.conf`:

```nginx
# /etc/nginx/sites-available/proxy.storsistemas.com.br.conf

# Redirect HTTP → HTTPS (whole vhost).
server {
    listen 80;
    listen [::]:80;
    server_name proxy.storsistemas.com.br;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name proxy.storsistemas.com.br;

    # certbot-managed paths — DO NOT TOUCH
    ssl_certificate     /etc/letsencrypt/live/proxy.storsistemas.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/proxy.storsistemas.com.br/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Tighten default SSL.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # API + static. Long read/send timeouts so a slow LLM turn (up to
    # ~60s with extended thinking) doesn't get cut off by the proxy.
    location / {
        proxy_pass http://127.0.0.1:4004;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

Reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 5. Frontend on Vercel (vanilla HTML)

The frontend is pure static HTML/CSS/JS — no build step. Two
deployment options:

### Option A — Vercel (recommended)

1. Create a new Vercel project (empty repo).
2. Drop the contents of `src/auto_dm/web/static/` into the project
   root (`index.html`, `style.css`, `app.js`).
3. In Vercel **Environment Variables**, add:

   ```
   AUTO_DM_API_BASE = https://proxy.storsistemas.com.br
   ```

4. Edit `app.js`'s `API_BASE` to read from `window.__ENV.API_BASE`:

   ```js
   const API_BASE = (window.__ENV && window.__ENV.API_BASE) || "";
   ```

   The current default (`""`, same origin) is fine when the backend
   serves the static files directly too. If you split, set the Vercel
   variable above and ensure `app.js` reads it.

5. Deploy. CORS must allow your Vercel origin — confirm `FRONTEND_URL`
   in the backend `.env` lists the Vercel URL.

### Option B — Backend serves the static files

If you don't want Vercel, just point your browser at
`https://proxy.storsistemas.com.br/`. FastAPI mounts `static/` at the
root URL out of the box. `FRONTEND_URL` is then just `https://proxy.storsistemas.com.br`.

---

## 6. Boot the backend

```bash
cd /opt/auto-dm
docker compose up -d --build
```

Watch the logs:

```bash
docker compose logs -f auto-dm
```

You should see:

```
INFO  Started server process [1]
INFO  Waiting for application startup.
INFO  Application startup complete.
INFO  Uvicorn running on http://0.0.0.0:4004
```

Health check:

```bash
curl -s https://proxy.storsistemas.com.br/api/health
# => {"status":"ok","version":"0.1.0"}
```

Sanity check the auth flow:

```bash
curl -s -X POST https://proxy.storsistemas.com.br/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"adminpass1234"}'
# => {"token":"...","user":{...},"expires_in_minutes":10080}
```

---

## 7. Backups

### Postgres

Add a cron job to dump the database nightly:

```bash
# /etc/cron.d/auto-dm-pg-backup
0 3 * * * postgres pg_dump -Fc -d auto_dm -f /var/backups/auto-dm/$(date +\%Y\%m\%d).dump
```

Pair with `restic`/`borg` offsite if you care about durability.

### Redis

Redis here only holds **active sessions** (TTL 24h) — losing it is
non-fatal. Saved games live in Postgres, not Redis. If your Redis
container doesn't have AOF persistence enabled, you're fine; if you
want belt-and-suspenders, enable AOF or schedule `BGSAVE`.

### `.env`

Treat `/opt/auto-dm/.env` as a secret. Back it up to your password
manager or a sealed vault — **don't** put it in git.

---

## 8. Updating the app

```bash
cd /opt/auto-dm
git pull                       # or scp new files
docker compose up -d --build   # rebuild + recreate the container
docker compose logs -f auto-dm # verify clean start
```

There is no in-place migration step. The schema is created
idempotently on boot; if you ever introduce Alembic migrations
(see `web/db.py::Base.metadata.create_all` comment), run them
inside the container before rolling out the new code.

---

## 9. Troubleshooting

| Symptom                                | Likely cause                                                |
| -------------------------------------- | ----------------------------------------------------------- |
| `401 invalid token` everywhere          | `JWT_SECRET` rotated without a redeploy, or backend restarted with new secret. |
| `503 / Connection refused` on health   | Container not running. `docker compose ps` then `logs -f`.  |
| `500 DatabaseError` on signup          | Postgres unreachable from container, or wrong creds in `DATABASE_URL`. |
| `500 no provider factory`              | `AUTO_DM_PROVIDER` / `AUTO_DM_API_KEY` / `AUTO_DM_MODEL` missing from env. |
| CORS error in browser console          | `FRONTEND_URL` in backend `.env` doesn't list the Vercel origin. Add it + redeploy. |
| SSE drops mid-stream                   | (Removido — endpoint SSE foi descontinuado; mensagens agora chegam inteiras via `/input`.) |
| Slow first response after idle         | Cold-start; uvicorn worker has no warm cache. Acceptable for hobby use. |

---

## 10. Hardening checklist (when you're ready)

These are **not** required for the friend's-test phase, but document
them as TODOs:

- [ ] Run nginx with `ssl_stapling on` and `resolver` set.
- [ ] Enable HSTS (`Strict-Transport-Security: max-age=63072000; includeSubDomains`).
- [ ] Fail2ban for `/api/auth/login` brute-force attempts.
- [ ] Add a rate-limiter (e.g. `nginx limit_req` zone, or `slowapi` middleware in FastAPI).
- [ ] Move from JWT-HS256 to RS256 if you ever need to verify tokens
      from a third party.
- [ ] Add Sentry / structured logging for error tracking.
- [ ] Add an `auto-dm` system user on the VPS with shell `/bin/false`,
      chown `/opt/auto-dm` to it, and run the compose stack under that user.

---

## Appendix: one-liner health check

```bash
curl -fsS https://proxy.storsistemas.com.br/api/health | jq .
```

If you don't have `jq`, drop the pipe.