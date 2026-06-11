"""Check how many segments have resolved real road paths."""
import json
import urllib.request

B = "http://127.0.0.1:8757"
req = urllib.request.Request(
    B + "/api/login",
    data=json.dumps({"user_id": "op-1", "password": "nexus-op-1"}).encode(),
    headers={"Content-Type": "application/json"})
tok = json.loads(urllib.request.urlopen(req, timeout=30).read())["token"]
g = json.loads(urllib.request.urlopen(urllib.request.Request(
    B + "/api/grid", headers={"Authorization": "Bearer " + tok}),
    timeout=30).read())
print("road_geometry:", g.get("road_geometry"))
withpath = [s for s in g["segments"] if "path" in s]
print("segments with real path:", len(withpath), "/", len(g["segments"]))
if withpath:
    print("sample path points:", len(withpath[0]["path"]))