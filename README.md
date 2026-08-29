# Village Pond Planner

A web application to help plan village pond locations using contour maps, terrain slopes, and rainfall data.

## Tech Stack
- **Backend:** FastAPI (Python)
- **Frontend:** React (Vite) + Leaflet for map rendering
- **Styling:** CSS

## How to Run

### 1. Backend Setup
```bash
cd backend
# Windows:
.\venv_win\Scripts\activate
# Linux/macOS:
source venv/bin/activate

uvicorn main:app --reload --port 8000
```
Backend runs at `http://localhost:8000`. API docs can be viewed at `http://localhost:8000/docs`.

### 2. Frontend Setup
In a new terminal:
```bash
cd frontend
npm install
npm run dev
```
Frontend runs at `http://localhost:5173`.

## Catchment Analysis API

### Endpoints
- `POST /analyzeContour` or `POST /findCatchment`: Upload a `.kml` or `.kmz` contour map file to get the terrain slope, recommended pond site, and catchment boundary polygon.
- `GET /api/sampleContour`: Runs analysis on the default sample contour map.

### Example cURL Request
```bash
curl -X POST "http://localhost:8000/analyzeContour" -F "file=@sample_data/contours_1m.kml"
```

### Running Tests
```bash
python test_contour_analyzer.py
```
