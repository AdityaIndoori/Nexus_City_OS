# Nexus City OS - boot-time runner (invoked by the NexusCityOS-Docker
# scheduled task as SYSTEM at startup). Waits for the Docker engine, then
# brings up the dockerized stack. Log: docker-autostart.log
$Root = "d:\Software_Projects\NexusCityOS"
# Best effort: if the engine is down, try to launch Docker Desktop.
# (On Docker Desktop <= 4.36 there is no pre-login engine service; the
# engine reliably starts at user sign-in via autoStart=true. This launch
# attempt helps where a service/session is available.)
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) {
        Start-Process $dd -ErrorAction SilentlyContinue
    }
}
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 10
}
Set-Location $Root
# NOTE: the "tunnel" profile is intentionally NOT enabled here. The
# Cloudflare tunnel now runs on the host as the "Cloudflared" Windows
# service (config: C:\Windows\System32\config\systemprofile\.cloudflared\).
# Running the compose cloudflared sidecar too would register a SECOND
# connector on the same tunnel and randomly break SSH/HTTP sessions.
$out = cmd /c "docker compose up -d 2>&1"
$rc = $LASTEXITCODE
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] compose exit=$rc" | Out-File "$Root\docker-autostart.log" -Encoding ascii
$out | Out-File "$Root\docker-autostart.log" -Append -Encoding ascii
exit $rc