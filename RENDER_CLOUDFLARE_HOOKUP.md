# Render → Cloudflare Hookup (Tunnel + Zero Trust Access)

How `https://nexus.aindoori.com` was wired to the Render-hosted Nexus City OS
container, fronted entirely by Cloudflare (Tunnel + Zero Trust Access), with a
keep-alive Worker to defeat free-tier sleep. This is the end-to-end runbook of
exactly what was done, with the CLI commands.

---

## 0. Topology (what we built)

```
 Browser ── HTTPS ──▶ Cloudflare edge (nexus.aindoori.com)
                         │   1. Zero Trust ACCESS challenge (email OTP, invite-only)
                         │   2. signs an Access JWT (RS256) and forwards
                         ▼
            Cloudflare TUNNEL (outbound-only, no inbound ports)
                         │   ingress: nexus.aindoori.com → http://127.0.0.1:10000
                         ▼
            cloudflared SIDECAR (runs inside the Render container)
                         │
                         ▼
            Nexus app  (listens on $PORT = 10000, 0.0.0.0)
                         │   verifies the Access JWT in pure stdlib
                         ▼   (nexus/cfaccess.py: JWKS + iss + aud + exp)
            Access-only mode: no password form, identity = JWT subject

 [Cloudflare Worker cron */10] ── ping ──▶ raw Render origin (keeps it awake)
```

Key idea: the Render service has **no public ingress used by humans**. All
human traffic comes through Cloudflare → Tunnel → the in-container
`cloudflared` sidecar → the app on loopback. The raw `*.onrender.com` URL is
only pinged by the keep-alive Worker.

---

## 1. Prerequisites / identifiers

| Thing | Value |
|---|---|
| Render service | `srv-d8oa981o3t8c73depee0` ("nexus-city-os"), Docker, Oregon, FREE plan |
| Render raw URL | `https://nexus-city-os.onrender.com` |
| Render API key | stored in `C:\Users\indoo\.render\cli.yaml` |
| Cloudflare account ID | `db646576fddd10368a374d5be7b89a1f` |
| Zone | `aindoori.com` (zone id `f65cc9d709b45c1a4064cb924b9f184a`) |
| Team domain | `aindoori.cloudflareaccess.com` |
| Cloudflare API token | scopes: Tunnel:Edit, Access Apps+Orgs, DNS:Edit |

Tools used: `curl` (Cloudflare REST + Render REST), `wrangler` (keep-alive
Worker), `git`/`gh` (deploy trigger). The Render CLI has **no `env` command**,
so env vars are set via the Render REST API.

---

## 2. Cloudflare Tunnel

### 2a. Create the named tunnel

```bash
# create tunnel "nexus-city-os" → returns its id + a connector token
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/cfd_tunnel" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"name":"nexus-city-os","config_src":"cloudflare"}'
# → result.id = 247288a8-a8a3-4ffc-9e38-8abffdf27a65
```

`config_src: cloudflare` means the tunnel is **remotely managed** — ingress
rules live in Cloudflare, not a local config file, so the sidecar only needs
the connector token.

### 2b. Get the connector token

```bash
curl -s \
  "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/cfd_tunnel/247288a8-a8a3-4ffc-9e38-8abffdf27a65/token" \
  -H "Authorization: Bearer $CF_API_TOKEN"
# → the long base64 token, set later as CLOUDFLARE_TUNNEL_TOKEN on Render
```

### 2c. Configure the tunnel ingress (remote config)

This is the critical mapping — `nexus.aindoori.com` → the app on loopback.
The app listens on `$PORT` which Render sets to **10000**, over IPv4, so the
origin must be `http://127.0.0.1:10000` (not `localhost`, not 8757).

```bash
curl -s -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/cfd_tunnel/247288a8-a8a3-4ffc-9e38-8abffdf27a65/configurations" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "config": {
      "ingress": [
        { "hostname": "nexus.aindoori.com", "service": "http://127.0.0.1:10000" },
        { "service": "http_status:404" }
      ]
    }
  }'
```

