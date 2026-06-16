#!/usr/bin/env sh
# Nexus City OS — container entrypoint.
#
# Starts the platform server and, OPTIONALLY, a Cloudflare Tunnel sidecar so
# the app can be fronted by Cloudflare (and gated by Cloudflare Access /
# Zero Trust) WITHOUT owning a domain.
#
#   * If $CLOUDFLARE_TUNNEL_TOKEN is set, `cloudflared tunnel run` is launched
#     in the background first. It dials OUT to Cloudflare's edge (no inbound
#     port needed) and forwards the tunnel's public hostname to the local
#     server on 127.0.0.1:$PORT. Put a Cloudflare Access policy on that
#     hostname and set NEXUS_CF_ACCESS_TEAM_DOMAIN + NEXUS_CF_ACCESS_AUD to
#     make Access the only sign-in path.
#   * If the token is unset, the platform just serves directly (unchanged).
#
# All server flags pass through:  ENTRYPOINT args + CMD → python run.py.
set -eu

PORT="${PORT:-8757}"

if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  if command -v cloudflared >/dev/null 2>&1; then
    echo "[entrypoint] Cloudflare Tunnel token detected — starting cloudflared sidecar."
    # The tunnel's Public Hostname (configured in the Zero Trust dashboard)
    # must point its Service at http://localhost:${PORT}.
    cloudflared tunnel --no-autoupdate run --token "${CLOUDFLARE_TUNNEL_TOKEN}" &
    echo "[entrypoint] cloudflared started (pid $!)."
  else
    echo "[entrypoint] WARNING: CLOUDFLARE_TUNNEL_TOKEN set but cloudflared not installed; serving directly."
  fi
else
  echo "[entrypoint] No Cloudflare Tunnel token — serving the origin directly."
fi

echo "[entrypoint] Launching Nexus City OS: python platform/run.py $* (PORT=${PORT})"
exec python platform/run.py --host 0.0.0.0 "$@"
