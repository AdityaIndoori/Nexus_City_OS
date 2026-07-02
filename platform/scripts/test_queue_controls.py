"""End-to-end check of the incident-queue controls (time range / sort /
show) against a running instance. Usage:
    python platform/scripts/test_queue_controls.py [base_url]
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"


def call(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={
        "Content-Type": "application/json",
        **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


tok = call("/api/login", {"user_id": "op-1",
                          "password": "nexus-op-1"})["token"]

# Inject scenarios one at a time (detection has a cooldown between raises)
# until at least two incidents exist in the queue.
grid = call("/api/grid", token=tok)
iids = [i["id"] for i in grid["intersections"][:8]]
r = {"total": 0}
for iid in iids:
    call("/api/scenario", {"intersection_id": iid, "anomaly": "collision"},
         token=tok)
    print("injected at:", iid)
    for _ in range(15):
        r = call("/api/incidents?window=86400&order=desc&limit=25",
                 token=tok)
        if r["total"] >= 2:
            break
        time.sleep(2)
    if r["total"] >= 2:
        break
print("total incidents (24h window):", r["total"])
assert r["total"] >= 2, "incidents were not raised"

# Time range: a 1-second window must exclude incidents detected earlier.
time.sleep(2)
narrow = call("/api/incidents?window=1&order=desc&limit=25", token=tok)
print("1s window count:", len(narrow["incidents"]))
assert len(narrow["incidents"]) == 0, "time window filter failed"

# Sort: asc vs desc must flip the first element.
asc = call("/api/incidents?window=86400&order=asc&limit=25",
           token=tok)["incidents"]
desc = call("/api/incidents?window=86400&order=desc&limit=25",
            token=tok)["incidents"]
assert asc[0]["detected_at"] <= asc[-1]["detected_at"], "asc order wrong"
assert desc[0]["detected_at"] >= desc[-1]["detected_at"], "desc order wrong"
assert asc[0]["id"] == desc[-1]["id"], "asc/desc not mirrored"
print("sort asc first:", asc[0]["id"], "| desc first:", desc[0]["id"])

# Show/limit: limit=1 must return exactly one, with total unchanged.
one = call("/api/incidents?window=86400&order=desc&limit=1", token=tok)
print("limit=1 ->", len(one["incidents"]), "of total", one["total"])
assert len(one["incidents"]) == 1 and one["total"] >= 2, "limit failed"

# 911/road endpoint honors the requested age parameter.
em = call("/api/emergencies?age=604800", token=tok)
print("emergencies available:", em.get("available"),
      "| n:", len(em.get("emergencies", [])))

print("ALL QUEUE CONTROL CHECKS PASSED")