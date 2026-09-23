"""Pond planning: site selection, catchment, water volume and pond sizing.

Given an elevation grid and (optionally) the land parcel the user selected:

* Site      - the cell inside the parcel where the most water converges
              (maximum flow accumulation), ties broken by lower ground. Water already
              flows there, so the pond fills without any channel work.
* Catchment - every cell whose D8 flow path passes through the site. It usually
              extends well outside the parcel; for satellite DEM analysis the window
              is widened until the catchment no longer touches its edge.
* Water     - mean annual runoff depth (SCS-CN on daily rainfall) x catchment area.
* Pond      - excavated pond with 1.5 H : 1 V side slopes and the chosen depth, sized
              to hold the annual runoff but never more than 60 % of the parcel or the
              single-pond limit (5 ha by default). Its footprint is grown from the site
              over the lowest ground in the parcel.
* Collected - min(annual runoff, pond capacity): the volume that can realistically
              be stored in a year.
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass

import numpy as np

import hydrology as hy
from geo import (GeometryError, M_PER_DEG, bbox_geojson, circle_ring, rasterize_ring, ring_area_m2,
                 ring_bbox, ring_is_simple, ring_perimeter_m)
from rainfall import RainfallService, water_budget

MIN_PARCEL_M2 = 400.0          # 20 m x 20 m
MAX_PARCEL_M2 = 25_000_000.0   # 25 km^2
MAX_POND_FRACTION = 0.6
MIN_POND_M2 = 100.0
MAX_WINDOW_CELLS = 200_000
LARGE_CATCHMENT_HA = 1000.0


@dataclass
class Params:
    pond_depth_m: float = 3.0
    curve_number: float = 80.0
    side_slope: float = 1.5
    max_pond_area_m2: float = 50_000.0  # one village pond; larger yields call for a chain of ponds

    def validated(self) -> "Params":
        if not (1.0 <= self.pond_depth_m <= 8.0):
            raise GeometryError("Pond depth must be between 1 and 8 m.")
        if not (40 <= self.curve_number <= 98):
            raise GeometryError("Curve number must be between 40 and 98.")
        if not (0.5 <= self.side_slope <= 4):
            raise GeometryError("Side slope must be between 0.5 and 4 (horizontal : vertical).")
        if not (500 <= self.max_pond_area_m2 <= 500_000):
            raise GeometryError("Maximum pond area must be between 500 m2 and 50 ha.")
        return self


@dataclass
class FlowModel:
    """Everything that depends only on the terrain (reusable for any parcel on the same grid)."""
    grid: hy.Grid
    valid: np.ndarray
    filled: np.ndarray
    recv: np.ndarray
    acc: np.ndarray
    order: np.ndarray
    edge: np.ndarray
    ms: dict


@dataclass
class Hydro:
    grid: hy.Grid
    valid: np.ndarray
    filled: np.ndarray
    recv: np.ndarray
    acc: np.ndarray
    order: np.ndarray
    parcel: np.ndarray
    outlet: int
    catchment: np.ndarray
    touches_edge: bool
    ms: dict


def validate_parcel(ring) -> dict:
    area = ring_area_m2(ring)
    if area < MIN_PARCEL_M2:
        raise GeometryError(f"Selected area is only {area:.0f} m2; select at least {MIN_PARCEL_M2:.0f} m2 (20 m x 20 m).")
    if area > MAX_PARCEL_M2:
        raise GeometryError(f"Selected area is {area / 1e6:.1f} km2; the limit is {MAX_PARCEL_M2 / 1e6:.0f} km2 per analysis.")
    if not ring_is_simple(ring):
        raise GeometryError("The polygon edges cross each other; redraw the land boundary.")
    return {"area_m2": area, "perimeter_m": ring_perimeter_m(ring)}


def flow_model(grid: hy.Grid) -> FlowModel:
    ms = {}
    t = time.perf_counter()
    valid = np.isfinite(grid.z)
    if valid.sum() < 9:
        raise GeometryError("No elevation data is available for this area.")
    filled = hy.priority_flood_fill(grid.z, valid)
    ms["fill"] = (time.perf_counter() - t) * 1e3

    t = time.perf_counter()
    recv = hy.d8_receivers(filled, valid, grid.dx_m, grid.dy_m)
    acc, order = hy.flow_accumulation(filled, recv, valid, grid.cell_area)
    ms["flow"] = (time.perf_counter() - t) * 1e3
    return FlowModel(grid, valid, filled, recv, acc, order, hy.boundary_cells(valid), ms)


def run_hydrology(grid: hy.Grid, ring) -> Hydro:
    return select_site(flow_model(grid), ring)


def select_site(fm: FlowModel, ring) -> Hydro:
    grid, valid, filled, recv, acc, order, edge = fm.grid, fm.valid, fm.filled, fm.recv, fm.acc, fm.order, fm.edge
    ms = dict(fm.ms)
    t = time.perf_counter()
    if ring is not None:
        parcel = rasterize_ring(ring, grid.lats, grid.lngs) & valid
        if not parcel.any():  # parcel smaller than one cell: use the cell under its centroid
            clng = sum(p[0] for p in ring) / len(ring)
            clat = sum(p[1] for p in ring) / len(ring)
            r = int(np.argmin(np.abs(grid.lats - clat)))
            c = int(np.argmin(np.abs(grid.lngs - clng)))
            if not valid[r, c]:
                raise GeometryError("No elevation data inside the selected area.")
            parcel = np.zeros_like(valid)
            parcel[r, c] = True
    else:
        parcel = valid.copy()

    # Cells on the data edge drain "off the map" artificially; avoid them when possible.
    candidates = parcel & ~edge
    if ring is None:
        inner = valid.copy()
        for _ in range(3):
            inner &= ~hy.boundary_cells(inner)
        if (candidates & inner).any():
            candidates &= inner
    if not candidates.any():
        candidates = parcel
    flat_c = np.flatnonzero(candidates.ravel())
    acc_c = acc[flat_c]
    best = acc_c.max()
    ties = flat_c[acc_c >= best * (1 - 1e-9)]
    outlet = int(ties[np.argmin(filled.ravel()[ties])])
    catchment = hy.upstream_mask(recv, order, outlet, grid.shape)
    touches = bool((catchment & edge).any())
    ms["catchment"] = (time.perf_counter() - t) * 1e3
    return Hydro(grid, valid, filled, recv, acc, order, parcel, outlet, catchment, touches, ms)


def dem_hydrology(store, ring, parcel_info) -> tuple[Hydro, dict]:
    """Run on satellite DEM windows, widening the window until the catchment fits."""
    min_lng, min_lat, max_lng, max_lat = ring_bbox(ring)
    mid_lat = (min_lat + max_lat) / 2
    kx = M_PER_DEG * math.cos(math.radians(mid_lat))
    extent = max((max_lng - min_lng) * kx, (max_lat - min_lat) * M_PER_DEG)
    buffer_m = min(max(1.5 * extent, 1200.0), 3000.0)
    tries = []
    hydro = None
    t_dem = 0.0
    while True:
        # keep the window inside the cell budget (1" cells ~ 30.9 m x 28.8 m here)
        w_cells = ((max_lng - min_lng) * kx + 2 * buffer_m) / (M_PER_DEG / 3600 * math.cos(math.radians(mid_lat)))
        h_cells = ((max_lat - min_lat) * M_PER_DEG + 2 * buffer_m) / (M_PER_DEG / 3600)
        at_limit = w_cells * h_cells >= MAX_WINDOW_CELLS * 0.97
        if at_limit:
            scale = math.sqrt(MAX_WINDOW_CELLS * 0.97 / (w_cells * h_cells))
            buffer_m = max(0.0, (buffer_m * 2 + extent) * scale - extent) / 2
        dlat = buffer_m / M_PER_DEG
        dlng = buffer_m / kx
        t = time.perf_counter()
        grid = store.window(min_lat - dlat, min_lng - dlng, max_lat + dlat, max_lng + dlng,
                            max_cells=int(MAX_WINDOW_CELLS * 1.3))
        t_dem += (time.perf_counter() - t) * 1e3
        hydro = run_hydrology(grid, ring)
        tries.append({"buffer_m": round(buffer_m), "cells": int(grid.z.size), "catchment_touches_edge": hydro.touches_edge})
        if not hydro.touches_edge or at_limit or len(tries) >= 4:
            break
        buffer_m *= 2.2
    hydro.ms["dem_read"] = t_dem
    return hydro, {"window_attempts": tries}


COMPACTNESS_M_PER_M = 1 / 40.0  # 40 m farther from the site weighs like 1 m higher ground


def _grow_footprint(hydro: Hydro, target_m2: float) -> tuple[np.ndarray, float]:
    """Cells of the parcel around the site until ``target_m2`` is covered, taking low ground
    first but penalising distance from the site so the pond stays compact (a pond is dug as
    one basin, not traced along every low furrow)."""
    grid = hydro.grid
    rows, cols = grid.shape
    area_row = grid.cell_area[:, 0]
    dx = grid.dx_m
    dy = grid.dy_m
    z = np.where(hydro.valid, grid.z, np.inf)
    allowed = hydro.parcel
    start = hydro.outlet
    r0, c0 = divmod(start, cols)

    def cost(rr: int, cc: int) -> float:
        dist = math.hypot((rr - r0) * dy, (cc - c0) * dx[rr])
        return float(z[rr, cc]) + dist * COMPACTNESS_M_PER_M

    seen = {start}
    heap = [(cost(r0, c0), start)]
    chosen = []
    area = 0.0
    while heap and area < target_m2:
        _, i = heapq.heappop(heap)
        chosen.append(i)
        area += area_row[i // cols]
        r, c = divmod(i, cols)
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols:
                j = rr * cols + cc
                if j not in seen and allowed[rr, cc]:
                    seen.add(j)
                    heapq.heappush(heap, (cost(rr, cc), j))
    mask = np.zeros(grid.shape, dtype=bool)
    mask.flat[chosen] = True
    return mask, area


def pond_volume(top_area: float, depth: float, side_slope: float) -> tuple[float, float]:
    """Frustum volume of a square pond; returns (volume m3, usable depth m)."""
    side = math.sqrt(max(top_area, 0.0))
    usable = min(depth, side / (2 * side_slope))  # a small pond cannot reach full depth
    bottom = max(side - 2 * side_slope * usable, 0.0)
    vol = usable / 3.0 * (side * side + bottom * bottom + side * bottom)
    return vol, usable


def size_pond(runoff_m3: float, max_area: float, params: Params) -> float:
    """Top area whose storage matches the annual runoff, clamped to [MIN_POND_M2, max_area]."""
    lo, hi = MIN_POND_M2, max(MIN_POND_M2, max_area)
    if pond_volume(hi, params.pond_depth_m, params.side_slope)[0] <= runoff_m3:
        return hi
    if pond_volume(lo, params.pond_depth_m, params.side_slope)[0] >= runoff_m3:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if pond_volume(mid, params.pond_depth_m, params.side_slope)[0] < runoff_m3:
            lo = mid
        else:
            hi = mid
    return hi


def classify_slope(pct: float) -> str:
    if pct < 2:
        return "Flat"
    if pct < 5:
        return "Gently sloping"
    if pct < 10:
        return "Moderately sloping"
    return "Steep"


def build_result(hydro: Hydro, ring, parcel_info: dict | None, params: Params, rain: RainfallService,
                 extra_layers: dict | None = None, notes: list[str] | None = None) -> dict:
    t0 = time.perf_counter()
    grid = hydro.grid
    rows, cols = grid.shape
    r, c = divmod(hydro.outlet, cols)
    site_lat, site_lng = float(grid.lats[r]), float(grid.lngs[c])
    site_elev = float(grid.z[r, c])
    depression_m = max(0.0, float(hydro.filled[r, c] - grid.z[r, c]))

    cell_area = np.broadcast_to(grid.cell_area, grid.shape)
    catch_m2 = float(cell_area[hydro.catchment].sum())
    parcel_m2 = parcel_info["area_m2"] if parcel_info else float(cell_area[hydro.parcel].sum())

    slope = hy.slope_percent(hy.smooth_nan(grid.z, 1.0), grid.dx_m, grid.dy_m)
    catch_slope = float(np.nanmean(slope[hydro.catchment])) if hydro.catchment.any() else 0.0
    zc = grid.z[hydro.catchment]
    relief = float(np.nanmax(zc) - np.nanmin(zc)) if zc.size else 0.0

    t = time.perf_counter()
    series = rain.series(site_lat, site_lng)
    budget = water_budget(series, params.curve_number)
    ms_rain = (time.perf_counter() - t) * 1e3

    runoff_m3 = budget["runoff_depth_mm"] / 1000.0 * catch_m2
    land_limit = min(MAX_POND_FRACTION * parcel_m2, float(cell_area[hydro.parcel].sum()) or parcel_m2)
    max_area = min(land_limit, params.max_pond_area_m2)
    top_area = size_pond(runoff_m3, max_area, params)
    limited_by = ("land" if land_limit <= params.max_pond_area_m2 else "max_pond_area") \
        if top_area >= max_area * 0.999 else "runoff"

    # footprint over the lowest parcel ground around the site
    fp_mask, fp_area = _grow_footprint(hydro, top_area)
    cell_m2 = float(grid.cell_area[r, 0])
    if fp_mask.sum() >= 9:
        top_area = min(top_area, fp_area)
        footprint = hy.mask_to_geojson(grid, fp_mask, smooth=2)
        fr, fc = np.nonzero(fp_mask)
        centre = [round(float(grid.lngs[fc].mean()), 6), round(float(grid.lats[fr].mean()), 6)]
    else:
        footprint = {"type": "Polygon", "coordinates": [circle_ring(site_lat, site_lng, math.sqrt(top_area / math.pi))]}
        centre = [round(site_lng, 6), round(site_lat, 6)]
    capacity, usable_depth = pond_volume(top_area, params.pond_depth_m, params.side_slope)
    collected = min(runoff_m3, capacity)
    side = math.sqrt(top_area)

    # map layers -------------------------------------------------------------
    t = time.perf_counter()
    catch_geo = hy.mask_to_geojson(grid, hydro.catchment, smooth=2)
    stream_thr = max(20 * cell_m2, min(catch_m2 / 25.0, 250_000.0))
    streams = hy.stream_features(grid, hydro.recv, hydro.acc, hydro.valid, stream_thr, hydro.catchment)
    layers = {"streams": {"type": "FeatureCollection", "features": streams}}
    if extra_layers and "contours" in extra_layers:
        layers["contours"] = extra_layers["contours"]
        contour_interval = extra_layers.get("contour_interval_m")
    else:
        zs = hy.smooth_nan(grid.z, 2.0)  # display only: 30 m DSM carries tree and bund noise
        finite = zs[np.isfinite(zs)]
        contour_interval = hy.nice_interval(float(finite.max() - finite.min())) if finite.size else 1.0
        layers["contours"] = {"type": "FeatureCollection", "features": hy.contour_features(grid, zs, contour_interval)}
    layers["window"] = bbox_geojson(float(grid.lngs[0] - grid.dlng / 2), float(grid.lats[-1] - grid.dlat / 2),
                                    float(grid.lngs[-1] + grid.dlng / 2), float(grid.lats[0] + grid.dlat / 2))
    ms_layers = (time.perf_counter() - t) * 1e3

    notes = list(notes or [])
    if hydro.touches_edge:
        if grid.source.get("type") == "contour_map":
            notes.append("The catchment reaches the edge of the contour map, so the true catchment may be larger "
                         "than reported; upload a contour map that covers the whole upstream slope.")
        else:
            notes.append("The catchment is very large and was cut at the analysis limit; the catchment area and "
                         "runoff are lower bounds.")
    if catch_m2 / 1e4 > LARGE_CATCHMENT_HA:
        notes.append(f"The site sits on a major drainage line ({catch_m2 / 1e6:.1f} km2 upstream). A check dam or an "
                     "off-stream pond with a diversion is safer than an on-stream pond here.")
    if runoff_m3 > capacity * 1.05:
        surplus = runoff_m3 - capacity
        if limited_by == "max_pond_area":
            notes.append(f"Runoff exceeds one {params.max_pond_area_m2 / 1e4:g} ha pond by {surplus:,.0f} m3/yr; the surplus "
                         "could fill a chain of ponds or check dams downstream (or raise the maximum pond area).")
        else:
            notes.append(f"Runoff exceeds pond capacity by {surplus:,.0f} m3/yr; the surplus leaves through the "
                         "spillway. A deeper pond or more land would store more.")
    if usable_depth < params.pond_depth_m - 1e-6:
        notes.append(f"The pond is too small to reach {params.pond_depth_m:g} m with {params.side_slope:g}:1 side slopes; "
                     f"usable depth is {usable_depth:.2f} m.")
    if budget["is_fallback"]:
        notes.append("Live rainfall data was unreachable; a regional normal was used.")

    reason = ("Highest flow accumulation inside the selected land: water from "
              f"{catch_m2 / 1e4:,.2f} ha of land drains to this point")
    if depression_m >= 0.3:
        reason += f", which is also a natural depression {depression_m:.1f} m deep"

    return {
        "status": "success",
        "source": grid.source,
        "selected_area": None if parcel_info is None else {
            "area_m2": round(parcel_info["area_m2"], 1),
            "area_ha": round(parcel_info["area_m2"] / 1e4, 3),
            "perimeter_m": round(parcel_info["perimeter_m"], 1),
            "geojson": {"type": "Polygon", "coordinates": [[list(p) for p in ring] + [list(ring[0])]]},
        },
        "pond": {
            "lat": round(site_lat, 6),
            "lng": round(site_lng, 6),
            "centre": centre,
            "elevation_m": round(site_elev, 2),
            "natural_depression_m": round(depression_m, 2),
            "reason": reason,
            "surface_area_m2": round(top_area, 1),
            "side_m": round(side, 1),
            "depth_m": round(usable_depth, 2),
            "side_slope": params.side_slope,
            "size_limited_by": limited_by,
            "storage_capacity_m3": round(capacity, 1),
            "footprint_geojson": footprint,
        },
        "catchment": {
            "area_m2": round(catch_m2, 1),
            "area_ha": round(catch_m2 / 1e4, 3),
            "geojson": catch_geo,
            "mean_slope_pct": round(catch_slope, 2),
            "terrain": classify_slope(catch_slope),
            "relief_m": round(relief, 2),
            "cells": int(hydro.catchment.sum()),
            "touches_data_edge": hydro.touches_edge,
        },
        "water": {
            **budget,
            "curve_number": params.curve_number,
            "harvestable_runoff_m3": round(runoff_m3, 1),
            "collectable_volume_m3": round(collected, 1),
            "collectable_litres": round(collected * 1000.0),
            "pond_fill_percent": round(min(100.0, 100.0 * runoff_m3 / capacity) if capacity > 0 else 0.0, 1),
        },
        "terrain": {
            "elevation_min_m": round(float(np.nanmin(grid.z)), 2),
            "elevation_max_m": round(float(np.nanmax(grid.z)), 2),
            "window_mean_slope_pct": round(float(np.nanmean(slope)), 2),
            "contour_interval_m": contour_interval,
            "grid": {"rows": rows, "cols": cols, "cell_m": round(math.sqrt(cell_m2), 1)},
        },
        "notes": notes,
        "layers": layers,
        "timings_ms": {**{k: round(v, 1) for k, v in hydro.ms.items()},
                       "rainfall": round(ms_rain, 1), "layers": round(ms_layers, 1),
                       "result": round((time.perf_counter() - t0) * 1e3, 1)},
    }
