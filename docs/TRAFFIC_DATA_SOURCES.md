# Traffic Data Sources — Research (Free & Paid)

Context: today Nexus City OS estimates congestion from **KC Metro bus GPS probes**
(OneBusAway) plus optional **WSDOT loop-detector flow** (`WSDOT_ACCESS_CODE`).
Buses are sparse, stop at bus stops (bias), and cover only transit corridors.
This doc catalogs every practical alternative, with cost, coverage, latency,
and a recommendation for what to integrate next.

---

## Tier 1 — Free / public (best first upgrades)

| Source | What you get | Coverage | Latency | Access |
|---|---|---|---|---|
| **WSDOT Traveler Information API** (already partially integrated) | Loop-detector flow (`TrafficFlow`), **travel times** (`TravelTimes`), **highway alerts/incidents** (`HighwayAlerts`), border/mtn pass | WA highways (I-5, I-90, SR-99, SR-520…) | ~20–90 s | Free access code, instant signup at wsdot.wa.gov/traffic/api |
| **Seattle Open Data (Socrata)** | SDOT permanent bike/ped/vehicle **counters**, collisions dataset, closures/permits | Seattle arterials (sparse) | Counters: hourly; collisions: days | Free, no key (throttled) or free app token |
| **PeMS-style state DOT feeds** (per-state) | Loop/radar detector speed+volume+occupancy | Varies per state | 30–60 s | Free registration (relevant for multi-city SDK) |
| **OpenStreetMap + OSRM/Valhalla** (already used for geometry) | Road network, free-flow speed baselines | Global | Static | Free |
| **GTFS-RT feeds beyond KC Metro** (Pierce, Community, Sound Transit) | More bus probes = denser probe coverage | Regional | 10–30 s | Free (OneBusAway TEST key or agency keys) |
| **Waze for Cities (Waze Data Feed / CCP)** ⭐ | **Crowdsourced jams (polyline + speed + delay), crashes, hazards** from Waze drivers | Everywhere Waze has users (dense in Seattle) | ~2 min | **Free**, but requires a data-sharing partnership application — designed for exactly this use case (public agencies / TOCs). The single highest-value free upgrade. |

## Tier 2 — Freemium APIs (generous free tiers, production-grade)

| Source | What you get | Free tier | Paid pricing (order of magnitude) |
|---|---|---|---|
| **TomTom Traffic API** ⭐ | **Flow Segment Data** (current speed, free-flow speed, confidence per road segment — exactly our congestion index), raster/vector flow tiles, **incident details** | **2,500 req/day free** | ~$0.4–0.5 / 1K req after |
| **HERE Traffic API v7** | Real-time flow (jam factor 0–10 per segment!), incidents | 250K transactions/month free | ~$1 / 1K after |
| **Mapbox Traffic** | Traffic tiles + typical/live speeds via Directions | 100K req/month free tiers | usage-based |
| **Google Maps Routes/Distance Matrix ("traffic_model")** | Live travel times between points (probe-derived, best-in-class accuracy) | $200/mo credit | expensive at scale (~$5–10 / 1K elements) — good for spot checks, not continuous polling |

## Tier 3 — Commercial / enterprise probe data (what DOTs actually buy)

| Source | What you get | Notes |
|---|---|---|
| **INRIX** | Segment speeds (XD segments), incidents, signal analytics, OD matrices | Industry standard for US DOTs; WSDOT itself buys INRIX. $50K–500K+/yr enterprise contracts. |
| **HERE Premium / TomTom enterprise licenses** | Full historical + real-time probe archive | Similar enterprise pricing |
| **StreetLight Data** | OD / volume analytics from location data | Analytics (not real-time), planning use |
| **Iteris ClearGuide / RITIS (CATT Lab)** | Fused probe dashboards for agencies | RITIS is *free to public agencies* via the Eastern Transportation Coalition — worth noting in sales conversations: the customer may already have it |
| **Miovision / connected-signal vendors** | Signal-level detection + ATSPM | Hardware-attached; competitor category |

## Tier 4 — Derived / self-built

- **Camera CV counting**: we already fetch SDOT/WSDOT JPEG frames — running the
  existing Claude vision sweep (or a cheap YOLO model) as a *vehicle counter*
  per camera converts 650 free cameras into 650 traffic sensors. Zero data
  cost; compute cost only. Strong product differentiator ("uses the city's
  existing cameras").
- **Connected-vehicle data** (Wejo-successors, Otonomo/Urgently, GM/Toyota data
  programs): raw CV probe feeds; expensive, procurement-heavy.

---

## Recommendation (in order)

1. **Enable the full WSDOT API surface** (free, we already have the key path):
   add `TravelTimes` + `HighwayAlerts` to `livedata.py` — real incidents and
   corridor travel-times at zero cost.
2. **Apply to Waze for Cities** (free): jams polylines + crash reports are the
   single biggest accuracy jump for arterials where buses don't run.
3. **Add a TomTom Flow Segment adapter** (2.5K req/day free = one refresh of
   ~100 key segments every ~hour, or targeted on-incident refreshes): gives a
   per-segment `currentSpeed / freeFlowSpeed` ratio to blend with bus probes.
   Architecture: new `_Cached` client in `livedata.py` + a high-weight sample
   source in `congestion.py` (same pattern as WSDOT flow).
4. **Camera-CV counting** as a differentiating roadmap item (frames are free).
5. For paying municipal customers, resell/bundle **INRIX or HERE enterprise**
   as a premium data tier — this matches how incumbent TOC software is priced.