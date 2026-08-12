import cv2
import numpy as np
import requests
import math

print("\n=======================================================")
print("  AI POND PLANNER - OpenCV TERRAIN ANALYZER SCRIPT")
print("=======================================================\n")

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

lat, lng = 19.3833, 74.3167 # Ralegan Siddhi
zoom = 16

print(f"STEP 1: Fetching Satellite Tile for Lat: {lat}, Lng: {lng}...")
x, y = deg2num(lat, lng, zoom)
tile_url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}"

headers = {"User-Agent": "PondSight-University-Project/1.0"}
resp = requests.get(tile_url, headers=headers)

if resp.status_code == 200:
    print("  -> Tile downloaded successfully.")
    
    print("\nSTEP 2: Processing image with OpenCV (HSV Color Space)...")
    nparr = np.frombuffer(resp.content, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is not None:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        
        # Detect Green Vegetation
        lower_green = np.array([30, 40, 40])
        upper_green = np.array([85, 255, 255])
        mask_green = cv2.inRange(hsv, lower_green, upper_green)
        veg_pixels = cv2.countNonZero(mask_green)
        
        # Detect Surface Water (Blue/Dark)
        lower_water = np.array([90, 40, 40])
        upper_water = np.array([130, 255, 255])
        mask_water = cv2.inRange(hsv, lower_water, upper_water)
        water_pixels = cv2.countNonZero(mask_water)
        
        total_pixels = img.shape[0] * img.shape[1]
        veg_percent = (veg_pixels / total_pixels) * 100
        water_percent = (water_pixels / total_pixels) * 100
        
        print("\n================== OpenCV RESULTS ==================")
        print(f"  Vegetation Cover Detected: {veg_percent:.2f}%")
        print(f"  Existing Surface Water:    {water_percent:.2f}%")
        print("====================================================\n")
        print("CONCLUSION: High vegetation prevents soil erosion. Surface water indicates a natural depression.")
else:
    print(f"Failed to fetch satellite tile. Status Code: {resp.status_code}")
