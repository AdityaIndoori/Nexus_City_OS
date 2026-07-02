"""One-shot check: incident dict headline fields (camera_name fallback)."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8899"


def call(path, body=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


tok = call("/api/login", {"user_id": "op-1", "password": "nexus-op-1"})["token"]
grid = call("/api/grid", tok=tok)
incs = []
for inter in grid["intersections"][:8]:
    call("/api/scenario",
         {"intersection_id": inter["id"], "anomaly": "collision"}, tok)
    for _ in range(10):      # detector needs a few telemetry cycles
        time.sleep(2)
        incs = call("/api/incidents?window=3600", tok=tok)["incidents"]
        if incs:
            break
    if incs:
        break
i = incs[0]
out = ("intersection_name: %r\ncamera_name: %r\nheadline: %r\n" % (
    i.get("intersection_name"), i.get("camera_name"),
    i.get("camera_name") or i.get("intersection_name")))
sys.stdout.write(out)
with open("verify-headline.txt", "w") as f:
    f.write(out)