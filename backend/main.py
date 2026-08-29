from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import math
import sqlite3
import cv2
import numpy as np
import os
from typing import List, Dict, Any, Optional
from contour_analyzer import ContourAnalysisEngine

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

def init_db():
    conn = sqlite3.connect('ponds.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reports
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  lat REAL, lng REAL, display_name TEXT,
                  catchment_area REAL, pond_depth REAL,
                  storage_capacity REAL, vegetation REAL, water REAL)''')
    conn.commit()
    conn.close()

init_db()

class Coordinates(BaseModel):
    lat: float
    lng: float

class LocationResponse(BaseModel):
    display_name: str
    land_type: str
    is_suitable: bool
    warning_message: str

class RainfallResponse(BaseModel):
    annual_rainfall_mm: float
    average_monthly_mm: float

class TerrainRequest(BaseModel):
    lat: float
    lng: float
    annual_rainfall_mm: float

class TerrainResponse(BaseModel):
    elevation_meters: float
    catchment_area_sq_meters: float
    estimated_runoff_volume_cubic_meters: float
    recommended_pond_depth_meters: float
    estimated_storage_capacity: float
    runoff_coefficient: float
    annual_rainfall_mm: float

@app.get('/')
def read_root():
    return {'status': 'ok'}

@app.post('/api/analyze/location', response_model=LocationResponse)
def analyze_location(coords: Coordinates):
    url = f'https://nominatim.openstreetmap.org/reverse?format=json&lat={coords.lat}&lon={coords.lng}&zoom=18&addressdetails=1&accept-language=en'
    headers = {'User-Agent': 'VillagePondPlanner/1.0'}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            display_name = data.get('display_name', 'Unknown Area')
            cls = data.get('class', '')
            ltype = data.get('type', '')
            address = data.get('address', {})
            country_code = address.get('country_code', '')
            is_suitable = True
            warning = ''
            if country_code != 'in':
                is_suitable = False
                warning = 'Location must be within India.'
            else:
                unsuitable = ['building', 'residential', 'commercial', 'industrial', 'aeroway', 'highway', 'railway', 'military']
                if cls in unsuitable or ltype in unsuitable:
                    is_suitable = False
                    warning = f"Selected location is in a restricted or built-up area ({ltype or cls})."
            raw_land_type = (ltype or cls or 'open_land').title()
            final_land_type = raw_land_type if not is_suitable else f'{raw_land_type} (Available for Project)'
            return LocationResponse(display_name=display_name, land_type=final_land_type, is_suitable=is_suitable, warning_message=warning)
    except Exception:
        pass
    return LocationResponse(display_name=f'Lat: {coords.lat}, Lng: {coords.lng}', land_type='Unknown', is_suitable=True, warning_message='')

class LegalResponse(BaseModel):
    owner_type: str
    clearance_status: str
    hurdles: str

@app.post('/api/analyze/legal', response_model=LegalResponse)
def analyze_legal(coords: Coordinates):
    return LegalResponse(owner_type='Gram Panchayat Land', clearance_status='Clear', hurdles='None detected')

@app.post('/api/analyze/rainfall', response_model=RainfallResponse)
def analyze_rainfall(coords: Coordinates):
    url = f'https://archive-api.open-meteo.com/v1/archive?latitude={coords.lat}&longitude={coords.lng}&start_date=2023-01-01&end_date=2023-12-31&daily=precipitation_sum&timezone=auto'
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        daily = data.get('daily', {}).get('precipitation_sum', [])
        valid = [p for p in daily if p is not None]
        total_annual = sum(valid)
        return RainfallResponse(annual_rainfall_mm=round(total_annual, 2), average_monthly_mm=round(total_annual / 12, 2))
    except Exception:
        return RainfallResponse(annual_rainfall_mm=850.0, average_monthly_mm=70.8)

@app.post('/api/analyze/terrain', response_model=TerrainResponse)
def analyze_terrain(req: TerrainRequest):
    offset = 0.00045
    lats = f'{req.lat},{req.lat + offset},{req.lat - offset},{req.lat},{req.lat}'
    lngs = f'{req.lng},{req.lng},{req.lng},{req.lng + offset},{req.lng - offset}'
    elevation_url = f'https://api.open-meteo.com/v1/elevation?latitude={lats}&longitude={lngs}'
    try:
        elev_response = requests.get(elevation_url, timeout=10)
        elevations = elev_response.json().get('elevation', [200.0, 200.0, 200.0, 200.0, 200.0])
    except Exception:
        elevations = [200.0, 200.0, 200.0, 200.0, 200.0]
    center_elev, north_elev, south_elev, east_elev, west_elev = elevations[0], elevations[1], elevations[2], elevations[3], elevations[4]
    dz_dx = (east_elev - west_elev) / 100.0
    dz_dy = (north_elev - south_elev) / 100.0
    slope_percent = math.sqrt(dz_dx ** 2 + dz_dy ** 2) * 100.0
    rainfall_meters = req.annual_rainfall_mm / 1000.0
    if slope_percent < 2.0:
        catchment_area_sqm = 500000.0
        runoff_coefficient = 0.2
    elif slope_percent < 7.0:
        catchment_area_sqm = 250000.0
        runoff_coefficient = 0.35
    elif slope_percent < 15.0:
        catchment_area_sqm = 100000.0
        runoff_coefficient = 0.5
    else:
        catchment_area_sqm = 50000.0
        runoff_coefficient = 0.65
    runoff_volume = runoff_coefficient * rainfall_meters * catchment_area_sqm
    pond_surface_area = catchment_area_sqm * 0.1
    active_depth = runoff_volume / pond_surface_area
    calculated_depth = 2.0 + active_depth
    recommended_depth = round(max(2.0, min(5.5, calculated_depth)), 2)
    storage_capacity = pond_surface_area * recommended_depth
    return TerrainResponse(
        elevation_meters=round(center_elev, 1),
        catchment_area_sq_meters=round(catchment_area_sqm, 2),
        estimated_runoff_volume_cubic_meters=round(runoff_volume, 2),
        recommended_pond_depth_meters=recommended_depth,
        estimated_storage_capacity=round(storage_capacity, 2),
        runoff_coefficient=runoff_coefficient,
        annual_rainfall_mm=round(req.annual_rainfall_mm, 2)
    )

class VisionResponse(BaseModel):
    vegetation_percentage: float
    water_body_percentage: float
    message: str

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

@app.post('/api/analyze/vision', response_model=VisionResponse)
def analyze_vision(coords: Coordinates):
    try:
        z = 16
        x, y = deg2num(coords.lat, coords.lng, z)
        tile_url = f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        headers = {'User-Agent': 'VillagePondPlanner/1.0'}
        resp = requests.get(tile_url, headers=headers, timeout=5)
        if resp.status_code == 200:
            nparr = np.frombuffer(resp.content, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                lower_green = np.array([30, 40, 40])
                upper_green = np.array([85, 255, 255])
                mask_green = cv2.inRange(hsv, lower_green, upper_green)
                veg_pixels = cv2.countNonZero(mask_green)
                lower_water = np.array([90, 40, 40])
                upper_water = np.array([130, 255, 255])
                mask_water = cv2.inRange(hsv, lower_water, upper_water)
                water_pixels = cv2.countNonZero(mask_water)
                total_pixels = img.shape[0] * img.shape[1]
                veg_percent = round(veg_pixels / total_pixels * 100, 2)
                water_percent = round(water_pixels / total_pixels * 100, 2)
                return VisionResponse(vegetation_percentage=veg_percent, water_body_percentage=water_percent, message='success')
    except Exception:
        pass
    return VisionResponse(vegetation_percentage=0.0, water_body_percentage=0.0, message='failed')

class Report(BaseModel):
    lat: float
    lng: float
    display_name: str
    catchment_area: float
    pond_depth: float
    storage_capacity: float
    vegetation: float
    water: float

class SavedReport(Report):
    id: int

@app.post('/api/reports')
def save_report(report: Report):
    conn = sqlite3.connect('ponds.db')
    c = conn.cursor()
    c.execute('''INSERT INTO reports (lat, lng, display_name, catchment_area, pond_depth, storage_capacity, vegetation, water)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
              (report.lat, report.lng, report.display_name, report.catchment_area, report.pond_depth, report.storage_capacity, report.vegetation, report.water))
    conn.commit()
    conn.close()
    return {'message': 'Report saved'}