> Gotcha solved: the tunnel originally pointed at `localhost:8757` → the app
> answered "connection refused" because under Render it binds `$PORT=10000`.
> Re-pointing ingress to `http://127.0.0.1:10000` fixed it.

### 2d. DNS record for the hostname

A proxied CNAME points the public hostname at the tunnel's
`<tunnel-id>.cfargotunnel.com` target:

```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/zones/f65cc9d709b45c1a4064cb924b9f184a/dns_records" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "type": "CNAME",
    "name": "nexus",
    "content": "247288a8-a8a3-4ffc-9e38-8abffdf27a65.cfargotunnel.com",
    "proxied": true
  }'
```

(`proxied: true` is mandatory — the orange-cloud is what runs Access + the
tunnel routing at the edge.)

---

## 3. The `cloudflared` sidecar (inside the container)

The Docker image bundles a static `cloudflared` binary and the entrypoint
starts it as a sidecar **only when `CLOUDFLARE_TUNNEL_TOKEN` is set**, so the
same image still runs locally without Cloudflare.

`Dockerfile` (relevant lines):

```dockerfile
# install a pinned static cloudflared
ADD https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 /usr/local/bin/cloudflared
RUN chmod +x /usr/local/bin/cloudflared
ENTRYPOINT ["platform/scripts/entrypoint.sh"]
```

`platform/scripts/entrypoint.sh` (essence):

```sh
#!/bin/sh
set -e
# start the tunnel sidecar only if a token was injected
if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
  cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN" &
fi
# app binds to $PORT (Render sets 10000) on all interfaces
exec python platform/run.py --host 0.0.0.0 --port "${PORT:-8757}"
```

---

## 4. Cloudflare Zero Trust Access (the only sign-in)

### 4a. Create the Access application

```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/access/apps" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "Nexus City OS",
    "domain": "nexus.aindoori.com",
    "type": "self_hosted",
    "session_duration": "24h"
  }'
# → result.aud = b2f83f60...3d1955b7   (the JWT audience the app verifies)
# → app id     = b2271b67-fa2a-42a8-a755-906649b9ea64
```

### 4b. Add ONE allow policy (invite-only, single email)

```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/access/apps/b2271b67-fa2a-42a8-a755-906649b9ea64/policies" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "Allow Aditya",
    "decision": "allow",
    "include": [ { "email": { "email": "indooriaditya@gmail.com" } } ]
  }'
```

The IdP is Cloudflare's built-in **one-time-PIN email** — no password, no
external SSO needed. Only that one email is allowed in.

### 4c. App-side JWT verification

The app enforces the JWT too (never trusting the bare `Cf-Access-Authenticated-User-Email`
header alone). Setting these env vars flips the app into **Access-only mode**:
the in-app password form disappears, demo accounts aren't seeded, and identity
comes from the verified Access JWT (`nexus/cfaccess.py`: RS256 against the team
JWKS, checks issuer + audience + expiry).

---

## 5. Render env vars (set via REST — there is no Render CLI `env`)

> **Critical Render gotcha:** `PUT /v1/services/{id}/env-vars` **replaces the
> entire set**. You must GET the current vars, merge, then PUT the full list —
> otherwise you wipe everything else.

```bash
# 1) fetch current env vars (so we can merge, not clobber)
curl -s "https://api.render.com/v1/services/srv-d8oa981o3t8c73depee0/env-vars" \
  -H "Authorization: Bearer $RENDER_API_KEY"

# 2) PUT the FULL merged set back (whole array, every var present)
curl -s -X PUT "https://api.render.com/v1/services/srv-d8oa981o3t8c73depee0/env-vars" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  --data '[
    {"key":"PYTHONUNBUFFERED","value":"1"},
    {"key":"NEXUS_TRUST_PROXY","value":"1"},
    {"key":"NEXUS_DB_PATH","value":"/tmp/nexus-fresh.db"},
    {"key":"NEXUS_DISABLE_DEMO_ACCOUNTS","value":"1"},
    {"key":"NEXUS_PASSWORD_OP_1","value":"<redacted>"},
    {"key":"CLOUDFLARE_TUNNEL_TOKEN","value":"<connector token from 2b>"},
    {"key":"NEXUS_CF_ACCESS_TEAM_DOMAIN","value":"aindoori.cloudflareaccess.com"},
    {"key":"NEXUS_CF_ACCESS_AUD","value":"b2f83f60...3d1955b7"},
    {"key":"NEXUS_CF_ACCESS_ADMINS","value":"indooriaditya@gmail.com"},
    {"key":"NEXUS_CF_ACCESS_DEFAULT_ROLE","value":"viewer"},
    {"key":"NEXUS_LLM_BASE_URL","value":"http://Bedroc-Proxy-...elb.amazonaws.com/api/v1"},
    {"key":"NEXUS_LLM_API_KEY","value":"<redacted>"}
  ]'
```

