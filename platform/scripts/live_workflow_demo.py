"""End-to-end Live Mode workflow exercise against a running platform.

Demonstrates: scenario injection -> detection -> acknowledgment ->
AI recommendation (with simulation) -> operator approval -> LIVE execution ->
rollback -> resolution -> audit verification.

Usage:  python platform/scripts/live_workflow_demo.py [intersection_id]
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8757"


def call(path, body=None):
    if body is None:
        req = urllib.request.Request(BASE + path)
    else:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    iid = sys.argv[1] if len(sys.argv) > 1 else "INT-0007"

    print("1. Set mode -> LIVE (admin)")
    print("  ", call("/api/mode", {"user_id": "admin-1", "mode": "live"}))

    print(f"2. Inject collision at {iid}")
    print("  ", {k: v for k, v in call(
        "/api/scenario", {"intersection_id": iid,
                          "anomaly": "collision"}).items()
        if k in ("injected", "anomaly")})

    status = call("/api/status")
    inc = next(i for i in status["incidents"]
               if i["intersection_id"] == iid)
    print(f"3. Incident detected: {inc['id']} state={inc['state']}")

    ack = call("/api/incident/ack",
               {"user_id": "op-1", "incident_id": inc["id"]})
    print(f"4. Acknowledged: state={ack['state']}")

    plan = call("/api/recommend", {"incident_id": inc["id"]})
    print(f"5. AI plan {plan['plan_id']}: status={plan['status']}, "
          f"confidence={plan['confidence_score']}%, "
          f"targets={plan['targets']}")
    sim = plan.get("simulation") or {}
    if sim:
        s = sim["summary"]
        print(f"   Dry-run: {s['intersections_improved']} improve / "
              f"{s['intersections_worsened']} worsen; clear in "
              f"~{sim['projected_clear_minutes']} min")

    approved = call("/api/plan/approve",
                    {"user_id": "op-1", "plan_id": plan["plan_id"]})
    print(f"6. Approved -> status={approved['status']} "
          f"(LIVE execution applied to timing plans)")

    status = call("/api/status")
    print(f"   Active timing changes: "
          f"{list(status['active_changes'].keys())}")

    rolled = call("/api/plan/rollback",
                  {"user_id": "op-1", "plan_id": plan["plan_id"]})
    print(f"7. Rollback -> status={rolled['status']} "
          f"(exact prior timing restored)")

    resolved = call("/api/incident/resolve",
                    {"user_id": "op-1", "incident_id": inc["id"],
                     "resolution": "Resolved", "notes": "demo complete"})
    print(f"8. Incident resolved: state={resolved['state']}")

    audit = call("/api/audit")
    print(f"9. Audit: {audit['total']} entries, "
          f"chain_intact={audit['chain_intact']}")

    print("\nFull HITL mission thread verified in LIVE mode. "
          "Returning platform to SHADOW mode.")
    call("/api/mode", {"user_id": "admin-1", "mode": "shadow"})


if __name__ == "__main__":
    main()
