"""Sunrise / sunset times via the standard sunrise equation (pure math, no API).

Returns *local* clock times using a UTC offset. India (the primary market) has
a single, DST-free timezone so its times are exact; other regions use a coarse
offset and are approximate (fine for "sunrise ~7:14" itinerary hooks).
"""

from __future__ import annotations

import math
from datetime import date

_J2000 = date(2000, 1, 1).toordinal()
_OBLIQUITY = math.radians(23.4397)  # Earth's axial tilt
_SUN_ANGLE = math.radians(-0.833)  # standard sunrise/sunset altitude (refraction + disc)


def _hhmm(local_hours: float) -> str:
    local_hours %= 24
    h = int(local_hours)
    m = int(round((local_hours - h) * 60))
    if m == 60:
        h = (h + 1) % 24
        m = 0
    return f"{h:d}:{m:02d}"


def sun_times(d: date, lat: float, lng: float, tz_offset_hours: float) -> tuple[str, str] | None:
    """Return (sunrise, sunset) as 'H:MM' local strings, or None at polar day/night.

    Implements the Wikipedia "Sunrise equation". `tz_offset_hours` is the
    destination's UTC offset (e.g. 5.5 for India).
    """
    n = math.ceil((2451544.5 + (d.toordinal() - _J2000)) - 2451545.0 + 0.0008)
    j_star = n - lng / 360.0
    m_anom = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    center = (
        1.9148 * math.sin(m_anom) + 0.0200 * math.sin(2 * m_anom) + 0.0003 * math.sin(3 * m_anom)
    )
    ecl_lon = math.radians((math.degrees(m_anom) + center + 282.9372) % 360)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m_anom) - 0.0069 * math.sin(2 * ecl_lon)
    decl = math.asin(math.sin(ecl_lon) * math.sin(_OBLIQUITY))
    lat_r = math.radians(lat)
    cos_omega = (math.sin(_SUN_ANGLE) - math.sin(lat_r) * math.sin(decl)) / (
        math.cos(lat_r) * math.cos(decl)
    )
    if cos_omega < -1 or cos_omega > 1:
        return None  # polar day / polar night
    omega = math.degrees(math.acos(cos_omega)) / 360.0
    j_rise = j_transit - omega
    j_set = j_transit + omega

    def _local(j: float) -> float:
        frac = (j - 2451544.5) % 1.0  # fraction of the UTC day
        return frac * 24 + tz_offset_hours

    return _hhmm(_local(j_rise)), _hhmm(_local(j_set))
