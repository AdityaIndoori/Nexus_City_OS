"""Reset demo state: resolve open incidents, return mode to shadow."""
import json
import urllib.request

B = "http://127.0.0.1:8757"


def call(p, t=None, b=None):
    h = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = "Bearer " + t
    d = json.dumps(b).encode() if b is not None else None
    req = urllib.request.Request(B + p, data=d, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


tok = call("/api/login", b={"user_id": "admin-1",
                            "password": "nexus-admin-1"})["token"]
st = call("/api/status", tok)
for inc in st["incidents"]:
    call("/api/incident/resolve", tok,
         {"incident_id": inc["id"], "resolution": "Resolved",
          "notes": "demo cleanup"})
    print("resolved", inc["id"])
call("/api/mode", tok, {"mode": "shadow"})
print("mode -> shadow")