import requests
import math

print("\n=======================================================")
print("  AI POND PLANNER - INDEPENDENT VERIFICATION SCRIPT")
print("=======================================================\n")

# We will test Ralegan Siddhi, Ahmednagar, Maharashtra. 
# This is India's most famous model village for watershed management
# transformed by Padmabhushan Anna Hazare.
lat, lng = 19.3833, 74.3167
print(f"Targeting Real Coordinates: Ralegan Siddhi (Lat: {lat}, Lng: {lng})\n")

print("STEP 1: Fetching LIVE Rainfall Data from Open-Meteo...")
url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lng}&start_date=2023-01-01&end_date=2023-12-31&daily=precipitation_sum&timezone=auto"
res = requests.get(url).json()
rain = sum([p for p in res['daily']['precipitation_sum'] if p is not None])
print(f"  -> Verified 2023 Annual Rainfall: {rain:.2f} mm")

print("\nSTEP 2: Fetching LIVE 3D Elevation Grid from Open-Meteo...")
offset = 0.00045 # Approx 50 meters
lats = f"{lat},{lat+offset},{lat-offset},{lat},{lat}"
lngs = f"{lng},{lng},{lng},{lng+offset},{lng-offset}"
elev_url = f"https://api.open-meteo.com/v1/elevation?latitude={lats}&longitude={lngs}"
elevs = requests.get(elev_url).json()['elevation']
print(f"  -> Center Elevation: {elevs[0]}m")
print(f"  -> Grid Elevations (N, S, E, W): {elevs[1]}m, {elevs[2]}m, {elevs[3]}m, {elevs[4]}m")

dz_dx = (elevs[3] - elevs[4]) / 100.0
dz_dy = (elevs[1] - elevs[2]) / 100.0
slope = math.sqrt(dz_dx**2 + dz_dy**2) * 100.0
print(f"  -> True Calculated Topological Slope: {slope:.2f}%")

print("\nSTEP 3: Determining HLD Constants (Rational Method)...")
if slope < 2.0:
    c, a = 0.20, 50.0
    print("  -> Slope < 2.0% (Flat Basin). Setting C=0.20, Area=50 Hectares")
elif slope < 7.0:
    c, a = 0.35, 25.0
    print("  -> Slope < 7.0% (Rolling Terrain). Setting C=0.35, Area=25 Hectares")
elif slope < 15.0:
    c, a = 0.50, 10.0
    print("  -> Slope < 15.0% (Hilly Terrain). Setting C=0.50, Area=10 Hectares")
else:
    c, a = 0.65, 5.0
    print("  -> Slope >= 15.0% (Steep Terrain). Setting C=0.65, Area=5 Hectares")

a_sqm = a * 10000.0

print("\nSTEP 4: Calculating Volumetric Runoff (Q = C * I * A)...")
i_meters = rain / 1000.0
q = c * i_meters * a_sqm
print(f"  -> Q = {c} * {i_meters:.4f} meters * {a_sqm} sq_meters")
print(f"  -> Total Runoff Volume: {q:,.2f} cubic meters")

print("\nSTEP 5: Calculating Optimal Pond Depth...")
pond_area = a_sqm * 0.10
active = q / pond_area
depth = 2.0 + active
print(f"  -> Active Depth Required ({q:,.2f} m3 / {pond_area:,.2f} m2) = {active:.2f} meters")
print(f"  -> Adding 2.0 meters (for Evaporation/Seepage) = {depth:.2f} meters total depth.")
print(f"  -> RECOMMENDED POND DEPTH: {round(max(2.0, min(5.5, depth)), 2)} meters")
print("\n=======================================================\n")
