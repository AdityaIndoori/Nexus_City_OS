# Cloudflare Access (Zero Trust) — make it the ONLY sign-in

This turns the live instance into **Access-only mode**: there is no in-app
password box, the demo accounts are not seeded, and every request's identity
comes from a Cloudflare-signed Access JWT (verified in pure stdlib by
`platform/nexus/cfaccess.py`). The reference deployment runs the platform on
a local machine and publishes it through a **Cloudflare Tunnel**
(`cloudflared`) — no inbound ports, free HTTPS, and Cloudflare's edge in
front of everything.

> ⚠️ **Order matters.** Do Steps 1–4 first. Only set the
> `NEXUS_CF_ACCESS_*` env vars (Step 5) **after** the Tunnel hostname + Access
> application exist and you can reach the app through the Cloudflare hostname.
> Setting them while the tunnel isn't up yet will lock everyone out
> (no password login + no Access JWT → 401 loop).

---

## Step 1 — Create a Cloudflare Tunnel and run the connector
1. Cloudflare dashboard → **Zero Trust** → **Networks → Tunnels → Create a tunnel**.
2. Connector type **Cloudflared**, name it `nexus`. Create.
3. On the "Install connector" screen, follow the Windows install command —
   it installs `cloudflared` as a service on this machine with the shown
   tunnel token (starts with `ey...`). The connector should show
   **Healthy** in the dashboard once running.

## Step 2 — Give the tunnel a Public Hostname pointing at the app
Still in the tunnel's config → **Public Hostname → Add a public hostname**:
- **Subdomain:** `nexus` (or anything)
- **Domain:** a domain you manage on Cloudflare (e.g. `aindoori.com` →
  the hostname becomes `nexus.aindoori.com`).
- **Service → Type:** `HTTP`, **URL:** `localhost:8757`
  (the platform's default port; match whatever `--port` you launch with).
- Save. Note the full hostname (e.g. `nexus.aindoori.com`) — call it
  `APP_HOST`.

## Step 3 — Create the Access application on that hostname
1. Zero Trust → **Access → Applications → Add an application → Self-hosted**.
2. **Application name:** `Nexus City OS`. **Application domain:** `APP_HOST`.
3. **Add a policy:** Action **Allow**; Include → **Emails** = your email
   (add teammates), or **Emails ending in** = `@yourorg.com`.
4. Save.

> Tip: to keep the marketing page public while gating the console, add a
> second Access application for `APP_HOST/landing` with a **Bypass** policy
> (Everyone) — `/landing` is served without app auth either way.

## Step 4 — Copy the two identifiers the app needs
- **AUD tag:** open the app → it shows the **Application Audience (AUD) Tag**
  (a 64-char hex string). Copy it → `AUD`.
- **Team domain:** Zero Trust → **Settings → Custom Pages** (or the URL of
  your Zero Trust org) shows `https://<team>.cloudflareaccess.com`. Copy
  `<team>.cloudflareaccess.com` → `TEAM_DOMAIN`.

## Step 5 — Set the env vars and restart the platform

Set these in the environment the platform runs under (e.g. the shell /
scheduled task / `start-nexus-hidden.vbs` launcher on this machine), then
restart the process:

```
set NEXUS_CF_ACCESS_TEAM_DOMAIN=<TEAM_DOMAIN>
set NEXUS_CF_ACCESS_AUD=<AUD>
set NEXUS_CF_ACCESS_ADMINS=<your-email>[,more-emails]
set NEXUS_CF_ACCESS_DEFAULT_ROLE=viewer
python platform/run.py
```

After the restart, `https://APP_HOST` shows **no login form** — you're signed
in as your Cloudflare identity; "Sign out" routes to `/cdn-cgi/access/logout`.
Optional role lists: `NEXUS_CF_ACCESS_OPERATORS`, `NEXUS_CF_ACCESS_ANALYSTS`,
`NEXUS_CF_ACCESS_VIEWERS` (comma-separated emails).

## Step 6 — Origin exposure
With a Tunnel there is **no public origin to lock down** — the machine
accepts no inbound connections; `cloudflared` dials out to Cloudflare. Keep
the app bound to `127.0.0.1` (the default) so nothing on the local network
can reach it directly either. With `NEXUS_CF_ACCESS_*` set, the app also
rejects any request without a valid Access JWT (401), so even local hits
can't bypass Access.

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
Remove the `NEXUS_CF_ACCESS_*` vars and restart → the app returns to its
normal audit-logged password login.