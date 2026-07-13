# Dockerized deployment with boot-time (pre-login) autostart on Windows

> **UPDATE 2026-07-13 — tunnel moved out of compose on the primary host.**
> The Cloudflare tunnel (`nexus-local`) now runs as the **`Cloudflared`
> Windows service** on the host (config:
> `C:\Windows\System32\config\systemprofile\.cloudflared\config.yml`,
> mirrored from `C:\Users\indoo\.cloudflared\config.yml`). The service
> routes `nexus.aindoori.com` / `nexuscity.aindoori.com` to the container's
> published host port `127.0.0.1:8757`, plus `ssh.aindoori.com` to host
> sshd, so the compose `cloudflared` sidecar is **no longer needed here**.
>
> **Do NOT run `docker compose --profile tunnel up` on this host.** Two
> connectors on the same tunnel made Cloudflare load-balance sessions
> between two different ingress configs, randomly dropping SSH/HTTP
> connections (broken pipe). The boot task (`boot-docker-up.ps1`) now runs
> plain `docker compose up -d`. The `tunnel` profile remains available for
> deploying on a machine that has no other connector for this tunnel. The
> `--profile tunnel` commands in the rest of this document apply only to
> such a machine.

How to run the entire Nexus City OS stack — platform + Cloudflare Tunnel —
as Docker containers that come up **when Windows boots, before anyone signs
in**. This replaces the Startup-folder launcher (`start-nexus-hidden.vbs`),
which only runs at user logon.

## The stack

`docker-compose.yml` defines two services:

| Service | What it does |
|---|---|
| `nexus` | The platform (python:3.12-slim, zero pip deps). Binds `127.0.0.1:8757` on the host (loopback only). SQLite state persists in the `nexus-data` volume. `restart: always`. |
| `cloudflared` (profile `tunnel`) | Official Cloudflare image running the SAME named tunnel as the non-Docker deployment. Ingress (`cloudflared/config-docker.yml`) routes `nexus.aindoori.com` and `nexuscity.aindoori.com` → `http://nexus:8757` over the compose network. `restart: always`. |

Because public traffic enters through the tunnel container, **no inbound
port is exposed** beyond host loopback.

## One-time setup

```powershell
cd d:\Software_Projects\NexusCityOS

# 1. Environment (secrets are git-ignored)
copy .env.example .env
notepad .env      # fill in NEXUS_CF_ACCESS_AUD, ADMINS, LLM gateway, WSDOT code
#    also set CLOUDFLARED_DIR=C:/Users/<you>/.cloudflared

# 2. Build the image
docker compose build

# 3. First run (foreground once, to watch it come up)
docker compose --profile tunnel up
#    verify https://nexuscity.aindoori.com then Ctrl+C

# 4. Detached
docker compose --profile tunnel up -d
```

> The tunnel container reuses the credentials already on this machine
> (`~/.cloudflared/cert.pem` + `<tunnel-id>.json`), mounted read-only. The
> DNS CNAMEs for both hostnames already exist. **Stop the non-Docker
> deployment first** (kill the python on 8757 + host cloudflared, and remove
> `NexusCityOS.lnk` from `shell:startup`) or the two will race for the tunnel.

## Boot-time start (pre-login) — two layers

### Layer 1 — the Docker engine itself must start at boot

Pick one:

* **Docker Desktop:** Settings → General → enable
  **"Start Docker Desktop when you sign in to your computer"**
  (`autoStart: true` in `%APPDATA%\Docker\settings.json` — already set on
  this machine). Note: Docker Desktop ≤ 4.36 (this machine runs 4.34) has
  **no true pre-login engine service** — the engine starts at user sign-in.
  The boot runner (`boot-docker-up.ps1`) additionally best-effort launches
  Docker Desktop if the engine is down and waits up to 5 minutes.
  For genuine before-sign-in start, either upgrade to a Docker Desktop
  version exposing "Start Docker Desktop before you sign in" / the Windows
  service option, or enable Windows **auto-logon** for this machine
  (netplwiz) so sign-in happens automatically at boot.
* **Without Docker Desktop:** run the engine inside WSL2 and enable WSL
  boot-time start (`wsl.exe --install`, then a scheduled task running
  `wsl -d <distro> -u root service docker start` at startup), or install
  the community `dockerd` Windows service. Docker Desktop is simpler.

### Layer 2 — the stack must come up once the engine is ready

Run the installer **from an elevated PowerShell**:

```powershell
powershell -ExecutionPolicy Bypass -File install-docker-autostart.ps1
```

It registers a Scheduled Task `NexusCityOS-Docker` that:
* triggers **At startup** (30 s delay), running as **SYSTEM** — no logon needed;
* waits up to 5 minutes for `docker info` to succeed (engine warm-up);
* runs `docker compose --profile tunnel up -d` in the repo directory;
* logs to `docker-autostart.log`; retries 3× on failure.

Test without rebooting:

```powershell
Start-ScheduledTask -TaskName NexusCityOS-Docker
Get-Content docker-autostart.log
docker compose ps
```

Strictly speaking, once the containers exist with `restart: always`, the
Docker engine restarts them by itself on boot — the scheduled task is a
belt-and-braces guarantee (it also handles the first boot after an image
rebuild or a `compose down`).

## What survives a reboot

* **SQLite state** (audit chain, incidents, plans, mode) — in the
  `nexus-data` volume.
* **Tunnel identity + DNS** — nothing to redo; the sidecar reconnects.
* **Total downtime** ≈ boot time + Docker engine start + ~10 s.

## Updating to a new build

```powershell
git pull
docker compose build
docker compose --profile tunnel up -d    # recreates only what changed
```

## Rollback to the non-Docker deployment

```powershell
docker compose --profile tunnel down
Unregister-ScheduledTask -TaskName NexusCityOS-Docker
# restore NexusCityOS.lnk in shell:startup, run start-nexus.ps1
```
