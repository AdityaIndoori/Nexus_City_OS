"""Probe Seattle open-data emergency feeds (Citizen-app style sources)."""
import json
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "NexusCityOS/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# 1. Seattle Fire Dept Real-Time 911 (Socrata, no key)
try:
    fire = get("https://data.seattle.gov/resource/kzjm-xkqj.json"
               "?$order=datetime%20DESC&$limit=5")
    print("FIRE 911 OK —", len(fire), "rows")
    for f in fire[:3]:
        print("  ", f.get("datetime"), "|", f.get("type"), "|",
              f.get("address"), "|", f.get("latitude"), f.get("longitude"))
except Exception as e:
    print("FIRE 911 FAIL:", e)

# 2. SPD 911 Call Data (Socrata)
try:
    pd = get("https://data.seattle.gov/resource/33kz-ixgy.json"
             "?$order=cad_event_original_time_queued%20DESC&$limit=3")
    print("SPD 911 OK —", len(pd), "rows")
    for p in pd[:2]:
        print("  keys:", sorted(p.keys())[:10])
except Exception as e:
    print("SPD 911 FAIL:", e)

# 3. NWS active alerts for Seattle zone
try:
    alerts = get("https://api.weather.gov/alerts/active?point=47.61,-122.33")
    feats = alerts.get("features", [])
    print("NWS ALERTS OK —", len(feats), "active")
    for a in feats[:2]:
        print("  ", a["properties"].get("event"), "|",
              a["properties"].get("severity"))
except Exception as e:
    print("NWS ALERTS FAIL:", e)