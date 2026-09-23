"""Terrain hydrology on a regular lat/lng elevation grid.

Pipeline (each step is a pure function so it can be unit tested on synthetic terrain):

1. ``priority_flood_fill``  - Priority-Flood+epsilon depression filling (Barnes et al., 2014).
   Every pit and flat is raised just enough that each cell has a strictly downhill
   path to the edge of the data, so no water gets "stuck" in DEM noise.
2. ``d8_receivers``         - D8 steepest-descent flow direction (O'Callaghan & Mark, 1984),
   vectorised over the 8 neighbours with true metric distances.
3. ``flow_accumulation``    - upstream contributing area of every cell, by visiting cells
   from highest to lowest (a topological order of the flow DAG).
4. ``upstream_mask``        - the catchment of a chosen outlet: all cells whose flow path
   passes through it.

Grid convention: row 0 is the northern-most row; ``lats`` descend, ``lngs`` ascend.
Cells with NaN elevation are "no data" and behave like the outside of the map.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field

import contourpy
import numpy as np

from geo import M_PER_DEG

# Neighbour order used everywhere: NW, N, NE, W, E, SW, S, SE
_DR = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
_DC = np.array([-1, 0, 1, -1, 1, -1, 0, 1])


@dataclass
class Grid:
    """Elevation raster on a regular lat/lng lattice (cell centres)."""

    z: np.ndarray            # (R, C) float64 metres, NaN = no data
    lats: np.ndarray         # (R,) descending
    lngs: np.ndarray         # (C,) ascending
    source: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape

    @property
    def dlat(self) -> float:
        return float(abs(self.lats[0] - self.lats[1])) if self.lats.size > 1 else 1e-4

    @property
    def dlng(self) -> float:
        return float(abs(self.lngs[1] - self.lngs[0])) if self.lngs.size > 1 else 1e-4

    @property
    def dy_m(self) -> float:
        return self.dlat * M_PER_DEG

    @property
    def dx_m(self) -> np.ndarray:
        """Column spacing in metres for every row (shrinks with cos(latitude))."""
        return self.dlng * M_PER_DEG * np.cos(np.radians(self.lats))

    @property
    def cell_area(self) -> np.ndarray:
        """(R, 1) cell area in m^2 per row."""
        return (self.dx_m * self.dy_m)[:, None]


def boundary_cells(valid: np.ndarray) -> np.ndarray:
    """Valid cells that touch no-data or the grid edge (8-connectivity)."""
    pad = np.zeros((valid.shape[0] + 2, valid.shape[1] + 2), dtype=bool)
    pad[1:-1, 1:-1] = valid
    touching = np.zeros_like(valid)
    for dr, dc in zip(_DR, _DC):
        touching |= ~pad[1 + dr:pad.shape[0] - 1 + dr, 1 + dc:pad.shape[1] - 1 + dc]
    return valid & touching


def priority_flood_fill(z: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Priority-Flood+epsilon: fill depressions and give flats a tiny drainage gradient.

    Seeds are the boundary cells; the lowest open cell is always expanded next, so
    water "floods in" from the edges. A neighbour that is not higher than the current
    cell is raised to nextafter(current) - that is the only modification made to the
    terrain, and it is exactly what makes every cell drain.
    """
    rows, cols = z.shape
    width = cols + 2
    zp = np.full((rows + 2, cols + 2), np.inf)
    zp[1:-1, 1:-1] = np.where(valid, z, np.inf)
    vp = np.zeros((rows + 2, cols + 2), dtype=bool)
    vp[1:-1, 1:-1] = valid

    elev = zp.ravel().tolist()
    closed = bytearray((~vp).ravel().astype(np.uint8).tobytes())
    seeds = np.flatnonzero(np.pad(boundary_cells(valid), 1).ravel()).tolist()

    open_heap = [(elev[i], i) for i in seeds]
    heapq.heapify(open_heap)
    for i in seeds:
        closed[i] = 1
    pit: deque[int] = deque()

    offsets = (-width - 1, -width, -width + 1, -1, 1, width - 1, width, width + 1)
    nextafter, inf = math.nextafter, math.inf
    heappop, heappush = heapq.heappop, heapq.heappush

    while open_heap or pit:
        if pit:
            c = pit.popleft()
            zc = elev[c]
        else:
            zc, c = heappop(open_heap)
        raised = nextafter(zc, inf)
        for o in offsets:
            n = c + o
            if closed[n]:
                continue
            closed[n] = 1
            if elev[n] <= raised:
                elev[n] = raised
                pit.append(n)
            else:
                heappush(open_heap, (elev[n], n))

    filled = np.asarray(elev, dtype=np.float64).reshape(rows + 2, cols + 2)[1:-1, 1:-1]
    return np.where(valid, filled, np.nan)


