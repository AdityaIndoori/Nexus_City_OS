"""E2E smoke test for the new endpoints (notes/contact/report/handover/
analytics SLA/incidents active param). Run against the sim server on 8899."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8899"


def req(path, body=None, token=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(r, timeout=20) as resp:
        payload = resp.read()
        return payload if raw else json.loads(payload)


def main():
    # wait for server
    for _ in range(30):
        try:
            req("/healthz")
            break
        except Exception:
            time.sleep(1)
    else:
        print("FAIL: server never came up")
        return 1

    tok = req("/api/login", {"user_id": "op-1",
                             "password": "nexus-op-1"})["token"]
    print("login OK")

    # inject an incident (retry across intersections — detector cooldown)
    grid = req("/api/grid", token=tok)
    inc = None
    for inter in grid["intersections"][:10]:
        req("/api/scenario", {"intersection_id": inter["id"],
                              "anomaly": "collision"}, token=tok)
        time.sleep(1.5)
        rows = req("/api/incidents?limit=5", token=tok)["incidents"]
        if rows:
            inc = rows[0]
            break
    if inc is None:
        print("FAIL: no incident raised after injection attempts")
        return 1
    incid = inc["id"]
    assert "operator_notes" in inc, "operator_notes missing from dict"
    print(f"incident injected: {incid} (operator_notes field present)")

    # notes save
    r = req("/api/incident/notes",
            {"incident_id": incid, "notes": "handover: crew en route"},
            token=tok)
    assert r["notes_len"] == len("handover: crew en route")
    inc2 = req("/api/incidents?limit=5", token=tok)["incidents"][0]
    assert inc2["operator_notes"] == "handover: crew en route"
    print("notes save + roundtrip OK")

    # field contact
    r = req("/api/incident/contact",
            {"incident_id": incid, "service": "fire",
             "note": "requested SFD"}, token=tok)
    assert r["service"] == "fire"
    # invalid service rejected
    try:
        req("/api/incident/contact",
            {"incident_id": incid, "service": "pizza"}, token=tok)
        print("FAIL: invalid service accepted")
        return 1
    except urllib.error.HTTPError as e:
        assert e.code == 400
    # audit contains field_contact
    audit = req("/api/audit", token=tok)
    acts = [e["action"] for e in audit["entries"]]
    assert "field_contact" in acts and "incident_notes_updated" in acts
    assert audit["chain_intact"]
    print("field contact + audit chain OK")

    # incident report export
    rep = json.loads(req("/api/incident/report?id=" + incid,
                         token=tok, raw=True))
    assert rep["incident"]["id"] == incid
    assert rep["audit_chain_intact"]
    print(f"incident report OK ({len(rep['audit_entries'])} audit entries)")

    # resolve, then active=1 vs closed
    req("/api/incident/resolve",
        {"incident_id": incid, "resolution": "Resolved",
         "notes": "test"}, token=tok)
    act = req("/api/incidents?active=1&limit=50", token=tok)
    assert all(i["state"] not in ("resolved", "closed")
               for i in act["incidents"])
    allq = req("/api/incidents?limit=50", token=tok)
    assert any(i["id"] == incid and i["state"] == "resolved"
               for i in allq["incidents"])
    print("closed-incidents filter (active=1 vs all) OK")

    # handover
    h = req("/api/handover?hours=8", token=tok)
    assert h["audit_chain_intact"]
    assert any(i["id"] == incid for i in h["resolved_in_window"])
    print(f"handover OK (resolved_in_window={len(h['resolved_in_window'])})")

    # analytics SLA metrics
    a = req("/api/analytics?hours=24", token=tok)
    rm = a.get("response_metrics")
    assert rm is not None and rm["resolve_count"] >= 1
    assert rm["resolve_median_s"] is not None
    print(f"analytics SLA OK (resolve_median_s={rm['resolve_median_s']})")

    print("ALL NEW-ENDPOINT E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())