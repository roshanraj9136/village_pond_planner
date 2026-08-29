import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))
from contour_analyzer import extract_kml_from_bytes, parse_contours_from_kml, analyze_terrain

if len(sys.argv) > 1:
    kml_file = sys.argv[1]
else:
    kml_file = os.path.join(os.path.dirname(__file__), 'sample_data', 'contours_1m.kml')

print(f"Reading {kml_file}")
with open(kml_file, 'rb') as f:
    kml_bytes = f.read()

kml_text = extract_kml_from_bytes(kml_bytes, os.path.basename(kml_file))
contours = parse_contours_from_kml(kml_text)
print(f"Found {len(contours)} contours")

result = analyze_terrain(contours)
print("Analysis output:")
print(json.dumps(result, indent=2))
