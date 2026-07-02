' Nexus City OS — hidden launcher (no console window).
' Runs the idempotent PowerShell start script that brings up the Nexus
' platform + the Cloudflare Tunnel for https://nexus.aindoori.com.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""d:\Software_Projects\NexusCityOS\start-nexus.ps1""", 0, False
