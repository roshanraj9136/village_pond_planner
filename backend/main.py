"""JalDrishti worker: pond site, catchment and water-volume analysis API.

One worker process runs per system (each system has a single CPU). The Go gateway on
sys1 load-balances across the workers; see ../gateway and ../README.md.

Endpoints
  POST /api/analyze            land parcel (GeoJSON Polygon) -> analysis on satellite DEM
  POST /api/analyze/contour    contour map (KML/KMZ) [+ optional parcel] -> analysis
  GET  /api/sample             analysis of the bundled sample contour map
  GET  /api/sample/contour_map the sample KML itself
  GET  /api/coverage           satellite DEM tiles already cached on this worker
  GET  /api/rainfall           rainfall / runoff summary for a point
  GET|POST|DELETE /api/sites   saved sites (shared PostgreSQL)
  GET  /api/health             liveness + load for the gateway
  POST /analyzeContour, /findCatchment   Phase 2 contract (multipart field ``contour_map``)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import planner
from contours import ContourError, contour_interval, contour_layer, contours_to_grid, parse_contours, read_kml_bytes
from dem import DemStore, TileUnavailable
from geo import GeometryError, parse_polygon
from rainfall import RainfallService, water_budget
from sites import SitesUnavailable, SiteStore

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("POND_DATA", str(Path.home() / "pond-data")))
SAMPLE_KML = Path(os.environ.get("SAMPLE_KML", str(BASE.parent / "sample_data" / "contours_1m.kml")))
WORKER = os.environ.get("WORKER_NAME", os.uname().nodename if hasattr(os, "uname") else "local")
VERSION = os.environ.get("APP_VERSION", "dev")
STARTED = time.time()

dem_store = DemStore(DATA / "dem")
rain = RainfallService(DATA / "rain")
site_store = SiteStore(os.environ.get("PG_DSN"))

# One CPU per system: run one analysis at a time, queue at most a couple, shed the rest
# quickly with 503 so the gateway can place the request on an idle worker instead.
COMPUTE = threading.Semaphore(1)
QUEUE_WAIT_S = float(os.environ.get("QUEUE_WAIT_S", "20"))
_stats_lock = threading.Lock()
stats = {"inflight": 0, "served": 0, "rejected": 0, "errors": 0}

# Flow models of recently used contour maps, keyed by file hash (parcel-independent work).
_flow_cache: OrderedDict[str, tuple] = OrderedDict()
_flow_lock = threading.Lock()

@asynccontextmanager
async def lifespan(_app):
    """Parse the sample map once in the background so the first demo request is instant."""
    def warm():
        try:
            if SAMPLE_KML.exists():
                _contour_flow(SAMPLE_KML.read_bytes(), SAMPLE_KML.name)
        except Exception:  # noqa: BLE001 - warming is best effort
            pass
    threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="JalDrishti - Village Pond Planner API",
    version=VERSION,
    description="Suggests a pond location inside a selected land parcel, delineates its catchment and "
                "estimates the water that can be collected. CS559 Assignment 1 (IIT Bhilai).",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


@app.middleware("http")
async def tag_worker(request: Request, call_next):
    t = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Worker"] = WORKER
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - t) * 1e3:.1f}"
    return response


class Busy(Exception):
    pass


def _malloc_trim():
    """Hand freed heap back to the OS after each analysis (glibc keeps it otherwise), so a
    worker's resident memory returns to baseline between requests on a 512 MiB container."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        return lambda: libc.malloc_trim(0)
    except (OSError, AttributeError):
        return lambda: None


malloc_trim = _malloc_trim()


class compute_slot:
    def __enter__(self):
        with _stats_lock:
            stats["inflight"] += 1
        if not COMPUTE.acquire(timeout=QUEUE_WAIT_S):
            with _stats_lock:
                stats["inflight"] -= 1
                stats["rejected"] += 1
            raise Busy()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        COMPUTE.release()
        malloc_trim()
        with _stats_lock:
            stats["inflight"] -= 1
            stats["served"] += 1
            if exc_type is not None and exc_type not in (GeometryError, ContourError):
                stats["errors"] += 1
        return False


def _error(status: int, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"status": "error", "detail": message}, status_code=status, headers=headers)


@app.exception_handler(Busy)
async def _busy(_, __):
    return _error(503, "All analysis slots on this worker are busy; retry shortly.", {"Retry-After": "2", "X-Worker-Busy": "1"})


@app.exception_handler(GeometryError)
async def _geom(_, exc):
    return _error(422, str(exc))


@app.exception_handler(ContourError)
async def _contour(_, exc):
    return _error(422, str(exc))


