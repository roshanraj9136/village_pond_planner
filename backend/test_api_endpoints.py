import requests
import json
import os
import sys

BASE_URL = "http://127.0.0.1:8000"
KML_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_data", "contours_1m.kml")

def run_tests():
    print("Testing backend endpoints...")

    res = requests.get(f"{BASE_URL}/")
    assert res.status_code == 200
    print("GET / -> OK")

    res = requests.get(f"{BASE_URL}/api/sampleContour")
    assert res.status_code == 200
    sample_data = res.json()
    assert sample_data["status"] == "success"
    print("GET /api/sampleContour -> OK")

    with open(KML_PATH, "rb") as f:
        files = {"file": ("contours_1m.kml", f, "application/vnd.google-earth.kml+xml")}
        res = requests.post(f"{BASE_URL}/analyzeContour", files=files)
    assert res.status_code == 200
    print("POST /analyzeContour -> OK")

    with open(KML_PATH, "rb") as f:
        files = {"file": ("contours_1m.kml", f, "application/vnd.google-earth.kml+xml")}
        res = requests.post(f"{BASE_URL}/findCatchment", files=files)
    assert res.status_code == 200
    print("POST /findCatchment -> OK")

    with open(KML_PATH, "rb") as f:
        files = {"file": ("contours_1m.kml", f, "application/vnd.google-earth.kml+xml")}
        res = requests.post(f"{BASE_URL}/api/analyzeContour", files=files)
    assert res.status_code == 200
    print("POST /api/analyzeContour -> OK")

    files = {"file": ("test.txt", b"dummy content", "text/plain")}
    res = requests.post(f"{BASE_URL}/analyzeContour", files=files)
    assert res.status_code == 400
    print("POST /analyzeContour (invalid file) -> 400 OK")

    print("All tests passed successfully.")

if __name__ == "__main__":
    run_tests()
