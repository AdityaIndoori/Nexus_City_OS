"""Verify copilot answers contain intersection names that match the
UI's location-chip scanner (locChipsFor logic mirrored here)."""
import json
import urllib.request

B = "http://127.0.0.1:8757"


def call(p, t=None, b=None):
    h = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = "Bearer " + t
    d = json.dumps(b).encode() if b is not None else None
    req = urllib.request.Request(B + p, data=d, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


tok = call("/api/login", b={"user_id": "op-1",
                            "password": "nexus-op-1"})["token"]
grid = call("/api/grid", tok)
r = call("/api/copilot/query", tok,
         {"text": "Which intersections are most congested right now? "
                  "Name them exactly."})
answer = r["answer"]
print("ANSWER:", answer[:400])
lower = answer.lower()
matches = [i["name"] for i in grid["intersections"]
           if len(i["name"]) > 6 and i["name"].lower() in lower]
print("\nLocation chips that would render:", len(matches))
for m in matches[:8]:
    print("  📍", m)