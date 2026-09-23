"""Engine tests on synthetic terrain where the right answer is known."""
import math

import numpy as np
import pytest

import hydrology as hy
import planner
from geo import M_PER_DEG, parse_polygon, rasterize_ring, ring_area_m2, GeometryError


def make_grid(z, lat0=21.25, lng0=81.30, cell_m=30.0):
    rows, cols = z.shape
    dlat = cell_m / M_PER_DEG
    dlng = cell_m / (M_PER_DEG * math.cos(math.radians(lat0)))
    lats = lat0 - np.arange(rows) * dlat
    lngs = lng0 + np.arange(cols) * dlng
    return hy.Grid(z=z.astype(float), lats=lats, lngs=lngs, source={"type": "test"})


def square(grid, r0, r1, c0, c1):
    """Polygon whose corners sit between cell centres, enclosing rows r0..r1, cols c0..c1."""
    la = lambda r: float(grid.lats[r] + grid.dlat / 2)  # noqa: E731
    lo = lambda c: float(grid.lngs[c] - grid.dlng / 2)  # noqa: E731
    top, bottom = la(r0), la(r1) - grid.dlat
    left, right = lo(c0), lo(c1) + grid.dlng
    return [(left, bottom), (right, bottom), (right, top), (left, top)]


def valley(rows=40, cols=41):
    r, c = np.mgrid[0:rows, 0:cols]
    return 300.0 - 0.5 * r + 0.8 * np.abs(c - cols // 2)  # slopes south, V-shaped around the centre column


def test_fill_removes_every_pit():
    rng = np.random.default_rng(1)
    z = valley() + rng.normal(0, 0.4, (40, 41))
    z[20, 20] -= 5  # a deep pit
    valid = np.isfinite(z)
    filled = hy.priority_flood_fill(z, valid)
    assert np.all(filled >= z - 1e-9)
    grid = make_grid(z)
    recv = hy.d8_receivers(filled, valid, grid.dx_m, grid.dy_m)
    interior = valid & ~hy.boundary_cells(valid)
    assert np.all(recv.reshape(z.shape)[interior] >= 0), "every interior cell must have a downhill neighbour"


def test_flow_converges_on_valley_axis():
    grid = make_grid(valley())
    fm = planner.flow_model(grid)
    acc = fm.acc.reshape(grid.shape)
    # maximum accumulation is on the valley floor at the bottom row
    r, c = np.unravel_index(np.argmax(acc), acc.shape)
    assert c == 20 and r == grid.shape[0] - 1
    # every cell drains somewhere: total area equals accumulated area at outlets
    total = float(np.broadcast_to(grid.cell_area, grid.shape).sum())
    outlets = fm.recv == -1
    assert math.isclose(float(fm.acc[outlets].sum()), total, rel_tol=1e-9)


def test_parcel_restricts_site_and_catchment_is_upstream():
    grid = make_grid(valley())
    ring = square(grid, 10, 19, 15, 25)  # a parcel in the middle of the valley
    hydro = planner.run_hydrology(grid, ring)
    r, c = divmod(hydro.outlet, grid.shape[1])
    assert hydro.parcel[r, c]
    assert c == 20 and r == 19, "pond goes to the lowest valley-floor cell inside the parcel"
    catch = hydro.catchment
    assert catch[:r + 1].sum() == catch.sum(), "nothing downstream of the site belongs to its catchment"
    assert catch[0, 20], "the catchment reaches the head of the valley"


def test_ridge_separates_catchments():
    rows, cols = 30, 41
    r, c = np.mgrid[0:rows, 0:cols]
    # two parallel valleys (axes at columns 10 and 30) draining north, ridge crest at column 20
    z = 200 + 0.4 * r + np.minimum(np.abs(c - 10), np.abs(c - 30))
    grid = make_grid(z)
    ring = square(grid, 0, 5, 25, 35)  # parcel on the east valley
    hydro = planner.run_hydrology(grid, ring)
    _, oc = divmod(hydro.outlet, cols)
    assert oc == 30
    assert hydro.catchment[:, 25:36].any()
    assert not hydro.catchment[:, :20].any(), "west of the ridge cannot drain to an east-side pond"


def test_polygon_parsing_and_area():
    poly = {"type": "Polygon", "coordinates": [[[81.3, 21.2], [81.31, 21.2], [81.31, 21.21], [81.3, 21.21], [81.3, 21.2]]]}
    ring = parse_polygon(poly)
    assert len(ring) == 4
    expected = (0.01 * M_PER_DEG * math.cos(math.radians(21.205))) * (0.01 * M_PER_DEG)
    assert math.isclose(ring_area_m2(ring), expected, rel_tol=2e-3)
    with pytest.raises(GeometryError):
        parse_polygon({"type": "Point", "coordinates": [81.3, 21.2]})
    with pytest.raises(GeometryError):
        parse_polygon({"type": "Polygon", "coordinates": [[[81.3, 21.2], [81.31, 21.2]]]})


def test_rasterize_concave_polygon():
    lats = np.linspace(1, 0, 11)
    lngs = np.linspace(0, 1, 11)
    ring = [(0, 0), (1, 0), (1, 1), (0.5, 0.5), (0, 1)]  # a "V" notch from the top
    m = rasterize_ring(ring, lats, lngs)
    assert m[9, 5] and not m[1, 5]  # below the notch inside, the notch itself outside


def test_pond_sizing_monotone_and_clamped():
    p = planner.Params()
    small = planner.size_pond(500, 10_000, p)
    big = planner.size_pond(20_000, 10_000, p)
    assert small < big <= 10_000
    vol, _ = planner.pond_volume(big, p.pond_depth_m, p.side_slope)
    assert vol <= 20_000 * 1.01
    assert planner.size_pond(1e9, 5_000, p) == 5_000
