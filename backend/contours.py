"""Contour maps (KML / KMZ) -> elevation grid.

Nothing here is specific to the sample map. Elevation of each contour is taken,
in order of reliability, from:
  1. an ExtendedData field named like ELEV / ELEVATION / CONTOUR / HEIGHT / Z / ALT,
  2. the third (altitude) value of the coordinates, when present and non-zero,
  3. the first number in the Placemark <name> (e.g. "277.0", "277 m", "Contour 277"),
  4. "elevation: 277" style text in <description>.
LineString, LinearRing and Polygon boundaries are accepted, inside any depth of
Document / Folder / MultiGeometry nesting.

The grid uses square cells (same metric size in x and y) sized to the map extent,
contour lines are densified so long straight segments do not leave gaps, and the
surface is a Delaunay (TIN) interpolation followed by a 1-cell Gaussian smoothing.
Cells outside the convex hull of the contours are "no data".
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from xml.etree.ElementTree import ParseError

import numpy as np
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from geo import M_PER_DEG
from hydrology import Grid, smooth_nan

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_POINTS = 400_000
TARGET_CELLS = 70_000

_ELEV_FIELDS = re.compile(r"^(elev|elevation|contour|height|z|alt|altitude|level|elev_m|contour_m)$", re.I)
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_DESC = re.compile(r"(?:elev(?:ation)?|contour|height|level)\s*[:=]?\s*([-+]?\d+(?:\.\d+)?)", re.I)


class ContourError(ValueError):
    pass


@dataclass
class ContourLine:
    elev: float
    coords: np.ndarray  # (N, 2) lng, lat


def read_kml_bytes(data: bytes, filename: str = "") -> bytes:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ContourError("Contour file is larger than 25 MB.")
    if filename.lower().endswith(".kmz") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if not names:
                    raise ContourError("The KMZ archive does not contain a .kml file.")
                names.sort(key=lambda n: (n.count("/"), n != "doc.kml"))
                info = zf.getinfo(names[0])
                if info.file_size > 4 * MAX_UPLOAD_BYTES:
                    raise ContourError("KML inside the KMZ is too large.")
                return zf.read(names[0])
        except zipfile.BadZipFile as exc:
            raise ContourError("The KMZ file is corrupted.") from exc
    return data


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _text(el, name):
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _placemark_elevation(pm) -> float | None:
    for node in pm.iter():
        tag = _local(node.tag)
        if tag in ("SimpleData", "Data"):
            key = node.get("name", "")
            if _ELEV_FIELDS.match(key.strip()):
                val = (node.text or "").strip() if tag == "SimpleData" else _text(node, "value")
                m = _NUM.search(val or "")
                if m:
                    return float(m.group())
    return None


def parse_contours(kml: bytes) -> list[ContourLine]:
    try:
        root = safe_fromstring(kml)  # defusedxml: no external entities, no entity expansion bombs
    except ParseError as exc:
        raise ContourError(f"Not a valid KML document ({exc}).") from exc
    except DefusedXmlException as exc:
        raise ContourError("KML uses XML entities or DTDs, which are not allowed.") from exc

    lines: list[ContourLine] = []
    total = 0
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        field_elev = _placemark_elevation(pm)
        name_elev = None
        m = _NUM.search(_text(pm, "name"))
        if m:
            name_elev = float(m.group())
        desc_elev = None
        m = _DESC.search(_text(pm, "description"))
        if m:
            desc_elev = float(m.group(1))

        for node in pm.iter():
            if _local(node.tag) != "coordinates" or not node.text:
                continue
            pts, zs = [], []
            for tok in node.text.split():
                parts = tok.split(",")
                if len(parts) < 2:
                    continue
                try:
                    lng, lat = float(parts[0]), float(parts[1])
                except ValueError:
                    continue
                if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                    continue
                pts.append((lng, lat))
                if len(parts) >= 3:
                    try:
                        zs.append(float(parts[2]))
                    except ValueError:
                        pass
            if len(pts) < 2:
                continue
            z_elev = None
            if zs and any(abs(v) > 1e-9 for v in zs) and (max(zs) - min(zs)) < 1e-6:
                z_elev = zs[0]
            elev = next((e for e in (field_elev, z_elev, name_elev, desc_elev) if e is not None), None)
            if elev is None or not math.isfinite(elev):
                continue
            lines.append(ContourLine(elev=float(elev), coords=np.asarray(pts, dtype=np.float64)))
            total += len(pts)
            if total > MAX_POINTS:
                raise ContourError(f"Contour map has more than {MAX_POINTS:,} vertices.")

    if not lines:
        raise ContourError("No contour lines with an elevation were found (checked name, ExtendedData, "
                           "altitude and description).")
    if len({ln.elev for ln in lines}) < 2:
        raise ContourError("All contours have the same elevation; terrain cannot be reconstructed.")
    return lines


def contour_interval(lines: list[ContourLine]) -> float:
    levels = np.unique(np.round([ln.elev for ln in lines], 3))
    if levels.size < 2:
        return 0.0
    diffs = np.diff(levels)
    diffs = diffs[diffs > 1e-6]
    return float(np.round(np.median(diffs), 3)) if diffs.size else 0.0


def _densify(coords: np.ndarray, step_deg_x: float, step_deg_y: float) -> np.ndarray:
    seg = np.diff(coords, axis=0)
    n = np.maximum(1, np.ceil(np.maximum(np.abs(seg[:, 0]) / step_deg_x, np.abs(seg[:, 1]) / step_deg_y))).astype(int)
    if n.max() == 1:
        return coords
    out = [coords[:1]]
    for i, k in enumerate(n):
        t = np.arange(1, k + 1)[:, None] / k
        out.append(coords[i] + seg[i] * t)
    return np.vstack(out)


def contours_to_grid(lines: list[ContourLine]) -> Grid:
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import QhullError

    allpts = np.vstack([ln.coords for ln in lines])
    min_lng, min_lat = allpts.min(axis=0)
    max_lng, max_lat = allpts.max(axis=0)
    mid_lat = (min_lat + max_lat) / 2
    kx = M_PER_DEG * math.cos(math.radians(mid_lat))
    w_m = max((max_lng - min_lng) * kx, 1.0)
    h_m = max((max_lat - min_lat) * M_PER_DEG, 1.0)
    cell = min(30.0, max(2.0, math.sqrt(w_m * h_m / TARGET_CELLS)))
    dlat = cell / M_PER_DEG
    dlng = cell / kx

    xs, ys, zs = [], [], []
    for ln in lines:
        d = _densify(ln.coords, dlng / 2, dlat / 2)
        xs.append(d[:, 0])
        ys.append(d[:, 1])
        zs.append(np.full(len(d), ln.elev))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    z = np.concatenate(zs)
    # de-duplicate at a quarter-cell so the triangulation stays well conditioned
    key = np.round(x / (dlng / 4)).astype(np.int64) * 1_000_003 + np.round(y / (dlat / 4)).astype(np.int64)
    _, keep = np.unique(key, return_index=True)
    x, y, z = x[keep], y[keep], z[keep]
    if x.size > MAX_POINTS:
        pick = np.linspace(0, x.size - 1, MAX_POINTS).astype(int)
        x, y, z = x[pick], y[pick], z[pick]

    ncols = max(3, int(round(w_m / cell)) + 1)
    nrows = max(3, int(round(h_m / cell)) + 1)
    lngs = min_lng + np.arange(ncols) * dlng
    lats = max_lat - np.arange(nrows) * dlat
    gx, gy = np.meshgrid(lngs, lats)
    try:
        # interpolate in metric space so the TIN is not distorted by cos(latitude)
        interp = LinearNDInterpolator(np.column_stack(((x - min_lng) * kx, (y - min_lat) * M_PER_DEG)), z)
    except QhullError as exc:
        raise ContourError("Contour points are collinear; cannot build a surface.") from exc
    grid_z = interp((gx - min_lng) * kx, (gy - min_lat) * M_PER_DEG)
    grid_z = smooth_nan(grid_z, 1.0)
    if np.isfinite(grid_z).sum() < 16:
        raise ContourError("Contour map covers too small an area to analyse.")
    return Grid(z=grid_z, lats=lats, lngs=lngs, source={
        "type": "contour_map",
        "name": "Uploaded contour map",
        "resolution": f"{cell:.1f} m grid from {len(lines)} contour lines",
        "cell_m": round(cell, 2),
    })


def contour_layer(lines: list[ContourLine], max_vertices: int = 60_000) -> list[dict]:
    """Original contour lines as GeoJSON (thinned only if the file is very dense)."""
    total = sum(len(ln.coords) for ln in lines)
    step = max(1, math.ceil(total / max_vertices))
    interval = contour_interval(lines) or 1.0
    by_level: dict[float, list] = {}
    for ln in lines:
        c = ln.coords[::step] if step > 1 else ln.coords
        if step > 1 and not np.array_equal(c[-1], ln.coords[-1]):
            c = np.vstack([c, ln.coords[-1:]])
        by_level.setdefault(ln.elev, []).append([[round(float(a), 7), round(float(b), 7)] for a, b in c])
    feats = []
    for elev in sorted(by_level):
        feats.append({
            "type": "Feature",
            "properties": {"elev": elev, "index": abs((elev / interval) % 5) < 1e-6},
            "geometry": {"type": "MultiLineString", "coordinates": by_level[elev]},
        })
    return feats
