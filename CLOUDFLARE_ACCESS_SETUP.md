# Cloudflare Access (Zero Trust) — make it the ONLY sign-in, no domain needed

This turns the live Render instance into **Access-only mode**: there is no
in-app password box, the demo accounts are not seeded, and every request's
identity comes from a Cloudflare-signed Access JWT (verified in pure stdlib by
`platform/nexus/cfaccess.py`). It works **without owning a domain** by using a
**Cloudflare Tunnel** sidecar (`cloudflared`, baked into the image) that gives
you a free `https://<something>.cfargotunnel`-style public hostname you put an
Access policy on.

> ⚠️ **Order matters.** Do Steps 1–4 first. Only set the
> `NEXUS_CF_ACCESS_*` env vars (Step 5) **after** the Tunnel hostname + Access
> application exist and you can reach the app through the Cloudflare hostname.
> Setting them while still hitting `nexus-city-os.onrender.com` directly will
> lock everyone out (no password login + no Access JWT → 401 loop).

---

## Step 1 — Create a Cloudflare Tunnel and copy its token
1. Cloudflare dashboard → **Zero Trust** → **Networks → Tunnels → Create a tunnel**.
2. Connector type **Cloudflared**, name it `nexus`. Create.
3. On the "Install connector" screen, copy the **tunnel token** — the long
   string after `--token` in the shown command (starts with `ey...`). You do
   **not** run that command anywhere; the Render container runs `cloudflared`
   for you when you paste the token in Step 5a.

## Step 2 — Give the tunnel a Public Hostname pointing at the app
Still in the tunnel's config → **Public Hostname → Add a public hostname**:
- **Subdomain:** `nexus` (or anything)
- **Domain:** pick one of the Cloudflare-provided options. If you have **no
  domain**, add a free one to your account first (or use a domain you already
  manage on Cloudflare). The hostname becomes e.g. `nexus.<yourdomain>`.
- **Service → Type:** `HTTP`, **URL:** `localhost:8757`
  (the container listens on `$PORT`; Render sets `$PORT`, and the tunnel runs
  inside the same container, so `localhost:$PORT` works — `8757` is the
  default if `$PORT` is unset).
- Save. Note the full hostname (e.g. `nexus.example.com`) — call it `APP_HOST`.

> No domain at all? In Zero Trust you can still front a tunnel, but Access
> applications need a hostname. The simplest free option is to register any
> cheap domain and add it to Cloudflare (one-time), or reuse one you own.

## Step 3 — Create the Access application on that hostname
1. Zero Trust → **Access → Applications → Add an application → Self-hosted**.
2. **Application name:** `Nexus City OS`. **Application domain:** `APP_HOST`.
3. **Add a policy:** Action **Allow**; Include → **Emails** = your email
   (add teammates), or **Emails ending in** = `@yourorg.com`.
4. Save.

## Step 4 — Copy the two identifiers the app needs
- **AUD tag:** open the app → it shows the **Application Audience (AUD) Tag**
  (a 64-char hex string). Copy it → `AUD`.
- **Team domain:** Zero Trust → **Settings → Custom Pages** (or the URL of
  your Zero Trust org) shows `https://<team>.cloudflareaccess.com`. Copy
  `<team>.cloudflareaccess.com` → `TEAM_DOMAIN`.

## Step 5 — Set the env vars on Render (then it's live)

**5a. Tunnel token** (starts the sidecar; app still has its normal login until
5b):
```
render env set --service srv-d8oa981o3t8c73depee0 \
  CLOUDFLARE_TUNNEL_TOKEN=<paste the ey... token>
```
Render redeploys; visit `https://APP_HOST` — Cloudflare Access challenges you,
then the app loads (still with the normal login behind it). Confirm the tunnel
is "Healthy" in the dashboard.

**5b. Flip to Access-only mode** (removes the password box entirely):
```
render env set --service srv-d8oa981o3t8c73depee0 \
  NEXUS_CF_ACCESS_TEAM_DOMAIN=<TEAM_DOMAIN> \
  NEXUS_CF_ACCESS_AUD=<AUD> \
  NEXUS_CF_ACCESS_ADMINS=<your-email>[,more-emails] \
  NEXUS_CF_ACCESS_DEFAULT_ROLE=viewer
```
After the redeploy, `https://APP_HOST` shows **no login form** — you're signed
in as your Cloudflare identity; "Sign out" routes to `/cdn-cgi/access/logout`.
Optional role lists: `NEXUS_CF_ACCESS_OPERATORS`, `NEXUS_CF_ACCESS_ANALYSTS`,
`NEXUS_CF_ACCESS_VIEWERS` (comma-separated emails).

> You can also set all of these in the Render dashboard → service →
> **Environment** instead of the CLI.

## Step 6 — (recommended) Stop allowing the raw origin
Once the tunnel hostname works, your `*.onrender.com` URL still bypasses
Access. Lock it down:
- Render → service → **Settings**, or
- Keep `NEXUS_CF_ACCESS_*` set: the app then rejects any request without a
  valid Access JWT (401) even on the raw origin, so direct hits can't get in.
  (Access verification happens in-app, so the origin is safe either way once
  Step 5b is set.)

---

## How it verifies (the trust model)
- `cloudflared` forwards the Access-stamped request, which carries the signed
  `Cf-Access-Jwt-Assertion` header / `CF_Authorization` cookie.
- `nexus/cfaccess.py` fetches your team's JWKS
  (`https://<team>/cdn-cgi/access/certs`), verifies the JWT's **RS256**
  signature with pure big-int math, and checks **issuer**, **audience (AUD)**,
  and **expiry**. The bare email header is never trusted on its own.
- Email → role mapping uses the `NEXUS_CF_ACCESS_*` lists; unmapped
  authenticated users get `NEXUS_CF_ACCESS_DEFAULT_ROLE` (default `viewer`).
- Covered by `platform/tests/test_cfaccess.py` (RS256 verify, tamper / wrong
  aud / wrong iss / expired / unknown-kid rejection, role mapping).

## Rollback
Remove the four `NEXUS_CF_ACCESS_*` vars (and optionally
`CLOUDFLARE_TUNNEL_TOKEN`) on Render → the app returns to its normal
audit-logged password login on the next deploy.