@app.exception_handler(TileUnavailable)
async def _tile(_, exc):
    return _error(502, str(exc))


# ------------------------------------------------------------------ models

class AnalyzeRequest(BaseModel):
    area: dict = Field(..., description="Land parcel as a GeoJSON Polygon (or Feature), coordinates [lng, lat].")
    pond_depth_m: float = Field(3.0, ge=1.0, le=8.0, description="Design pond depth (m).")
    curve_number: float = Field(80.0, ge=40, le=98, description="SCS runoff curve number for the catchment's land cover.")
    side_slope: float = Field(1.5, ge=0.5, le=4.0, description="Pond side slope, horizontal : vertical.")
    max_pond_area_m2: float = Field(50_000, ge=500, le=500_000, description="Largest single pond to suggest (m2).")


class SiteIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    lat: float = Field(..., ge=-85, le=85)
    lng: float = Field(..., ge=-180, le=180)
    source_type: str = Field(..., max_length=32)
    parcel_ha: Optional[float] = Field(None, ge=0, le=1e6)
    catchment_ha: float = Field(..., ge=0, le=1e7)
    runoff_m3: float = Field(..., ge=0, le=1e10)
    capacity_m3: float = Field(..., ge=0, le=1e10)
    collectable_m3: float = Field(..., ge=0, le=1e10)
    pond_area_m2: float = Field(..., ge=0, le=1e9)
    depth_m: float = Field(..., ge=0, le=20)
    area_geojson: Optional[dict] = None


def _params(pond_depth_m: float, curve_number: float, side_slope: float,
            max_pond_area_m2: float = 50_000) -> planner.Params:
    return planner.Params(pond_depth_m=pond_depth_m, curve_number=curve_number, side_slope=side_slope,
                          max_pond_area_m2=max_pond_area_m2).validated()


def _finish(result: dict, t0: float) -> dict:
    result["served_by"] = WORKER
    result["version"] = VERSION
    result["timings_ms"]["total"] = round((time.perf_counter() - t0) * 1e3, 1)
    return result


# ------------------------------------------------------------------ core analyses

def analyze_parcel(area: dict, params: planner.Params) -> dict:
    t0 = time.perf_counter()
    ring = parse_polygon(area)
    info = planner.validate_parcel(ring)
    with compute_slot():
        hydro, meta = planner.dem_hydrology(dem_store, ring, info)
        result = planner.build_result(hydro, ring, info, params, rain)
    result["analysis_window"] = meta
    return _finish(result, t0)


def _contour_flow(data: bytes, filename: str):
    key = hashlib.sha256(data).hexdigest()
    with _flow_lock:
        hit = _flow_cache.get(key)
        if hit:
            _flow_cache.move_to_end(key)
            return hit + (True,)
    lines = parse_contours(read_kml_bytes(data, filename))
    grid = contours_to_grid(lines)
    fm = planner.flow_model(grid)
    meta = contour_metadata(lines, filename)
    layer = {"type": "FeatureCollection", "features": contour_layer(lines)}
    entry = (fm, meta, layer)
    with _flow_lock:
        _flow_cache[key] = entry
        while len(_flow_cache) > 6:
            _flow_cache.popitem(last=False)
    return entry + (False,)


def contour_metadata(lines, filename: str) -> dict:
    pts = np.vstack([ln.coords for ln in lines])
    elevs = [ln.elev for ln in lines]
    return {
        "filename": filename,
        "total_contour_lines": len(lines),
        "total_vertices": int(pts.shape[0]),
        "elevation_min_m": round(min(elevs), 2),
        "elevation_max_m": round(max(elevs), 2),
        "contour_interval_m": contour_interval(lines),
        "bounding_box": {"min_lat": float(pts[:, 1].min()), "max_lat": float(pts[:, 1].max()),
                         "min_lng": float(pts[:, 0].min()), "max_lng": float(pts[:, 0].max())},
    }


def analyze_contour(data: bytes, filename: str, area: dict | None, params: planner.Params) -> dict:
    t0 = time.perf_counter()
    ring = parse_polygon(area) if area else None
    info = planner.validate_parcel(ring) if ring else None
    with compute_slot():
        fm, meta, layer, cached = _contour_flow(data, filename)
        hydro = planner.select_site(fm, ring)
        if cached:
            hydro.ms = {k: 0.0 for k in ("fill", "flow")} | {k: v for k, v in hydro.ms.items() if k not in ("fill", "flow")}
        result = planner.build_result(hydro, ring, info, params, rain,
                                      extra_layers={"contours": layer, "contour_interval_m": meta["contour_interval_m"]})
    result["contour_map"] = meta
    result["source"] = {**result["source"], "name": f"Contour map: {filename}", "flow_model_cached": cached}
    return _finish(result, t0)