Roles of the key vars:
- `CLOUDFLARE_TUNNEL_TOKEN` → tells the entrypoint to launch the sidecar.
- `NEXUS_CF_ACCESS_TEAM_DOMAIN` + `NEXUS_CF_ACCESS_AUD` → flip the app into
  Access-only mode and let it verify the JWT.
- `NEXUS_CF_ACCESS_ADMINS` → map that email to the admin role.
- `NEXUS_TRUST_PROXY=1` → trust `CF-Connecting-IP` for the rate limiter.

### Trigger a deploy (so new env + image take effect)

Deploys are normally auto-triggered by `git push origin main`, but you can
force one via the API:

```bash
curl -s -X POST "https://api.render.com/v1/services/srv-d8oa981o3t8c73depee0/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" --data '{"clearCache":"do_not_clear"}'
```

---

## 6. Keep-alive Worker (defeat free-tier sleep)

Render's FREE plan sleeps after ~15 min idle; when the container sleeps, the
`cloudflared` sidecar dies and `nexus.aindoori.com` goes down. A tiny
Cloudflare Worker on a `*/10` cron pings the **raw** Render origin to keep it
awake.

`wrangler.toml`:

```toml
name = "nexus-keepalive"
main = "src/index.js"
compatibility_date = "2024-01-01"

[triggers]
crons = ["*/10 * * * *"]
```

`src/index.js`:

```js
export default {
  async scheduled(event, env, ctx) {
    // ping the RAW origin (not the Access-gated hostname) to wake Render
    await fetch("https://nexus-city-os.onrender.com/", {
      method: "GET",
      headers: { "User-Agent": "nexus-keepalive" },
    });
  },
};
```

Deploy:

```bash
wrangler deploy
# → https://nexus-keepalive.indooriaditya.workers.dev (cron */10 active)
```

---

## 7. Verification checklist (what we confirmed)

```bash
# tunnel shows healthy / connected
curl -s "https://api.cloudflare.com/client/v4/accounts/db646576fddd10368a374d5be7b89a1f/cfd_tunnel/247288a8-a8a3-4ffc-9e38-8abffdf27a65" \
  -H "Authorization: Bearer $CF_API_TOKEN"

# DNS resolves the hostname to the tunnel
nslookup nexus.aindoori.com

# raw origin responds 200 (keep-alive target)
curl -s -o NUL -w "%{http_code}\n" https://nexus-city-os.onrender.com/

# hitting nexus.aindoori.com returns the Cloudflare Access login (not the app)
curl -sI https://nexus.aindoori.com/   # → redirect to *.cloudflareaccess.com
```

Confirmed: only `indooriaditya@gmail.com` can complete the email-OTP, after
which the app loads with no in-app password prompt, identity taken from the
verified Access JWT.

---

## 8. Recap of issues hit & fixes

| Symptom | Root cause | Fix |
|---|---|---|
| Tunnel "connection refused" | ingress pointed at `localhost:8757`; app binds `$PORT=10000` | repointed ingress to `http://127.0.0.1:10000` |
| Site "down" intermittently | Render free tier slept → sidecar died | keep-alive Worker cron `*/10` pinging raw origin |
| Render env wiped after an update | `PUT env-vars` replaces the whole set | always GET → merge → PUT full array |
| Copilot "unknown url type: chat/completions" | relative/empty LLM base URL reached urllib | `LLMClient.configured` guard → degrade to expert system |
