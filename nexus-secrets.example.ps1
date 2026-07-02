# Nexus City OS — per-machine data-feed secrets (TEMPLATE)
#
# Copy this file to nexus-secrets.ps1 (git-ignored), fill in real values,
# then re-run start-nexus.ps1. The launcher dot-sources nexus-secrets.ps1
# automatically before starting the platform.

# WSDOT Traveler Information API access code (FREE, instant):
#   1. Open https://wsdot.wa.gov/traffic/api/
#   2. Enter your email → the access code is issued immediately.
# Enables: WSDOT loop-detector flow, corridor travel times, highway alerts.
# $env:WSDOT_ACCESS_CODE = "PASTE-YOUR-CODE-HERE"

# Waze for Cities (CCP) partner feed URL (requires an approved partnership:
# https://www.waze.com/wazeforcities). Enables crowdsourced jams + alerts.
# $env:NEXUS_WAZE_FEED_URL = "https://www.waze.com/partnerhub-api/feeds/..."