def legacy_response(result: dict) -> dict:
    """Phase 2 response contract (same keys as before) plus the full Phase 3 analysis."""
    meta = result["contour_map"]
    return {
        "status": "success",
        "message": "Terrain analysed with Priority-Flood depression filling and D8 flow accumulation.",
        "contour_metadata": {
            "total_contour_lines": meta["total_contour_lines"],
            "elevation_min_meters": meta["elevation_min_m"],
            "elevation_max_meters": meta["elevation_max_m"],
            "elevation_range_meters": round(meta["elevation_max_m"] - meta["elevation_min_m"], 2),
            "contour_interval_meters": meta["contour_interval_m"],
            "bounding_box": meta["bounding_box"],
        },
        "terrain_metrics": {
            "average_slope_percent": result["catchment"]["mean_slope_pct"],
            "terrain_classification": result["catchment"]["terrain"],
            "runoff_coefficient": result["water"]["runoff_coefficient"],
            "annual_rainfall_mm": result["water"]["annual_rainfall_mm"],
        },
        "pond_location": {
            "latitude": result["pond"]["lat"],
            "longitude": result["pond"]["lng"],
            "elevation_meters": result["pond"]["elevation_m"],
            "site_suitability": "Maximum flow accumulation point",
        },
        "catchment_analysis": {
            "catchment_area_sq_meters": result["catchment"]["area_m2"],
            "catchment_area_hectares": result["catchment"]["area_ha"],
            "estimated_runoff_volume_cubic_meters": result["water"]["harvestable_runoff_m3"],
            "recommended_pond_surface_area_sq_meters": result["pond"]["surface_area_m2"],
            "recommended_pond_depth_meters": result["pond"]["depth_m"],
            "estimated_storage_capacity_cubic_meters": result["pond"]["storage_capacity_m3"],
            "expected_collectable_volume_cubic_meters": result["water"]["collectable_volume_m3"],
            "boundary_geojson": result["catchment"]["geojson"],
        },
        "analysis": result,
    }


def _read_sample() -> bytes:
    if not SAMPLE_KML.exists():
        raise HTTPException(404, "Sample contour map is not installed on this worker.")
    return SAMPLE_KML.read_bytes()


def _parse_area_field(area: Optional[str]) -> dict | None:
    if area is None or not area.strip():
        return None
    try:
        return json.loads(area)
    except ValueError as exc:
        raise GeometryError("The 'area' field must be GeoJSON text.") from exc


async def _read_upload(upload: UploadFile) -> tuple[bytes, str]:
    name = upload.filename or "contour_map.kml"
    if Path(name).suffix.lower() not in (".kml", ".kmz"):
        raise HTTPException(400, f"Unsupported file type '{Path(name).suffix}'. Upload a .kml or .kmz contour map.")
    data = await upload.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    return data, name


# ------------------------------------------------------------------ routes

@app.get("/api/health", tags=["system"])
async def health():
    with _stats_lock:
        s = dict(stats)
    return {"status": "ok", "worker": WORKER, "version": VERSION, "uptime_s": round(time.time() - STARTED), **s}


@app.post("/api/analyze", tags=["analysis"], summary="Analyse a land parcel on satellite elevation data")
def api_analyze(req: AnalyzeRequest):
    return analyze_parcel(req.area, _params(req.pond_depth_m, req.curve_number, req.side_slope, req.max_pond_area_m2))


@app.post("/api/analyze/contour", tags=["analysis"], summary="Analyse a contour map, optionally within a land parcel")
async def api_analyze_contour(
    contour_map: Optional[UploadFile] = File(None, description="KML or KMZ contour map"),
    use_sample: bool = Form(False, description="Use the bundled contours_1m.kml instead of an upload"),
    area: Optional[str] = Form(None, description="Optional land parcel as GeoJSON Polygon text"),
    pond_depth_m: float = Form(3.0, ge=1.0, le=8.0),
    curve_number: float = Form(80.0, ge=40, le=98),
    side_slope: float = Form(1.5, ge=0.5, le=4.0),
    max_pond_area_m2: float = Form(50_000, ge=500, le=500_000),
):
    if contour_map is not None and contour_map.filename:
        data, name = await _read_upload(contour_map)
    elif use_sample:
        data, name = _read_sample(), SAMPLE_KML.name
    else:
        raise HTTPException(400, "Upload a contour map in the 'contour_map' field or set use_sample=true.")
    return await run_in_threadpool(analyze_contour, data, name, _parse_area_field(area),
                                   _params(pond_depth_m, curve_number, side_slope, max_pond_area_m2))


