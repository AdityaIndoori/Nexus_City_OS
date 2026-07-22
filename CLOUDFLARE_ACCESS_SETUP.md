# Cloudflare Access (Zero Trust) setup

Cloudflare Access is **the only identity layer** — there is no in-app
password box, and no fallback. Every request's identity comes from a
Cloudflare-signed Access JWT (verified in pure stdlib by
`platform/nexus/cfaccess.py`). The reference deployment runs the platform on
a local machine and publishes it through a **Cloudflare Tunnel**
(`cloudflared`) — no inbound ports, free HTTPS, and Cloudflare's edge in
front of everything.

> ⚠️ **Order matters.** Do Steps 1–4 first. Only set the
> `NEXUS_CF_ACCESS_*` env vars (Step 5) **after** the Tunnel hostname + Access
> application exist and you can reach the app through the Cloudflare hostname.
> Setting them too early locks everyone out — the server refuses to start
> once Access is configured unless it can reach a request carrying a valid
> Access JWT, and there is no password login to fall back to.

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

> Tip: to keep the marketing page public while gating the console, give the
> landing page **its own subdomain** on the same tunnel (this is how the
> reference deployment runs it): add a second ingress hostname (e.g.
> `nexuscity.aindoori.com` → the same `http://localhost:8757`) in
> `~/.cloudflared/config.yml`, route DNS with
> `cloudflared tunnel route dns <tunnel> nexuscity.aindoori.com`, and set
> `NEXUS_LANDING_HOST=nexuscity.aindoori.com` +
> `NEXUS_CONSOLE_URL=https://nexus.aindoori.com/` in the launcher. Requests
> arriving with the landing Host get ONLY the marketing page (no console, no
> API); the console hostname stays fully Access-gated. Do **not** add an
> Access application on the landing hostname.

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
`NEXUS_CF_ACCESS_VIEWERS`, `NEXUS_CF_ACCESS_CITIZENS` (comma-separated
emails) — citizens are limited to the civilian Community Watch API only.

## Step 6 — Origin exposure
With a Tunnel there is **no public origin to lock down** — the machine
accepts no inbound connections; `cloudflared` dials out to Cloudflare. Keep
the app bound to `127.0.0.1` (the default) so nothing on the local network
can reach it directly either. With `NEXUS_CF_ACCESS_*` set, the app also
rejects any request without a valid Access JWT (401), so even local hits
can't bypass Access.

---

## A second, path-scoped Access app (e.g. Community Watch on `/community*`)

One hostname can carry two Access applications when the second one is
scoped to a more specific path — Cloudflare picks the most-specific-path
match, so an app on `nexus.aindoori.com/community*` wins over the root app
on `nexus.aindoori.com` for requests under `/community`.

1. Zero Trust → **Access → Applications → Add an application → Self-hosted**.
2. **Application domain:** `nexus.aindoori.com/community*` (path suffix
   `*` matters — without it the app only matches the exact path). Give it
   its own Allow policy (e.g. "allow everyone with email OTP" for a public
   citizen pilot, independent of the console's invite-only policy).
3. Copy this app's own **AUD tag** and append it, comma-separated, to
   `NEXUS_CF_ACCESS_AUD` — e.g.
   `NEXUS_CF_ACCESS_AUD=<console-aud>,<community-aud>`. The origin accepts
   a JWT from either application.
4. Map citizen emails with `NEXUS_CF_ACCESS_CITIZENS` (or make `citizen` the
   `NEXUS_CF_ACCESS_DEFAULT_ROLE` if the community app's policy is "allow
   everyone").

**The role map — not the AUD — is the authorization boundary** between the
operator console and Community Watch. A citizen-role JWT is accepted on
console routes too (both AUDs are trusted at the origin); `server.py`'s
citizen gate is what actually confines that identity to `/api/community/*`.
Don't rely on the second Access app's policy alone to keep citizens out of
the console.

## Service tokens (machine / MCP clients)

Human logins go through a browser OTP/SSO flow; machine clients (MCP tools,
CI, scripts) authenticate with a Cloudflare Access **service token** instead —
a client ID + secret pair sent as headers on every request, no browser
involved.

1. Zero Trust → **Access → Service Auth → Service Tokens → Create Service
   Token**. Name it (e.g. `nexus-mcp-ci`). Copy the **Client ID** and
   **Client Secret** — the secret is shown once.
2. On the Access application (console or community app), add a policy with
   Action **Service Auth**, Include → **Service Token** = the token you
   created. This lets the token in without an interactive login.
3. **Turn OFF "Require binding cookie"** on the Access application — it's
   an anti-token-replay feature for browser sessions and machine clients
   can't satisfy it; leaving it on makes every service-token request fail.
4. Map the token to a role with `NEXUS_CF_ACCESS_SERVICE_ROLES`:
   `<client-id>:<role>` (comma-separated for multiple tokens). Unmapped
   tokens default to `viewer`; service principals can never be `citizen`.
5. The client sends `CF-Access-Client-Id` and `CF-Access-Client-Secret`
   headers on every request (no cookie, no interactive step). At the
   origin, `cfaccess.py` sees a JWT with no `email` claim and a
   `common_name` equal to the client ID, and maps it to principal
   `svc:<client-id>` with the role from `NEXUS_CF_ACCESS_SERVICE_ROLES`.

See `docs/mcp-connect.md` for the MCP client-side configuration.

---

## How it verifies (the trust model)
- `cloudflared` forwards the Access-stamped request, which carries the signed
  `Cf-Access-Jwt-Assertion` header / `CF_Authorization` cookie.
- `nexus/cfaccess.py` fetches your team's JWKS
  (`https://<team>/cdn-cgi/access/certs`), verifies the JWT's **RS256**
  signature with pure big-int math, and checks **issuer**, **audience (AUD,
  matched against the comma-separated set)**, and **expiry**. The bare email
  header is never trusted on its own.
- Email → role mapping uses the `NEXUS_CF_ACCESS_*` lists; unmapped
  authenticated users get `NEXUS_CF_ACCESS_DEFAULT_ROLE` (default `viewer`).
  Service-token assertions (no `email`, a `common_name`) map through
  `NEXUS_CF_ACCESS_SERVICE_ROLES` instead, and never resolve to `citizen`.
- Covered by `platform/tests/test_cfaccess.py` (RS256 verify, tamper / wrong
  aud / wrong iss / expired / unknown-kid rejection, role mapping incl.
  citizen and service-token principals).

## Rollback
There is no password fallback — removing the `NEXUS_CF_ACCESS_*` vars just
makes the server refuse to start (it requires either Access or an explicit
`NEXUS_DEV_IDENTITY`; see `.env.example`). To roll back a deployment,
`git revert` the commit(s) that introduced Access-only auth and redeploy the
prior build.