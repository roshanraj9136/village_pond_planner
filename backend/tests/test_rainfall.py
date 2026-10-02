"""Rainfall cache behaviour when the archive is unreachable (no network used)."""
import json

from rainfall import RainfallService, water_budget


def _series(mm_per_day: float) -> dict:
    times = [f"{y}-{m:02d}-{d:02d}" for y in range(2015, 2025) for m in range(1, 13) for d in range(1, 29)]
    return {"time": times, "precip": [mm_per_day] * len(times), "grid_lat": 21.2, "grid_lng": 81.3}


def _offline(svc):
    svc._download = lambda lat, lng: None


def test_unreachable_uses_nearest_saved_cell(tmp_path):
    (tmp_path / "+21.2_+81.3.json").write_text(json.dumps(_series(3.0)))
    svc = RainfallService(tmp_path)
    _offline(svc)
    s = svc.series(21.31, 81.29)  # cell +21.3_+81.3, about 11 km north of the saved one
    assert s is not None and 10 < s["nearby_km"] < 13
    budget = water_budget(s, 80)
    assert budget["is_fallback"] is False
    assert budget["nearby_km"] == s["nearby_km"]
    assert "km away" in budget["rainfall_source"]
    # the stand-in must not be remembered as the cell's own record
    assert not (tmp_path / "+21.3_+81.3.json").exists()


def test_unreachable_and_nothing_nearby_uses_regional_normal(tmp_path):
    (tmp_path / "+21.2_+81.3.json").write_text(json.dumps(_series(3.0)))
    svc = RainfallService(tmp_path)
    _offline(svc)
    s = svc.series(22.5, 82.5)  # ~190 km away
    assert s is None
    assert water_budget(s, 80)["is_fallback"] is True


def test_exact_cell_wins_over_neighbours(tmp_path):
    (tmp_path / "+21.2_+81.3.json").write_text(json.dumps(_series(3.0)))
    (tmp_path / "+21.3_+81.3.json").write_text(json.dumps(_series(5.0)))
    svc = RainfallService(tmp_path)
    _offline(svc)
    s = svc.series(21.3, 81.3)
    assert "nearby_km" not in s and s["precip"][0] == 5.0