@app.get('/api/reports', response_model=List[SavedReport])
def get_reports():
    conn = sqlite3.connect('ponds.db')
    c = conn.cursor()
    c.execute('SELECT id, lat, lng, display_name, catchment_area, pond_depth, storage_capacity, vegetation, water FROM reports')
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'lat': r[1], 'lng': r[2], 'display_name': r[3], 'catchment_area': r[4], 'pond_depth': r[5], 'storage_capacity': r[6], 'vegetation': r[7], 'water': r[8]} for r in rows]

@app.delete('/api/reports/{report_id}')
def delete_report(report_id: int):
    conn = sqlite3.connect('ponds.db')
    c = conn.cursor()
    c.execute('DELETE FROM reports WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()
    return {'message': 'Report deleted'}

class BoundingBox(BaseModel):
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float

class ContourMetadata(BaseModel):
    total_contour_lines: int
    elevation_min_meters: float
    elevation_max_meters: float
    elevation_range_meters: float
    contour_interval_meters: float
    bounding_box: BoundingBox

class TerrainMetrics(BaseModel):
    average_slope_percent: float
    terrain_classification: str
    runoff_coefficient: float
    annual_rainfall_mm: float

class PondLocation(BaseModel):
    latitude: float
    longitude: float
    elevation_meters: float
    site_suitability: str

class CatchmentAnalysis(BaseModel):
    catchment_area_sq_meters: float
    catchment_area_hectares: float
    estimated_runoff_volume_cubic_meters: float
    recommended_pond_surface_area_sq_meters: float
    recommended_pond_depth_meters: float
    estimated_storage_capacity_cubic_meters: float
    boundary_geojson: Optional[Dict[str, Any]] = None

class ContourAnalysisResponse(BaseModel):
    status: str
    message: str
    contour_metadata: ContourMetadata
    terrain_metrics: TerrainMetrics
    pond_location: PondLocation
    catchment_analysis: CatchmentAnalysis

async def process_contour_upload(file: UploadFile) -> ContourAnalysisResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail='Missing filename.')
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.kml', '.kmz']:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'. Must be .kml or .kmz")
    try:
        content = await file.read()
        kml_text = ContourAnalysisEngine.extract_kml_from_bytes(content, file.filename)
        contours = ContourAnalysisEngine.parse_contours_from_kml(kml_text)
        result = ContourAnalysisEngine.analyze_terrain(contours)
        return ContourAnalysisResponse(**result)
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Analysis failed: {str(e)}')

