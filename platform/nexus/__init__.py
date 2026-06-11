"""
Nexus City OS — reference implementation.

A decision-intelligence platform for real-time smart-city traffic management
and incident mitigation. Seattle-first, extensible to any city via the
City Adapter SDK (``nexus.adapters.CityAdapter``).

See PRD_v2.md (v2.1) for requirements and MASTER_PROMPT.md for the
engineering blueprint this package implements.
"""
from __future__ import annotations

from typing import Tuple

from .adapters import CityAdapter, SeattleAdapter
from .edge import EdgeSimulator
from .engine import NexusEngine
from .models import OperatingMode
from .store import Store

__version__ = "1.1.0"


def bootstrap(adapter: CityAdapter | None = None,
              store: "Store | None" = None,
              use_llm: bool = False,
              ) -> Tuple[NexusEngine, EdgeSimulator, CityAdapter]:
    """Stand up a full platform instance for a city.

    Loads topology through the adapter, wires the edge simulator to the
    telemetry bus, and primes the data feeds. The platform starts in
    Shadow Mode (PRD §7.1); with a ``store``, the last Admin-authorized
    mode and the durable audit chain are restored.
    """
    adapter = adapter or SeattleAdapter()
    engine = NexusEngine(city_id=adapter.city_id, store=store,
                         use_llm=use_llm)

    topology = adapter.load_topology()
    for inter in topology["intersections"]:
        engine.graph.add_intersection(inter)
    for seg in topology["segments"]:
        engine.graph.add_segment(seg)
    for cam in topology["cameras"]:
        engine.graph.add_camera(cam)

    for vehicle in adapter.poll_transit():
        engine.graph.add_vehicle(vehicle)
    engine.graph.set_weather(adapter.poll_weather())

    engine.touch_feed("transit_gps")
    engine.touch_feed("weather")
    engine.touch_feed("closures")
    engine.touch_feed("camera")

    edge = EdgeSimulator(engine.graph, engine.bus, adapter.city_id)
    engine.audit.record(
        actor="system", action="city_bootstrapped",
        detail=f"{adapter.display_name}: "
               f"{len(topology['intersections'])} intersections, "
               f"{len(topology['segments'])} segments, "
               f"{len(topology['cameras'])} cameras")
    return engine, edge, adapter