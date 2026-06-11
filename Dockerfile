# Nexus City OS — zero-dependency Python stdlib platform.
# No pip install step: the platform uses only the standard library.
FROM python:3.12-slim

WORKDIR /app

COPY platform/ ./platform/
COPY models.json ./models.json

# Persistent state (SQLite store, road-geometry cache) lives here;
# mount a volume at /app/platform/data to survive container rebuilds.
VOLUME ["/app/platform/data"]

EXPOSE 8757

# Bind all interfaces inside the container. Add e.g.
#   --city tacoma --no-vision --sim
# via `docker run ... <flags>` (CMD args are appended to ENTRYPOINT).
ENTRYPOINT ["python", "platform/run.py", "--host", "0.0.0.0"]