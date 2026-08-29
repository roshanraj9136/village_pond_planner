import math
import re
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
from shapely.geometry import Point, mapping
from shapely.ops import unary_union
import requests

def extract_kml_from_bytes(file_bytes, filename):
    if filename.lower().endswith('.kmz') or zipfile.is_zipfile(zipfile.io.BytesIO(file_bytes)):
        with zipfile.ZipFile(zipfile.io.BytesIO(file_bytes), 'r') as z:
            kml_files = [f for f in z.namelist() if f.lower().endswith('.kml')]
            if not kml_files:
                raise ValueError('No .kml files found in KMZ.')
            with z.open(kml_files[0]) as kml_entry:
                return kml_entry.decode('utf-8', errors='ignore')
    return file_bytes.decode('utf-8', errors='ignore')

def parse_contours_from_kml(kml_text):
    kml_clean = re.sub(r'<\?xml[^>]*\?>', '', kml_text)
    kml_clean = re.sub(r'\s+xmlns(:\w+)?="[^"]*"', '', kml_clean)
    kml_clean = re.sub(r'\s+[\w\-]+:[\w\-]+="[^"]*"', '', kml_clean)
    kml_clean = re.sub(r'<(/)?[\w\-]+:([\w\-]+)', r'<\1\2', kml_clean)

    root = ET.fromstring(f'<root>{kml_clean}</root>')
    contours = []

    for placemark in root.iter('Placemark'):
        elevation = None

        name_elem = placemark.find('name')
        if name_elem is not None and name_elem.text:
            match = re.search(r'[-+]?\d*\.?\d+', name_elem.text.strip())
            if match:
                try: elevation = float(match.group())
                except ValueError: pass

        if elevation is None:
            ext = placemark.find('ExtendedData')
            if ext is not None:
                for sd in ext.iter('SimpleData'):
                    if 'ELEV' in str(sd.attrib.get('name')).upper() or 'CONTOUR' in str(sd.attrib.get('name')).upper():
                        if sd.text:
                            try: elevation = float(sd.text.strip())
                            except ValueError: pass

        if elevation is None:
            desc = placemark.find('description')
            if desc is not None and desc.text:
                match = re.search(r'(?:elev|elevation|contour)[\s:=]+([-+]?\d*\.?\d+)', desc.text, re.IGNORECASE)
                if match:
                    try: elevation = float(match.group(1))
                    except ValueError: pass

        coords_elements = placemark.iter('coordinates')
        for c_elem in coords_elements:
            if not c_elem.text: continue
            raw = c_elem.text.strip().split()
            pts = []
            for p in raw:
                parts = p.split(',')
                if len(parts) >= 2:
                    try:
                        lng, lat = float(parts[0]), float(parts[1])
                        z = float(parts[2]) if len(parts) >= 3 else None
                        if elevation is None and z is not None and z != 0:
                            elevation = z
                        pts.append((lng, lat))
                    except ValueError: pass

            if pts and elevation is not None:
                contours.append({'elevation': elevation, 'coordinates': pts})

    if not contours:
        raise ValueError("Could not extract any contours with elevation data.")
    return contours

def get_d8_flow_direction(dem):
    rows, cols = dem.shape
    flow_dir = np.zeros((rows, cols), dtype=np.int8)
    padded = np.pad(dem, 1, mode='constant', constant_values=np.inf)

    dr = [-1, -1, -1,  0, 0,  1, 1, 1]
    dc = [-1,  0,  1, -1, 1, -1, 0, 1]

    for r in range(rows):
        for c in range(cols):
            center_val = padded[r+1, c+1]
            min_val = center_val
            best_dir = -1

            for i in range(8):
                nr = r + 1 + dr[i]
                nc = c + 1 + dc[i]
                if padded[nr, nc] < min_val:
                    min_val = padded[nr, nc]
                    best_dir = i

            flow_dir[r, c] = best_dir
    return flow_dir, dr, dc

def get_flow_accumulation(flow_dir, dr, dc):
    rows, cols = flow_dir.shape
    accumulation = np.zeros((rows, cols), dtype=np.int32)

    in_degree = np.zeros((rows, cols), dtype=np.int32)
    for r in range(rows):
        for c in range(cols):
            d = flow_dir[r, c]
            if d != -1:
                nr, nc = r + dr[d], c + dc[d]
                if 0 <= nr < rows and 0 <= nc < cols:
                    in_degree[nr, nc] += 1

    queue = []
    for r in range(rows):
        for c in range(cols):
            if in_degree[r, c] == 0:
                queue.append((r, c))

    while queue:
        r, c = queue.pop(0)
        d = flow_dir[r, c]
        if d != -1:
            nr, nc = r + dr[d], c + dc[d]
            if 0 <= nr < rows and 0 <= nc < cols:
                accumulation[nr, nc] += (accumulation[r, c] + 1)
                in_degree[nr, nc] -= 1
                if in_degree[nr, nc] == 0:
                    queue.append((nr, nc))

    return accumulation