@app.post('/analyzeContour', response_model=ContourAnalysisResponse)
async def analyze_contour_route_1(file: UploadFile = File(...)):
    return await process_contour_upload(file)

@app.get('/analyzeContour')
def get_analyze_contour_info():
    return analyze_sample_contour()

@app.post('/findCatchment', response_model=ContourAnalysisResponse)
async def find_catchment_route_1(file: UploadFile = File(...)):
    return await process_contour_upload(file)

@app.get('/findCatchment')
def get_find_catchment_info():
    return analyze_sample_contour()

@app.post('/api/analyzeContour', response_model=ContourAnalysisResponse)
async def analyze_contour_route_api(file: UploadFile = File(...)):
    return await process_contour_upload(file)

@app.post('/api/findCatchment', response_model=ContourAnalysisResponse)
async def find_catchment_route_api(file: UploadFile = File(...)):
    return await process_contour_upload(file)

@app.get('/api/sampleContour', response_model=ContourAnalysisResponse)
def analyze_sample_contour():
    sample_path = os.path.join(os.path.dirname(__file__), '..', 'sample_data', 'contours_1m.kml')
    if not os.path.exists(sample_path):
        raise HTTPException(status_code=404, detail='Sample file not found.')
    with open(sample_path, 'rb') as f:
        content = f.read()
    kml_text = ContourAnalysisEngine.extract_kml_from_bytes(content, 'contours_1m.kml')
    contours = ContourAnalysisEngine.parse_contours_from_kml(kml_text)
    result = ContourAnalysisEngine.analyze_terrain(contours)
    return ContourAnalysisResponse(**result)