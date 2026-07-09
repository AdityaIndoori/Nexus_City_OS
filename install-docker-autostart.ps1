# Nexus City OS - install BOOT-TIME (pre-login) Docker autostart.
#
# Creates a Windows Scheduled Task that runs AT SYSTEM STARTUP as SYSTEM
# (no user logon required) and brings up the dockerized stack:
#   docker compose --profile tunnel up -d
#
# REQUIREMENTS (one-time, see DOCKER_AUTOSTART.md):
#   * Docker engine must itself start at boot without login (Docker Desktop
#     "Start Docker Desktop before you sign in" setting, or a dockerd service).
#   * Copy .env.example to .env and fill in secrets (git-ignored).
#   * The stack must have been built once: docker compose build
#
# Run elevated:
#   powershell -ExecutionPolicy Bypass -File install-docker-autostart.ps1

$ErrorActionPreference = "Stop"
$Root = "d:\Software_Projects\NexusCityOS"
$TaskName = "NexusCityOS-Docker"
Start-Transcript -Path "$Root\install-task.log" -Force | Out-Null

if (-not ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script from an ELEVATED PowerShell (task runs as SYSTEM)."
}

# The task runs the dedicated runner script (boot-docker-up.ps1): waits for
# the Docker engine, runs compose via cmd /c (so docker's stderr progress
# output cannot become a PowerShell error), exits with compose's real code.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\boot-docker-up.ps1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"          # let networking/Docker service settle
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
    -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Nexus City OS - docker compose up at boot (pre-login)" | Out-Null

Write-Host "Installed scheduled task '$TaskName' (runs as SYSTEM at boot)."
Write-Host "Test now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Remove with:    Unregister-ScheduledTask -TaskName $TaskName"
Stop-Transcript | Out-Null