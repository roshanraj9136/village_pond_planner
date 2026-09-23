"""API tests against the bundled sample contour map (no network needed: rainfall falls back)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "contours_1m.kml"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    main.rain.cache_dir = tmp_path_factory.mktemp("rain")
    return TestClient(main.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "X-Worker" in r.headers


def test_phase2_contract(client):
    with SAMPLE.open("rb") as fh:
        r = client.post("/analyzeContour", files={"contour_map": ("contours_1m.kml", fh, "application/xml")})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("contour_metadata", "terrain_metrics", "pond_location", "catchment_analysis"):
        assert key in body
    meta = body["contour_metadata"]
    assert meta["total_contour_lines"] == 92
    assert meta["contour_interval_meters"] == 1.0
    ca = body["catchment_analysis"]
    assert ca["catchment_area_sq_meters"] > 0
    assert ca["estimated_runoff_volume_cubic_meters"] > 0
    assert ca["boundary_geojson"]["type"] == "MultiPolygon"
    bbox = meta["bounding_box"]
    pl = body["pond_location"]
    assert bbox["min_lat"] <= pl["latitude"] <= bbox["max_lat"]
    assert bbox["min_lng"] <= pl["longitude"] <= bbox["max_lng"]


def test_find_catchment_alias_and_parcel(client):
    parcel = {"type": "Polygon", "coordinates": [[[81.2900, 21.2622], [81.2960, 21.2622], [81.2960, 21.2635],
                                                  [81.2900, 21.2635], [81.2900, 21.2622]]]}
    with SAMPLE.open("rb") as fh:
        r = client.post("/findCatchment", files={"contour_map": ("contours_1m.kml", fh)},
                        data={"area": json.dumps(parcel)})
    assert r.status_code == 200, r.text
    pond = r.json()["analysis"]["pond"]
    assert 81.2900 <= pond["lng"] <= 81.2960


def test_rejects_wrong_extension(client):
    r = client.post("/analyzeContour", files={"contour_map": ("map.txt", b"hello")})
    assert r.status_code == 400


def test_rejects_xml_entities(client):
    evil = b"""<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <kml><Placemark><name>&x;</name><LineString><coordinates>81,21 81.1,21.1</coordinates></LineString></Placemark></kml>"""
    r = client.post("/analyzeContour", files={"contour_map": ("evil.kml", evil)})
    assert r.status_code == 422


def test_rejects_kml_without_elevations(client):
    kml = b"<kml><Placemark><name>road</name><LineString><coordinates>81,21 81.1,21.1</coordinates></LineString></Placemark></kml>"
    r = client.post("/analyzeContour", files={"contour_map": ("road.kml", kml)})
    assert r.status_code == 422


def test_parcel_validation(client):
    tiny = {"type": "Polygon", "coordinates": [[[81.3, 21.2], [81.30001, 21.2], [81.30001, 21.20001], [81.3, 21.2]]]}
    r = client.post("/api/analyze", json={"area": tiny})
    assert r.status_code == 422
    bow = {"type": "Polygon", "coordinates": [[[81.3, 21.2], [81.31, 21.21], [81.31, 21.2], [81.3, 21.21], [81.3, 21.2]]]}
    r = client.post("/api/analyze", json={"area": bow})
    assert r.status_code == 422
