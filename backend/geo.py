"""Geometry helpers: WGS84 lat/lng <-> local metres, polygon parsing and rasterisation.

All areas are computed on a local equirectangular projection centred on the
geometry. For village-scale shapes (< 50 km^2) the error versus a geodesic
area is well under 0.1 %, far below the uncertainty of the elevation data.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

EARTH_RADIUS_M = 6371008.8
M_PER_DEG = math.pi * EARTH_RADIUS_M / 180.0  # ~111,195 m per degree of latitude

MAX_POLYGON_VERTICES = 2000


class GeometryError(ValueError):
    """Raised when a user-supplied geometry cannot be used."""


def parse_polygon(geom: dict) -> list[tuple[float, float]]:
    """Return the outer ring of a GeoJSON Polygon/Feature as [(lng, lat), ...] without the closing vertex."""
    if not isinstance(geom, dict):
        raise GeometryError("Area must be a GeoJSON object.")
    if geom.get("type") == "Feature":
        geom = geom.get("geometry") or {}
    if geom.get("type") == "MultiPolygon":
        polys = geom.get("coordinates") or []
        if len(polys) != 1:
            raise GeometryError("Select a single land parcel (one polygon).")
        coords = polys[0]
    elif geom.get("type") == "Polygon":
        coords = geom.get("coordinates") or []
    else:
        raise GeometryError("Area must be a GeoJSON Polygon.")
    if not coords or not isinstance(coords[0], list):
        raise GeometryError("Polygon has no outer ring.")

    ring: list[tuple[float, float]] = []
    for pt in coords[0]:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise GeometryError("Every vertex must be [longitude, latitude].")
        lng, lat = float(pt[0]), float(pt[1])
        if not (math.isfinite(lng) and math.isfinite(lat)):
            raise GeometryError("Polygon contains non-numeric coordinates.")
        if not (-180.0 <= lng <= 180.0 and -85.0 <= lat <= 85.0):
            raise GeometryError("Coordinates must be [longitude, latitude] in degrees.")
        if not ring or (lng, lat) != ring[-1]:
            ring.append((lng, lat))
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        raise GeometryError("A land area needs at least 3 distinct corners.")
    if len(ring) > MAX_POLYGON_VERTICES:
        raise GeometryError(f"Polygon has too many vertices (max {MAX_POLYGON_VERTICES}).")
    return ring


def ring_bbox(ring: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    """(min_lng, min_lat, max_lng, max_lat)."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _local_xy(ring: Sequence[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    lat0 = sum(p[1] for p in ring) / len(ring)
    lng0 = sum(p[0] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    x = np.array([(p[0] - lng0) * M_PER_DEG * k for p in ring])
    y = np.array([(p[1] - lat0) * M_PER_DEG for p in ring])
    return x, y


def ring_area_m2(ring: Sequence[tuple[float, float]]) -> float:
    x, y = _local_xy(ring)
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def ring_perimeter_m(ring: Sequence[tuple[float, float]]) -> float:
    x, y = _local_xy(ring)
    return float(np.hypot(np.diff(np.append(x, x[0])), np.diff(np.append(y, y[0]))).sum())


def ring_is_simple(ring: Sequence[tuple[float, float]]) -> bool:
    """True when no two non-adjacent edges cross (a bow-tie polygon has no meaningful area)."""
    n = len(ring)
    if n > 200:  # O(n^2); large rings come from files, not hand drawing
        return True
    x, y = _local_xy(ring)
    pts = list(zip(x.tolist(), y.tolist()))

    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)

    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                return False
    return True


def rasterize_ring(ring: Sequence[tuple[float, float]], lats: np.ndarray, lngs: np.ndarray) -> np.ndarray:
    """Boolean mask of grid cells whose centre lies inside the ring (even-odd rule, scanline fill).

    lats: (R,) cell-centre latitudes, lngs: (C,) cell-centre longitudes.
    """
    xs = np.array([p[0] for p in ring])
    ys = np.array([p[1] for p in ring])
    x2 = np.roll(xs, -1)
    y2 = np.roll(ys, -1)
    mask = np.zeros((lats.size, lngs.size), dtype=bool)
    for r, lat in enumerate(lats):
        crosses = (ys > lat) != (y2 > lat)
        if not crosses.any():
            continue
        xi = xs[crosses] + (lat - ys[crosses]) * (x2[crosses] - xs[crosses]) / (y2[crosses] - ys[crosses])
        xi.sort()
        mask[r] = (np.searchsorted(xi, lngs, side="right") % 2) == 1
    return mask


def circle_ring(lat: float, lng: float, radius_m: float, n: int = 40) -> list[list[float]]:
    k = math.cos(math.radians(lat))
    out = []
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        out.append([round(lng + radius_m * math.cos(t) / (M_PER_DEG * k), 7),
                    round(lat + radius_m * math.sin(t) / M_PER_DEG, 7)])
    return out


def bbox_geojson(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[min_lng, min_lat], [max_lng, min_lat], [max_lng, max_lat],
                         [min_lng, max_lat], [min_lng, min_lat]]],
    }


def round_coords(points: Iterable[Sequence[float]], nd: int = 6) -> list[list[float]]:
    return [[round(float(p[0]), nd), round(float(p[1]), nd)] for p in points]
