# Nexus City OS — zero-dependency Python stdlib platform.
# No pip install step: the platform uses only the standard library.
FROM python:3.12-slim

WORKDIR /app

# Optional Cloudflare Tunnel sidecar (only RUNS when CLOUDFLARE_TUNNEL_TOKEN
# is set at runtime). Installing the static binary adds no Python deps and
# keeps the image self-contained; it lets the app sit behind Cloudflare —
# and thus Cloudflare Access / Zero Trust — without owning a domain.
ARG TARGETARCH=amd64
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH}" \
         -o /usr/local/bin/cloudflared; \
    chmod +x /usr/local/bin/cloudflared; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

COPY platform/ ./platform/
COPY models.json ./models.json

RUN chmod +x platform/scripts/entrypoint.sh

# Persistent state (SQLite store, road-geometry cache) lives here;
# mount a volume at /app/platform/data to survive container rebuilds.
VOLUME ["/app/platform/data"]

EXPOSE 8757

# The entrypoint starts the server and, when CLOUDFLARE_TUNNEL_TOKEN is set,
# a cloudflared sidecar. Extra flags (e.g. --city tacoma --sim) appended via
# `docker run ... <flags>` pass straight through to python platform/run.py.
ENTRYPOINT ["platform/scripts/entrypoint.sh"]
