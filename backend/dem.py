"""Satellite elevation: Copernicus GLO-30 DEM (ESA / Airbus, 1 arc-second ~ 30 m).

Tiles are 1 x 1 degree Cloud-Optimised GeoTIFFs on the public AWS bucket
``copernicus-dem-30m``. A tile is downloaded once, decoded, and stored as a raw
``.npy`` array that the workers memory-map: extracting any analysis window is then
a slice of the page cache, with no decoding and almost no resident memory.

Pixel registration is *PixelIsPoint*: pixel (r, c) of the tile whose north-west
corner is (lat0 + 1, lon0) sits exactly at (lat0 + 1 - r/3600, lon0 + c/3600).

CLI (used by the worker in a subprocess so decode memory is released afterwards):
    python dem.py fetch 21 81          # download + convert tile N21 E081
    python dem.py convert path.tif     # convert an already downloaded tile
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np

from geo import GeometryError
from hydrology import Grid

ARCSEC = 1.0 / 3600.0
TILE_PX = 3600
BUCKET_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
SOURCE_INFO = {
    "type": "satellite_dem",
    "name": "Copernicus GLO-30 DEM",
    "provider": "ESA / Airbus (TanDEM-X mission), via AWS Open Data",
    "resolution": "1 arc-second (~30 m)",
    "url": "https://registry.opendata.aws/copernicus-dem/",
}


def tile_key(lat_floor: int, lng_floor: int) -> str:
    return f"{'N' if lat_floor >= 0 else 'S'}{abs(lat_floor):02d}{'E' if lng_floor >= 0 else 'W'}{abs(lng_floor):03d}"


def tile_remote_name(lat_floor: int, lng_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lng_floor >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lng_floor):03d}_00_DEM"


class TileUnavailable(Exception):
    pass


class DemStore:
    """Read-only mosaic of converted tiles with lazy download on first use."""

    def __init__(self, root: str | os.PathLike, max_open: int = 12, fetch_timeout_s: int = 240):
        self.root = Path(root)
        (self.root / "tiles").mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        self._open: OrderedDict[str, np.ndarray | None] = OrderedDict()
        self._lock = threading.Lock()
        self.max_open = max_open
        self.fetch_timeout_s = fetch_timeout_s

    # ---------------------------------------------------------------- tiles
    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.root / "tiles" / f"{key}.npy", self.root / "tiles" / f"{key}.none"

    def has_tile(self, lat_floor: int, lng_floor: int) -> bool:
        npy, none = self._paths(tile_key(lat_floor, lng_floor))
        return npy.exists() or none.exists()

    def coverage(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "tiles").glob("*.npy")):
            k = p.stem
            lat = int(k[1:3]) * (1 if k[0] == "N" else -1)
            lng = int(k[4:7]) * (1 if k[3] == "E" else -1)
            out.append({"tile": k, "south": lat, "west": lng, "north": lat + 1, "east": lng + 1})
        return out

    def _tile(self, lat_floor: int, lng_floor: int) -> np.ndarray | None:
        key = tile_key(lat_floor, lng_floor)
        with self._lock:
            if key in self._open:
                self._open.move_to_end(key)
                return self._open[key]
        npy, none = self._paths(key)
        if not npy.exists() and not none.exists():
            self._fetch_subprocess(lat_floor, lng_floor)
        arr = np.load(npy, mmap_mode="r") if npy.exists() else None
        with self._lock:
            self._open[key] = arr
            while len(self._open) > self.max_open:
                self._open.popitem(last=False)
        return arr

    def _fetch_subprocess(self, lat_floor: int, lng_floor: int) -> None:
        cmd = [sys.executable, str(Path(__file__).resolve()), "fetch", str(lat_floor), str(lng_floor),
               "--root", str(self.root)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.fetch_timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise TileUnavailable("Timed out downloading satellite elevation for this region; try again.") from exc
        if proc.returncode != 0:
            raise TileUnavailable("Could not download satellite elevation for this region: "
                                  + (proc.stderr.strip().splitlines() or ["unknown error"])[-1])

    # ---------------------------------------------------------------- windows
    def window(self, south: float, west: float, north: float, east: float, max_cells: int = 250_000) -> Grid:
        """Elevation grid covering the box, cell centres on the native 1" lattice."""
        if north - south <= 0 or east - west <= 0:
            raise GeometryError("Empty analysis window.")
        if not (-56 <= south and north <= 60):
            raise GeometryError("Satellite elevation is only available between 56 S and 60 N here.")
        r0 = math.floor((90.0 - north) * 3600.0)
        r1 = math.ceil((90.0 - south) * 3600.0)
        c0 = math.floor((west + 180.0) * 3600.0)
        c1 = math.ceil((east + 180.0) * 3600.0)
        rows, cols = r1 - r0 + 1, c1 - c0 + 1
        if rows * cols > max_cells:
            raise GeometryError("Selected area is too large for one analysis.")

        z = np.full((rows, cols), np.nan, dtype=np.float64)
        # global row rg -> tile lat_floor = 89 - rg // 3600 ; row in tile = rg % 3600
        for tr in range(r0 // TILE_PX, r1 // TILE_PX + 1):
            lat_floor = 89 - tr
            for tc in range(c0 // TILE_PX, c1 // TILE_PX + 1):
                lng_floor = tc - 180
                gr0, gr1 = max(r0, tr * TILE_PX), min(r1, tr * TILE_PX + TILE_PX - 1)
                gc0, gc1 = max(c0, tc * TILE_PX), min(c1, tc * TILE_PX + TILE_PX - 1)
                if gr0 > gr1 or gc0 > gc1:
                    continue
                arr = self._tile(lat_floor, lng_floor)
                if arr is None:
                    continue  # open sea: stays NaN (no data)
                if arr.shape[1] != TILE_PX:
                    raise GeometryError("This latitude uses a different tile width; not supported.")
                block = arr[gr0 - tr * TILE_PX: gr1 - tr * TILE_PX + 1, gc0 - tc * TILE_PX: gc1 - tc * TILE_PX + 1]
                z[gr0 - r0: gr1 - r0 + 1, gc0 - c0: gc1 - c0 + 1] = block
        lats = 90.0 - np.arange(r0, r1 + 1) * ARCSEC
        lngs = -180.0 + np.arange(c0, c1 + 1) * ARCSEC
        z[z < -500] = np.nan  # guard against fill values
        return Grid(z=z, lats=lats, lngs=lngs, source=dict(SOURCE_INFO))


# -------------------------------------------------------------------- CLI helpers

def _convert(tif_path: Path, npy_path: Path) -> None:
    import tifffile  # imported only in the helper process

    with tifffile.TiffFile(tif_path) as tf:
        page = tf.pages[0]
        geo = tf.geotiff_metadata or {}
        if int(geo.get("GTRasterTypeGeoKey", 2)) != 2:  # 2 = RasterPixelIsPoint (the lattice window() assumes)
            raise RuntimeError("Unexpected PixelIsArea registration")
        arr = page.asarray().astype(np.float32)
    tmp = npy_path.with_suffix(".tmp.npy")
    np.save(tmp, arr)
    os.replace(tmp, npy_path)


def _fetch(lat_floor: int, lng_floor: int, root: Path) -> None:
    key = tile_key(lat_floor, lng_floor)
    npy = root / "tiles" / f"{key}.npy"
    none = root / "tiles" / f"{key}.none"
    raw = root / "raw" / f"{tile_remote_name(lat_floor, lng_floor)}.tif"
    lock_path = root / "tiles" / f"{key}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one download per tile, even across workers on a host
        if npy.exists() or none.exists():
            _drop_raw(raw)
            return
        if not raw.exists():
            name = tile_remote_name(lat_floor, lng_floor)
            req = urllib.request.Request(BUCKET_URL.format(name=name), headers={"User-Agent": "JalDrishti/3.0"})
            part = raw.with_suffix(".part")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 404):  # no tile = ocean
                    none.write_text(time.strftime("%Y-%m-%d"))
                    return
                raise
            os.replace(part, raw)
        _convert(raw, npy)
        (root / "tiles" / f"{key}.json").write_text(json.dumps({"tile": key, "source": raw.name}))
    _drop_raw(raw)


def _drop_raw(raw: Path) -> None:
    """The compressed GeoTIFF is only needed until the .npy exists (set DEM_KEEP_RAW=1 to keep it)."""
    if raw.exists() and not os.environ.get("DEM_KEEP_RAW"):
        raw.unlink()


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("lat", type=int)
    f.add_argument("lng", type=int)
    f.add_argument("--root", default=os.environ.get("DEM_ROOT", str(Path.home() / "pond-data" / "dem")))
    c = sub.add_parser("convert-all", help="convert every raw/*.tif that has no .npy yet")
    c.add_argument("--root", default=os.environ.get("DEM_ROOT", str(Path.home() / "pond-data" / "dem")))
    r = sub.add_parser("region", help="prefetch every tile in a lat/lng box, e.g. region 20 22 80 82")
    r.add_argument("lat_min", type=int)
    r.add_argument("lat_max", type=int)
    r.add_argument("lng_min", type=int)
    r.add_argument("lng_max", type=int)
    r.add_argument("--root", default=os.environ.get("DEM_ROOT", str(Path.home() / "pond-data" / "dem")))
    args = ap.parse_args(argv)
    root = Path(args.root)
    (root / "tiles").mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    if args.cmd == "fetch":
        _fetch(args.lat, args.lng, root)
    elif args.cmd == "region":
        for lat in range(args.lat_min, args.lat_max + 1):
            for lng in range(args.lng_min, args.lng_max + 1):
                for attempt in range(3):
                    try:
                        _fetch(lat, lng, root)
                        print("ready", tile_key(lat, lng), flush=True)
                        break
                    except Exception as exc:  # noqa: BLE001 - retry flaky downloads
                        print("retry", tile_key(lat, lng), exc, flush=True)
                        time.sleep(5)
    else:
        for tif in sorted((root / "raw").glob("Copernicus_DSM_COG_10_*_DEM.tif")):
            parts = tif.stem.split("_")  # Copernicus DSM COG 10 N21 00 E081 00 DEM
            lat = int(parts[4][1:]) * (1 if parts[4][0] == "N" else -1)
            lng = int(parts[6][1:]) * (1 if parts[6][0] == "E" else -1)
            _fetch(lat, lng, root)
            print("ready", tile_key(lat, lng))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
