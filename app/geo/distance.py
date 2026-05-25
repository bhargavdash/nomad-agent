"""Great-circle distance + rough drive-time estimation (pure Python, no I/O)."""

from __future__ import annotations

import math

# Roads are not straight lines: scale crow-flies distance up to approximate a
# realistic driving distance. ~1.3 is a common empirical factor.
_ROAD_FACTOR = 1.3
# Mixed Indian-highway average including stops; deliberately conservative so the
# hint reads as a floor, not an optimistic best case.
_AVG_KMH = 50.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    r = 6371.0  # Earth radius km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def road_km(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Approximate driving distance (km), rounded — haversine × road factor."""
    return int(round(haversine_km(lat1, lng1, lat2, lng2) * _ROAD_FACTOR))


def drive_time_hint(road_distance_km: float) -> str:
    """Rough by-road duration string, e.g. '~5h', '~45m', '~6h30m'.

    Deliberately approximate (the prompt frames it as an estimate). Long legs
    (> ~9h) read as 'overnight' territory; the synthesizer decides train vs car.
    """
    hours = road_distance_km / _AVG_KMH
    total_min = int(round(hours * 60))
    h, m = divmod(total_min, 60)
    if h == 0:
        return f"~{m}m"
    if m == 0:
        return f"~{h}h"
    return f"~{h}h{m}m"