@app.get("/api/sample", tags=["analysis"], summary="Analysis of the bundled sample contour map")
def api_sample(pond_depth_m: float = Query(3.0, ge=1.0, le=8.0), curve_number: float = Query(80.0, ge=40, le=98)):
    return analyze_contour(_read_sample(), SAMPLE_KML.name, None, _params(pond_depth_m, curve_number, 1.5))


@app.get("/api/sample/contour_map", tags=["analysis"], summary="Download the sample contour map (KML)")
def api_sample_file():
    if not SAMPLE_KML.exists():
        raise HTTPException(404, "Sample contour map is not installed on this worker.")
    return FileResponse(SAMPLE_KML, media_type="application/vnd.google-earth.kml+xml", filename=SAMPLE_KML.name)


@app.get("/api/coverage", tags=["system"], summary="Satellite DEM tiles cached on this worker")
def api_coverage():
    return {"worker": WORKER, "tiles": dem_store.coverage(),
            "note": "Areas outside these tiles work too; their tile is downloaded on first use (~40 MB)."}


@app.get("/api/rainfall", tags=["analysis"], summary="Rainfall and runoff depth at a point")
def api_rainfall(lat: float = Query(..., ge=-85, le=85), lng: float = Query(..., ge=-180, le=180),
                 curve_number: float = Query(80.0, ge=40, le=98)):
    return {"lat": lat, "lng": lng, **water_budget(rain.series(lat, lng), curve_number)}


@app.get("/api/sites", tags=["sites"])
def list_sites():
    try:
        return {"sites": site_store.list()}
    except SitesUnavailable as exc:
        return _error(503, str(exc))


@app.post("/api/sites", tags=["sites"], status_code=201)
def add_site(site: SiteIn):
    data = site.model_dump()
    if data.get("area_geojson"):
        parse_polygon(data["area_geojson"])  # reject malformed geometry
    for k in ("lat", "lng", "catchment_ha", "runoff_m3", "capacity_m3", "collectable_m3", "pond_area_m2", "depth_m"):
        if not math.isfinite(data[k]):
            raise GeometryError(f"{k} must be a finite number.")
    try:
        return {"status": "saved", **site_store.add(data)}
    except SitesUnavailable as exc:
        return _error(503, str(exc))


@app.delete("/api/sites/{site_id}", tags=["sites"])
def delete_site(site_id: int):
    try:
        if not site_store.delete(site_id):
            raise HTTPException(404, "No such site.")
        return {"status": "deleted", "id": site_id}
    except SitesUnavailable as exc:
        return _error(503, str(exc))


# ---- Phase 2 contract ------------------------------------------------------------

async def _phase2(contour_map: UploadFile, area: Optional[str]) -> dict:
    data, name = await _read_upload(contour_map)
    result = await run_in_threadpool(analyze_contour, data, name, _parse_area_field(area), planner.Params())
    return legacy_response(result)


PHASE2_DOC = ("Phase 2 route. multipart/form-data with the contour map in field `contour_map` "
              "(.kml or .kmz); optional `area` (GeoJSON Polygon text) restricts the pond to a parcel.")


@app.post("/analyzeContour", tags=["phase 2"], summary="Analyse a contour map (Phase 2 contract)", description=PHASE2_DOC)
async def analyze_contour_route(contour_map: UploadFile = File(...), area: Optional[str] = Form(None)):
    return await _phase2(contour_map, area)


@app.post("/findCatchment", tags=["phase 2"], summary="Alias of /analyzeContour", description=PHASE2_DOC)
async def find_catchment_route(contour_map: UploadFile = File(...), area: Optional[str] = Form(None)):
    return await _phase2(contour_map, area)


@app.post("/api/analyzeContour", include_in_schema=False)
async def analyze_contour_api_alias(contour_map: UploadFile = File(...), area: Optional[str] = Form(None)):
    return await _phase2(contour_map, area)


@app.post("/api/findCatchment", include_in_schema=False)
async def find_catchment_api_alias(contour_map: UploadFile = File(...), area: Optional[str] = Form(None)):
    return await _phase2(contour_map, area)


@app.get("/analyzeContour", tags=["phase 2"], summary="Sample contour map result in the Phase 2 format")
def analyze_contour_sample():
    return legacy_response(analyze_contour(_read_sample(), SAMPLE_KML.name, None, planner.Params()))


@app.get("/findCatchment", include_in_schema=False)
def find_catchment_sample():
    return analyze_contour_sample()


@app.get("/api/sampleContour", include_in_schema=False)
def sample_contour_alias():
    return analyze_contour_sample()