def d8_receivers(filled: np.ndarray, valid: np.ndarray, dx_rows: np.ndarray, dy: float) -> np.ndarray:
    """Flat index of the steepest-descent neighbour of every cell; -1 = drains off the data."""
    rows, cols = filled.shape
    fp = np.full((rows + 2, cols + 2), np.inf)
    fp[1:-1, 1:-1] = np.where(valid, filled, np.inf)
    centre = fp[1:-1, 1:-1]
    best_slope = np.zeros((rows, cols))
    best_dir = np.full((rows, cols), -1, dtype=np.int64)
    dx = dx_rows[:, None]
    for k, (dr, dc) in enumerate(zip(_DR, _DC)):
        nb = fp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        dist = np.hypot(dr * dy, dc * dx)
        with np.errstate(invalid="ignore"):
            slope = (centre - nb) / dist
        better = slope > best_slope
        best_slope = np.where(better, slope, best_slope)
        best_dir = np.where(better, k, best_dir)

    idx = np.arange(rows * cols, dtype=np.int64).reshape(rows, cols)
    safe = np.maximum(best_dir, 0)
    recv = idx + _DR[safe] * cols + _DC[safe]
    recv = np.where((best_dir >= 0) & valid, recv, -1)
    return recv.ravel()


def flow_accumulation(filled: np.ndarray, recv: np.ndarray, valid: np.ndarray,
                      cell_area: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Upstream contributing area (m^2, including the cell itself) and the high-to-low visit order."""
    flat = np.where(valid, filled, -np.inf).ravel()
    order = np.argsort(-flat, kind="stable")
    order = order[: int(valid.sum())]  # drop no-data cells (sorted last)
    acc = np.where(valid, np.broadcast_to(cell_area, filled.shape), 0.0).ravel().tolist()
    rl = recv.tolist()
    for i in order.tolist():
        r = rl[i]
        if r >= 0:
            acc[r] += acc[i]
    return np.asarray(acc), order


def upstream_mask(recv: np.ndarray, order: np.ndarray, outlet: int, shape: tuple[int, int]) -> np.ndarray:
    """Cells draining through ``outlet``. Visits cells low-to-high so receivers are decided first."""
    inside = bytearray(shape[0] * shape[1])
    inside[outlet] = 1
    rl = recv.tolist()
    for i in order[::-1].tolist():
        r = rl[i]
        if r >= 0 and inside[r]:
            inside[i] = 1
    return np.frombuffer(bytes(inside), dtype=np.uint8).reshape(shape).astype(bool)


def slope_percent(z: np.ndarray, dx_rows: np.ndarray, dy: float) -> np.ndarray:
    gz_y, gz_x = np.gradient(z)
    return np.hypot(gz_x / dx_rows[:, None], gz_y / dy) * 100.0


def smooth_nan(z: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing that ignores NaNs (normalised convolution)."""
    from scipy.ndimage import gaussian_filter

    valid = np.isfinite(z)
    num = gaussian_filter(np.where(valid, z, 0.0), sigma)
    den = gaussian_filter(valid.astype(float), sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    return np.where(valid, out, np.nan)


# ---------------------------------------------------------------- vector layers

def _increasing(grid: Grid, a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """contourpy wants increasing y; flip rows so latitude ascends."""
    return grid.lngs, grid.lats[::-1], a[::-1]


def _simplify(line: np.ndarray, tol: float) -> np.ndarray:
    """Drop vertices closer than ``tol`` degrees to the last kept one (cheap, keeps shape at map scale)."""
    if len(line) <= 3:
        return line
    keep = [0]
    last = line[0]
    for i in range(1, len(line) - 1):
        if abs(line[i, 0] - last[0]) + abs(line[i, 1] - last[1]) >= tol:
            keep.append(i)
            last = line[i]
    keep.append(len(line) - 1)
    return line[keep]


def chaikin(coords: np.ndarray, iterations: int = 2, closed: bool = False) -> np.ndarray:
    """Corner-cutting smoothing for display. D8 paths and cell outlines move in 45-degree
    steps; this rounds them without moving endpoints (so stream junctions stay joined)."""
    pts = np.asarray(coords, dtype=np.float64)
    for _ in range(iterations):
        if closed:
            ring = pts[:-1] if np.allclose(pts[0], pts[-1]) else pts
            if len(ring) < 3:
                return pts
            nxt = np.roll(ring, -1, axis=0)
            out = np.empty((len(ring) * 2, 2))
            out[0::2] = 0.75 * ring + 0.25 * nxt
            out[1::2] = 0.25 * ring + 0.75 * nxt
            pts = np.vstack([out, out[:1]])
        else:
            if len(pts) < 3:
                return pts
            a, b = pts[:-1], pts[1:]
            out = np.empty((len(a) * 2, 2))
            out[0::2] = 0.75 * a + 0.25 * b
            out[1::2] = 0.25 * a + 0.75 * b
            pts = np.vstack([pts[:1], out[1:-1], pts[-1:]])
    return pts


def nice_interval(relief: float, target_lines: int = 14) -> float:
    if relief <= 0:
        return 1.0
    raw = relief / target_lines
    for step in (0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200):
        if step >= raw:
            return float(step)
    return 500.0


def contour_features(grid: Grid, z: np.ndarray, interval: float, index_every: int = 5,
                     max_vertices: int = 40000) -> list[dict]:
    """Isolines of ``z`` as GeoJSON features (property ``elev``; ``index`` for every 5th line)."""
    finite = z[np.isfinite(z)]
    if finite.size < 4:
        return []
    x, y, zz = _increasing(grid, z)
    gen = contourpy.contour_generator(x, y, np.ma.masked_invalid(zz),
                                      line_type=contourpy.LineType.Separate)
    lo = math.ceil(float(finite.min()) / interval) * interval
    hi = float(finite.max())
    tol = min(grid.dlat, grid.dlng) * 0.6
    feats, budget = [], max_vertices
    level = lo
    while level <= hi and budget > 0:
        parts = []
        for ln in gen.lines(level):
            if len(ln) < 2:
                continue
            ln = _simplify(np.asarray(ln), tol)
            budget -= len(ln)
            parts.append([[round(float(a), 6), round(float(b), 6)] for a, b in ln])
        if parts:
            idx_line = abs((level / interval) % index_every) < 1e-6
            feats.append({
                "type": "Feature",
                "properties": {"elev": round(level, 2), "index": bool(idx_line)},
                "geometry": {"type": "MultiLineString", "coordinates": parts},
            })
        level = round(level + interval, 6)
    return feats


def mask_to_geojson(grid: Grid, mask: np.ndarray, smooth: int = 0) -> dict | None:
    """Outline of a boolean cell mask as a GeoJSON MultiPolygon (holes preserved).
    ``smooth`` rounds the stair-stepped cell edges for display; areas are always computed from cells."""
    if not mask.any():
        return None
    rows, cols = mask.shape
    pad = np.zeros((rows + 2, cols + 2))
    pad[1:-1, 1:-1] = mask
    lngs = np.concatenate(([grid.lngs[0] - grid.dlng], grid.lngs, [grid.lngs[-1] + grid.dlng]))
    lats = np.concatenate(([grid.lats[0] + grid.dlat], grid.lats, [grid.lats[-1] - grid.dlat]))
    gen = contourpy.contour_generator(lngs, lats[::-1], pad[::-1],
                                      fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(0.5, 1.5)
    polys = []
    for pts, offs in zip(points, offsets):
        rings = []
        for a, b in zip(offs[:-1], offs[1:]):
            part = pts[a:b]
            if len(part) < 3:
                continue
            if smooth:
                part = chaikin(part, smooth, closed=True)
            ring = [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in part]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
        if rings:
            polys.append(rings)
    if not polys:
        return None
    return {"type": "MultiPolygon", "coordinates": polys}


def stream_features(grid: Grid, recv: np.ndarray, acc: np.ndarray, valid: np.ndarray,
                    threshold_m2: float, catchment: np.ndarray, max_cells: int = 6000,
                    outside_factor: float = 5.0) -> list[dict]:
    """Drainage lines: cells whose upstream area exceeds ``threshold_m2`` (``outside_factor`` times
    more outside the catchment, so only the main drains around it are drawn), merged into polylines."""
    flat_valid = valid.ravel()
    in_catch = catchment.ravel()
    need = np.where(in_catch, threshold_m2, threshold_m2 * outside_factor)
    is_stream = (acc >= need) & flat_valid
    if is_stream.sum() > max_cells:  # keep the payload bounded
        cut = float(np.partition(acc[is_stream], -max_cells)[-max_cells])
        is_stream &= acc >= cut
    s_idx = np.flatnonzero(is_stream)
    if s_idx.size < 2:
        return []
    r = recv[s_idx]
    ok = r >= 0
    ok[ok] &= is_stream[r[ok]]
    donors = np.zeros(acc.size, dtype=np.int32)
    np.add.at(donors, r[ok], 1)

    cols = grid.shape[1]
    lats, lngs = grid.lats, grid.lngs
    rl = recv.tolist()
    is_s = is_stream.tolist()
    dn = donors.tolist()
    in_c = catchment.ravel().tolist()
    feats = []
    for s in s_idx[donors[s_idx] != 1].tolist():
        chain = [s]
        cur = s
        while True:
            nxt = rl[cur]
            if nxt < 0 or not is_s[nxt]:
                break
            chain.append(nxt)
            if dn[nxt] != 1:
                break
            cur = nxt
        if len(chain) < 2:
            continue
        line = chaikin(np.array([[lngs[i % cols], lats[i // cols]] for i in chain]), 2)
        coords = [[round(float(x), 6), round(float(y), 6)] for x, y in line]
        feats.append({
            "type": "Feature",
            "properties": {
                "upstream_ha": round(float(acc[chain[-1]]) / 1e4, 2),
                "in_catchment": bool(in_c[chain[0]]),
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return feats
