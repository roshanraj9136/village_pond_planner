from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import math

app = FastAPI(title="AI-based Village Pond Planning System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    
class TerrainResponse(BaseModel):
    elevation_meters: float
    catchment_area_sq_meters: float
    estimated_runoff_volume_cubic_meters: float
    recommended_pond_depth_meters: float
    estimated_storage_capacity: float
    runoff_coefficient: float
    annual_rainfall_mm: float

@app.get("/")
def read_root():
    return {"status": "Backend is running flawlessly!"}

@app.post("/api/analyze/location", response_model=LocationResponse)
def analyze_location(coords: Coordinates):
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={coords.lat}&lon={coords.lng}&zoom=18&addressdetails=1"
    headers = {"User-Agent": "PondSight-University-Project/1.0"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            display_name = data.get("display_name", "Unknown Area")
            cls = data.get("class", "")
            ltype = data.get("type", "")
            address = data.get("address", {})
            country_code = address.get("country_code", "")
            
            is_suitable = True
            warning = ""
            
            if country_code != "in":
                is_suitable = False
                warning = "CRITICAL: Jurisdictional boundary restriction. This system is exclusively authorized for terrain analysis within India."
            else:
                unsuitable = ["building", "residential", "commercial", "industrial", "aeroway", "highway", "railway", "military"]
                if cls in unsuitable or ltype in unsuitable:
                    is_suitable = False
                    warning = f"CRITICAL: Selected location overlaps with '{ltype or cls}'. Pond construction is structurally and legally prohibited."
            
            return LocationResponse(
                display_name=display_name,
                land_type=(ltype or cls or "Open Land").title(),
                is_suitable=is_suitable,
                warning_message=warning
            )
    except Exception as e:
        pass
        
    return LocationResponse(
        display_name=f"Lat: {coords.lat}, Lng: {coords.lng}",
        land_type="Unknown",
        is_suitable=True,
        warning_message=""
    )

@app.post("/api/analyze/rainfall", response_model=RainfallResponse)
def analyze_rainfall(coords: Coordinates):
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude={coords.lat}&longitude={coords.lng}&start_date=2023-01-01&end_date=2023-12-31&daily=precipitation_sum&timezone=auto"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        daily = data.get("daily", {}).get("precipitation_sum", [])
        valid = [p for p in daily if p is not None]
        total_annual = sum(valid)
        return RainfallResponse(annual_rainfall_mm=round(total_annual, 2), average_monthly_mm=round(total_annual / 12, 2))
    except:
        return RainfallResponse(annual_rainfall_mm=850.0, average_monthly_mm=70.8)

@app.post("/api/analyze/terrain", response_model=TerrainResponse)
def analyze_terrain(coords: Coordinates):
    offset = 0.00045 
    lats = f"{coords.lat},{coords.lat+offset},{coords.lat-offset},{coords.lat},{coords.lat}"
    lngs = f"{coords.lng},{coords.lng},{coords.lng},{coords.lng+offset},{coords.lng-offset}"
    
    elevation_url = f"https://api.open-meteo.com/v1/elevation?latitude={lats}&longitude={lngs}"
    try:
        elev_response = requests.get(elevation_url, timeout=10)
        elevations = elev_response.json().get("elevation", [200.0, 200.0, 200.0, 200.0, 200.0])
    except Exception:
        elevations = [200.0, 200.0, 200.0, 200.0, 200.0]
        
    center_elev, north_elev, south_elev, east_elev, west_elev = elevations[0], elevations[1], elevations[2], elevations[3], elevations[4]

    dz_dx = (east_elev - west_elev) / 100.0
    dz_dy = (north_elev - south_elev) / 100.0
    slope_percent = math.sqrt(dz_dx**2 + dz_dy**2) * 100.0

    rain_url = f"https://archive-api.open-meteo.com/v1/archive?latitude={coords.lat}&longitude={coords.lng}&start_date=2023-01-01&end_date=2023-12-31&daily=precipitation_sum&timezone=auto"
    try:
        rain_response = requests.get(rain_url, timeout=10)
        rain_data = rain_response.json().get("daily", {}).get("precipitation_sum", [])
        annual_rainfall_mm = sum([p for p in rain_data if p is not None])
    except Exception:
        annual_rainfall_mm = 850.0
        
    rainfall_meters = annual_rainfall_mm / 1000.0

    if slope_percent < 2.0:
        catchment_area_sqm = 500000.0
        runoff_coefficient = 0.20
    elif slope_percent < 7.0:
        catchment_area_sqm = 250000.0
        runoff_coefficient = 0.35
    elif slope_percent < 15.0:
        catchment_area_sqm = 100000.0
        runoff_coefficient = 0.50
    else:
        catchment_area_sqm = 50000.0
        runoff_coefficient = 0.65
        
    runoff_volume = runoff_coefficient * rainfall_meters * catchment_area_sqm
    
    pond_surface_area = catchment_area_sqm * 0.10
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
        annual_rainfall_mm=round(annual_rainfall_mm, 2)
    )
