"""Verify the 911 emergency layer end-to-end through the API."""
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
em = call("/api/emergencies?age=3600", tok)
print(f"EMERGENCIES: available={em['available']}, "
      f"count={len(em['emergencies'])}, hazards={len(em['hazards'])}")
for e in em["emergencies"][:5]:
    print(f"  {e['type']:35s} {e['category']:10s} "
          f"{'TRAFFIC' if e['traffic_impacting'] else '       '} "
          f"{e['address']}")

print("\nCOPILOT 911 GROUNDING:")
r = call("/api/copilot/query", tok,
         {"text": "Summarize current 911 emergency activity in the city."})
print(" ", r["answer"][:500])