def analyze_terrain(contours, grid_size=150):
    pts, elevs = [], []
    for c in contours:
        for lng, lat in c['coordinates']:
            pts.append((lng, lat))
            elevs.append(c['elevation'])

    pts = np.array(pts)
    elevs = np.array(elevs)

    min_lng, min_lat = np.min(pts, axis=0)
    max_lng, max_lat = np.max(pts, axis=0)

    meters_per_lat = 111320.0
    meters_per_lng = 111320.0 * math.cos(math.radians((min_lat + max_lat) / 2))

    dx_m = (max_lng - min_lng) * meters_per_lng / grid_size
    dy_m = (max_lat - min_lat) * meters_per_lat / grid_size
    cell_area = dx_m * dy_m

    grid_x = np.linspace(min_lng, max_lng, grid_size)
    grid_y = np.linspace(min_lat, max_lat, grid_size)
    gx, gy = np.meshgrid(grid_x, grid_y)

    dem = griddata(pts, elevs, (gx, gy), method='cubic')
    dem_nearest = griddata(pts, elevs, (gx, gy), method='nearest')
    dem[np.isnan(dem)] = dem_nearest[np.isnan(dem)]
    dem = gaussian_filter(dem, sigma=1.0)

    gy_grad, gx_grad = np.gradient(dem, dy_m, dx_m)
    slope_percent = np.sqrt(gx_grad**2 + gy_grad**2) * 100
    avg_slope = float(np.mean(slope_percent))

    flow_dir, dr, dc = get_d8_flow_direction(dem)
    flow_acc = get_flow_accumulation(flow_dir, dr, dc)

    inner_acc = flow_acc[5:-5, 5:-5]
    if inner_acc.size > 0:
        max_idx = np.unravel_index(np.argmax(inner_acc), inner_acc.shape)
        pour_r, pour_c = max_idx[0] + 5, max_idx[1] + 5
    else:
        max_idx = np.unravel_index(np.argmax(flow_acc), flow_acc.shape)
        pour_r, pour_c = max_idx

    pond_lng = float(grid_x[pour_c])
    pond_lat = float(grid_y[pour_r])
    pond_elev = float(dem[pour_r, pour_c])

    catchment = np.zeros_like(dem, dtype=bool)
    stack = [(pour_r, pour_c)]
    catchment[pour_r, pour_c] = True

    while stack:
        cr, cc = stack.pop()
        for i in range(8):
            nr, nc = cr - dr[i], cc - dc[i]
            if 0 <= nr < grid_size and 0 <= nc < grid_size:
                if not catchment[nr, nc] and flow_dir[nr, nc] == i:
                    catchment[nr, nc] = True
                    stack.append((nr, nc))

    catchment_area_sqm = float(np.sum(catchment) * cell_area)
    catchment_area_ha = catchment_area_sqm / 10000.0

    c_points = []
    for r in range(grid_size):
        for c in range(grid_size):
            if catchment[r, c]:
                c_points.append(Point(grid_x[c], grid_y[r]))

    boundary_geojson = None
    if c_points:
        buffered = [p.buffer(dx_m / meters_per_lng * 0.8) for p in c_points]
        union = unary_union(buffered).simplify(0.0001, preserve_topology=True)
        boundary_geojson = mapping(union)

    annual_rainfall_mm = 850.0
    try:
        url = f'https://archive-api.open-meteo.com/v1/archive?latitude={pond_lat}&longitude={pond_lng}&start_date=2023-01-01&end_date=2023-12-31&daily=precipitation_sum&timezone=auto'
        res = requests.get(url, timeout=3).json()
        daily = [p for p in res.get('daily', {}).get('precipitation_sum', []) if p is not None]
        if daily:
            annual_rainfall_mm = float(sum(daily))
    except:
        pass

    c_coeff = 0.2 if avg_slope < 2 else 0.35 if avg_slope < 7 else 0.5 if avg_slope < 15 else 0.65
    runoff_m3 = round(c_coeff * (annual_rainfall_mm / 1000.0) * catchment_area_sqm, 2)

    pond_area = min(catchment_area_sqm * 0.1, max(2500.0, runoff_m3 / 3.0))
    active_depth = runoff_m3 / max(1.0, pond_area)
    depth = round(max(2.5, min(5.5, 2.0 + active_depth * 0.5)), 2)

    elevation_min = round(float(np.min(dem)), 2)
    elevation_max = round(float(np.max(dem)), 2)
    elevation_range = round(elevation_max - elevation_min, 2)

    t_class = 'Flat' if avg_slope < 2 else 'Rolling' if avg_slope < 8 else 'Hilly'
    storage_capacity = round(pond_area * depth, 2)

    return {
        'status': 'success',
        'message': 'Terrain analyzed using D8 flow accumulation.',
        'contour_metadata': {
            'total_contour_lines': len(contours),
            'elevation_min_meters': elevation_min,
            'elevation_max_meters': elevation_max,
            'elevation_range_meters': elevation_range,
            'contour_interval_meters': 1.0,
            'bounding_box': {
                'min_lat': min_lat, 'max_lat': max_lat, 'min_lng': min_lng, 'max_lng': max_lng
            }
        },
        'terrain_metrics': {
            'average_slope_percent': round(avg_slope, 2),
            'terrain_classification': t_class,
            'runoff_coefficient': c_coeff,
            'annual_rainfall_mm': round(annual_rainfall_mm, 2)
        },
        'pond_location': {
            'latitude': round(pond_lat, 6),
            'longitude': round(pond_lng, 6),
            'elevation_meters': round(pond_elev, 2),
            'site_suitability': 'Maximum Flow Accumulation Point'
        },
        'catchment_analysis': {
            'catchment_area_sq_meters': round(catchment_area_sqm, 2),
            'catchment_area_hectares': round(catchment_area_ha, 2),
            'estimated_runoff_volume_cubic_meters': runoff_m3,
            'recommended_pond_surface_area_sq_meters': round(pond_area, 2),
            'recommended_pond_depth_meters': depth,
            'estimated_storage_capacity_cubic_meters': storage_capacity,
            'boundary_geojson': boundary_geojson
        }
    }

class ContourAnalysisEngine:
    @staticmethod
    def extract_kml_from_bytes(file_bytes, filename):
        return extract_kml_from_bytes(file_bytes, filename)
    @staticmethod
    def parse_contours_from_kml(text):
        return parse_contours_from_kml(text)
    @staticmethod
    def analyze_terrain(contours):
        return analyze_terrain(contours)