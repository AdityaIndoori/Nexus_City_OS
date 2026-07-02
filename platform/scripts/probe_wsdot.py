"""Probe the WSDOT TravelTimes / HighwayAlerts feeds with the configured
access code (reads WSDOT_ACCESS_CODE from the environment).
Run: python platform/scripts/probe_wsdot.py [ACCESS_CODE]"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if len(sys.argv) > 1:
    os.environ["WSDOT_ACCESS_CODE"] = sys.argv[1]

from nexus.livedata import SeattleLiveData  # noqa: E402

ld = SeattleLiveData()
tt = ld.travel_times()
ha = ld.highway_alerts()
fl = ld.flow_speeds()
print("travel_times:", len(tt),
      "| slowest:", tt[0]["name"] if tt else "-",
      f"({tt[0]['current_minutes']:.0f} min, ratio {tt[0]['ratio']})"
      if tt else "")
print("highway_alerts:", len(ha),
      "| top:", f"{ha[0]['category']} on {ha[0]['road']}" if ha else "-")
print("flow_stations:", len(fl))
h = ld.health()
print("health traveltimes:", h["wsdot_traveltimes"])
print("health hwalerts:", h["wsdot_highway_alerts"])