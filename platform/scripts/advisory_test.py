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


tok = call("/api/login", b={"user_id": "admin-1",
                            "password": "nexus-admin-1"})["token"]
call("/api/mode", tok, {"mode": "advisory"})
print("mode -> advisory")
st = call("/api/status", tok)
pending = [p for p in st["plans"] if p["status"] == "pending_approval"]
print("pending plans:", [p["plan_id"] for p in pending])
if pending:
    r = call("/api/plan/approve", tok, {"plan_id": pending[0]["plan_id"]})
    print("approved ->", r["status"])
    ins = call("/api/plan/instruction?id=" + pending[0]["plan_id"], tok)
    print("instruction priority:", ins["priority"],
          "| lines:", len(ins["instructions"]))
    for line in ins["instructions"]:
        print("  ", line["intersection"], "->", line["requested_change"])