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
$out = cmd /c "docker compose --profile tunnel up -d 2>&1"
$rc = $LASTEXITCODE
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] compose exit=$rc" | Out-File "$Root\docker-autostart.log" -Encoding ascii
$out | Out-File "$Root\docker-autostart.log" -Append -Encoding ascii
exit $rc