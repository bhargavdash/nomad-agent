"""Geographic reasoning layer (Milestone D).

Turns a destination into a *geo brief* — a verified, non-backtracking city
circuit with real inter-city distances + rough drive-times, plus sunrise/sunset
times per city — that the synthesizer narrates against. Pure-Python math
(haversine, the sunrise equation) + free OSM Nominatim geocoding; no premium
APIs. Everything degrades gracefully: any failure yields an empty/partial brief
and the synthesizer falls back to its existing behaviour.
"""

from app.geo.planner import GeoBrief, GeoLeg, build_geo_brief

__all__ = ["GeoBrief", "GeoLeg", "build_geo_brief"]
