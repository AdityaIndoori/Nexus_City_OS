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

# The app binds the host-provided $PORT (container hosts inject it;
# defaults to 8757 locally). The host health-check expects the app on $PORT,
# so we always bind it — whether or not a tunnel is running.
PORT="${PORT:-8757}"

if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  if command -v cloudflared >/dev/null 2>&1; then
    echo "[entrypoint] Cloudflare Tunnel token detected — starting cloudflared sidecar."
    # Point the tunnel's Public Hostname Service at http://127.0.0.1:${PORT}
    # (use 127.0.0.1, NOT 'localhost', which can resolve to IPv6 [::1] where
    # the server is not listening). The tunnel ingress is configured remotely
    # in the Zero Trust dashboard / via API — set it to this same port.
    cloudflared tunnel --no-autoupdate run --token "${CLOUDFLARE_TUNNEL_TOKEN}" &
    echo "[entrypoint] cloudflared started (pid $!); tunnel origin should be http://127.0.0.1:${PORT}."
  else
    echo "[entrypoint] WARNING: CLOUDFLARE_TUNNEL_TOKEN set but cloudflared not installed; serving directly."
  fi
else
  echo "[entrypoint] No Cloudflare Tunnel — serving the origin directly on \$PORT (${PORT})."
fi

echo "[entrypoint] Launching Nexus City OS (port ${PORT}): python platform/run.py $*"
exec python platform/run.py --host 0.0.0.0 "$@"


