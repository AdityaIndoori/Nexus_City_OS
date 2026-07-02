"""Inject test incidents into a running dev instance (default :8899).

Login as op-1, then inject collision scenarios across several intersections
with a retry loop (the anomaly detector has a per-intersection cooldown).
Prints how many incidents + pending plans exist afterwards.
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:" + (sys.argv[1] if len(sys.argv) > 1 else "8899")


def call(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    tok = call("/api/login", {"user_id": "op-1", "password": "nexus-op-1"})["token"]
    grid = call("/api/grid", token=tok)
    iids = [i["id"] for i in grid["intersections"]][:8]
    raised = 0
    for attempt in range(12):
        for iid in iids:
            try:
                call("/api/scenario", {"intersection_id": iid,
                                       "anomaly": "collision"}, token=tok)
            except Exception:
                pass
        time.sleep(2)
        st = call("/api/status", token=tok)
        incs = st.get("incidents", [])
        raised = len(incs)
        if raised >= 2:
            break
    st = call("/api/status", token=tok)
    incs = st.get("incidents", [])
    plans = st.get("plans", [])
    out = {
        "incidents": [{"id": i["id"], "state": i["state"],
                       "severity": i["severity"]} for i in incs],
        "plans": [{"plan_id": p["plan_id"], "status": p["status"],
                   "has_hash": bool(p.get("plan_hash"))} for p in plans],
    }
    # Request a recommendation for the first detected/acknowledged incident
    for i in incs:
        if i["state"] in ("detected", "acknowledged", "monitoring"):
            try:
                if i["state"] == "detected":
                    call("/api/incident/ack", {"incident_id": i["id"]}, token=tok)
                p = call("/api/recommend", {"incident_id": i["id"]}, token=tok)
                out["recommended"] = {"plan_id": p.get("plan_id"),
                                      "status": p.get("status"),
                                      "plan_hash": p.get("plan_hash", "")[:16]}
            except Exception as e:
                out["recommend_error"] = str(e)
            break
    with open("inject-result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    sys.stdout.buffer.write((json.dumps(out, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()