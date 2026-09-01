# Village Pond Planner (Catchment & Hydrological Analysis Engine)

An automated geospatial analysis engine and web platform designed to analyze terrain contour maps (KML/KMZ), reconstruct high-resolution continuous Digital Elevation Models (DEM), calculate hydrological drainage networks via Directed Acyclic Graph (DAG) topological flow accumulation, identify optimal pond sites, and delineate catchment boundaries with volumetric runoff estimations.

---

## 1. System Architecture & Hydrological Pipeline

```
[KML / KMZ Vector Input]
         │
         ▼
[1. Resilient KML Parser] ──────> Schema-agnostic Placemark 3D coordinate extraction
         │
         ▼
[2. Equirectangular Transform] ──> Trigonometric geographic-to-metric distance projection
         │
         ▼
[3. DEM Surface Generator] ─────> Bivariate cubic spline interpolation with Gaussian smoothing
         │
         ▼
[4. D8 Flow Routing Engine] ────> 8-connected gradient descent DAG vector field
         │
         ▼
[5. Topological Flow Acc.] ─────> Kahn's algorithm accumulation for global sink / pond placement
         │
         ▼
[6. Reverse DFS Delineation] ───> Upstream boundary tagging and RFC 7946 GeoJSON Polygon export
```

---

## 2. Tech Stack

* **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic
* **Geospatial & Scientific Computing:** NumPy, SciPy (`griddata`, `gaussian_filter`), Shapely (`unary_union`, `Polygon`), OpenCV
* **Persistence Layer:** SQLite3 (`ponds.db`)
* **Frontend:** React 19, Vite, Leaflet, React-Leaflet, Modern CSS

---

## 3. API Specification

### 3.1 Primary Endpoints

| Endpoint | Method | Parameter | Description |
| :--- | :--- | :--- | :--- |
| `/analyzeContour` | `POST` | `contour_map` (File) | Upload `.kml` or `.kmz` contour map for complete catchment and pond analysis |
| `/findCatchment` | `POST` | `contour_map` (File) | Aliased route for contour map catchment analysis |
| `/api/sampleContour` | `GET` | None | Executes analysis against the bundled `contours_1m.kml` sample map |
| `/api/reports` | `GET` | None | Retrieves historical site assessment reports from database |
| `/api/reports` | `POST` | JSON Payload | Persists an analyzed site report to SQLite |
| `/docs` | `GET` | None | Interactive OpenAPI / Swagger UI |

### 3.2 cURL Example

```bash
curl -X POST "http://localhost:3297/analyzeContour" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "contour_map=@sample_data/contours_1m.kml"
```

### 3.3 Sample JSON Response

```json
{
  "status": "success",
  "message": "Terrain analyzed using D8 flow accumulation.",
  "contour_metadata": {
    "total_contour_lines": 92,
    "elevation_min_meters": 266.7,
    "elevation_max_meters": 295.68,
    "elevation_range_meters": 28.98,
    "contour_interval_meters": 1.0,
    "bounding_box": {
      "min_lat": 21.2621049,
      "max_lat": 21.2635806,
      "min_lng": 81.2814045,
      "max_lng": 81.3126469
    }
  },
  "terrain_metrics": {
    "average_slope_percent": 9.24,
    "terrain_classification": "Hilly",
    "runoff_coefficient": 0.25,
    "annual_rainfall_mm": 850.0
  },
  "pond_location": {
    "latitude": 21.262935,
    "longitude": 81.286172,
    "elevation_meters": 272.85,
    "site_suitability": "Maximum Flow Accumulation Point"
  },
  "catchment_analysis": {
    "catchment_area_sq_meters": 69653.39,
    "catchment_area_hectares": 6.97,
    "estimated_runoff_volume_cubic_meters": 14801.35,
    "recommended_pond_surface_area_sq_meters": 6965.34,
    "recommended_pond_depth_meters": 3.06,
    "estimated_storage_capacity_cubic_meters": 21313.94,
    "boundary_geojson": {
      "type": "Polygon",
      "coordinates": [[[81.2862978, 21.2621827], "..."]]
    }
  }
}
```

---

## 4. Local Installation & Development

### 4.1 Prerequisites
* Python $\ge 3.10$
* Node.js $\ge 18$ and npm

### 4.2 Backend Setup
```bash
cd backend
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\activate

pip install -r requirements.txt
uvicorn main:app --reload --port 3297
```
Backend will be live at `http://localhost:3297` (Swagger docs at `/docs`).

### 4.3 Frontend Setup
```bash
cd frontend
npm install
npm run dev
```
Frontend development server will be live at `http://localhost:5173`.

---

## 5. Deployment & Production Operations

On remote Linux servers / container hosts, services can run persistently using `tmux`:

```bash
# Start Backend in tmux session
tmux new-session -d -s pond_planner 'cd ~/village_pond_planner/backend && source venv2/bin/activate && uvicorn main:app --host 0.0.0.0 --port 3297'

# Build and Serve Static Frontend
cd ~/village_pond_planner/frontend
npx vite build
tmux new-session -d -s frontend 'cd dist && python3 -m http.server 4297 --bind 0.0.0.0'
```

---

## 6. Generalization & Zero-Hardcoding Principles

* **Dynamic Extents:** Spatial bounds are derived dynamically via `min()` and `max()` across input vector points with zero hardcoded geographic constraints.
* **Scale Invariance:** Equirectangular local scaling dynamically accounts for meridian convergence at arbitrary latitudes ($\cos(\theta)$).
* **Contour Interval Agnostic:** Handles non-uniform interval distributions (0.5m, 1m, 2m, 5m, 10m) using continuous surface splines.
* **Standard Compliant Outputs:** Generates valid RFC 7946 GeoJSON polygons ready for any GIS rendering pipeline.
