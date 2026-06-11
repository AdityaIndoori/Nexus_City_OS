"""End-to-end verification of the live AI layer through the platform API:
vision analysis on a real camera + LLM-generated plan for an incident."""
import json
import urllib.request

BASE = "http://127.0.0.1:8757"


def call(path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def main():
    tok = call("/api/login", body={"user_id": "op-1",
                                   "password": "nexus-op-1"})["token"]
    grid = call("/api/grid", tok)
    # pick a well-connected live-camera intersection (downtown core) so the
    # planner has neighbor candidates
    live_iids = {c["intersection_id"] for c in grid["cameras"]
                 if c.get("live")}
    inter = next(i for i in grid["intersections"]
                 if i["id"] in live_iids and "2nd Ave" in i["name"])
    iid, name = inter["id"], inter["name"]
    print(f"VISION TEST — {name} ({iid})")
    v = call("/api/incident/analyze", tok, {"intersection_id": iid})
    print(f"  available: {v.get('available')}  model: {v.get('model')}")
    print(f"  congestion: {v.get('congestion_visible')}  "
          f"incident: {v.get('incident_visible')}  "
          f"visibility: {v.get('visibility')}  "
          f"conf: {v.get('confidence_pct')}%")
    print(f"  assessment: {v.get('assessment')}")

    print(f"\nLLM PLAN TEST — injecting collision at {name}")
    call("/api/scenario", tok, {"intersection_id": iid,
                                "anomaly": "collision"})
    status = call("/api/status", tok)
    inc = next(i for i in status["incidents"]
               if i["intersection_id"] == iid)
    call("/api/incident/ack", tok, {"incident_id": inc["id"]})
    plan = call("/api/recommend", tok, {"incident_id": inc["id"]})
    print(f"  generator:  {plan.get('generator')}")
    print(f"  model:      {plan.get('model_version')}")
    print(f"  status:     {plan.get('status')}")
    print(f"  confidence: {plan.get('confidence_score')}%")
    print(f"  targets:    {plan.get('targets')}")
    print(f"  rationale:  {plan.get('justification')[:350]}")
    # clean up
    call("/api/incident/resolve", tok,
         {"incident_id": inc["id"], "resolution": "False Alarm",
          "notes": "AI verification test"})
    print("\nincident resolved (cleanup). DONE.")


if __name__ == "__main__":
    main()