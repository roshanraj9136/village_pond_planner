"""Daily rainfall and runoff.

Rainfall: Open-Meteo historical archive (ERA5 reanalysis), daily totals for the ten
complete years 2015-2024 at the site. Responses are cached per 0.1 degree cell in
memory and on disk, so the external API is called at most once per cell per worker;
a failed call is remembered for a minute so an outage does not stall every request.

Runoff: USDA SCS Curve Number method applied to every rain day,
    S  = 25400 / CN - 254          (potential retention, mm)
    Ia = 0.2 S                     (initial abstraction, mm)
    Q  = (P - Ia)^2 / (P - Ia + S) for P > Ia, else 0
and averaged over the ten years. Daily application matters: the same annual total
falling as a few monsoon downpours produces far more runoff than steady drizzle.
CN is adjusted each day for antecedent moisture (dry / normal / wet soil from the
previous five days of rain), which is what makes a wet monsoon week run off.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START, END = "2015-01-01", "2024-12-31"
FALLBACK_ANNUAL_MM = 1150.0   # long-term normal for the Durg / Raipur plains (IMD)
FALLBACK_RUNOFF_COEFF = 0.25


class RainfallService:
    def __init__(self, cache_dir: str | Path, timeout_s: float = 12.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self._mem: OrderedDict[str, dict] = OrderedDict()
        self._fail_until: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @staticmethod
    def cell(lat: float, lng: float) -> tuple[float, float, str]:
        clat, clng = round(lat, 1), round(lng, 1)
        return clat, clng, f"{clat:+.1f}_{clng:+.1f}"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def series(self, lat: float, lng: float) -> dict | None:
        """{'time': [...], 'precip': [...]} for the cell, or None if unavailable."""
        clat, clng, key = self.cell(lat, lng)
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        with self._lock_for(key):  # concurrent requests for one cell share one download
            if key in self._mem:
                return self._mem[key]
            path = self.cache_dir / f"{key}.json"
            data = None
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    data = None
            if data is None:
                if self._fail_until.get(key, 0) > time.time():
                    return None
                data = self._download(clat, clng)
                if data is None:
                    self._fail_until[key] = time.time() + 60
                    return None
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(path)
            self._mem[key] = data
            while len(self._mem) > 256:
                self._mem.popitem(last=False)
            return data

    def _download(self, lat: float, lng: float) -> dict | None:
        q = urllib.parse.urlencode({
            "latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}", "start_date": START, "end_date": END,
            "daily": "precipitation_sum", "timezone": "Asia/Kolkata",
        })
        req = urllib.request.Request(f"{ARCHIVE_URL}?{q}", headers={"User-Agent": "JalDrishti/3.0 (IIT Bhilai CS559)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = json.loads(resp.read())
            daily = raw.get("daily") or {}
            times = daily.get("time") or []
            precip = [0.0 if v is None else float(v) for v in (daily.get("precipitation_sum") or [])]
            if len(times) < 3000 or len(times) != len(precip):
                return None
            return {"time": times, "precip": precip, "grid_lat": raw.get("latitude"), "grid_lng": raw.get("longitude")}
        except (OSError, ValueError):
            return None


def cn_for_amc(cn: float) -> tuple[float, float]:
    """(CN for dry AMC I, CN for wet AMC III) from the normal AMC II value (Chow et al., 1988)."""
    return 4.2 * cn / (10.0 - 0.058 * cn), 23.0 * cn / (10.0 + 0.13 * cn)


def scs_runoff_mm(p: np.ndarray, cn: float, months: np.ndarray | None = None) -> np.ndarray:
    """Daily SCS-CN runoff. With ``months``, CN follows the antecedent moisture condition
    (SCS NEH-4): the 5-day antecedent rainfall decides whether the soil is dry (AMC I),
    normal (AMC II) or wet (AMC III), with the growing-season thresholds used in Jun-Oct."""
    cn_day = np.full(p.shape, float(cn))
    if months is not None:
        p5 = np.convolve(p, np.ones(6), mode="full")[: p.size] - p  # rain in the 5 days before today
        growing = (months >= 6) & (months <= 10)
        dry_lim = np.where(growing, 35.6, 12.7)
        wet_lim = np.where(growing, 53.3, 27.9)
        cn1, cn3 = cn_for_amc(cn)
        cn_day = np.where(p5 < dry_lim, cn1, np.where(p5 > wet_lim, cn3, cn))
    s = 25400.0 / cn_day - 254.0
    ia = 0.2 * s
    excess = np.maximum(p - ia, 0.0)
    return excess * excess / (excess + s)


def water_budget(series: dict | None, cn: float) -> dict:
    """Mean annual rainfall and SCS-CN runoff depth for the cell."""
    if not series:
        return {
            "annual_rainfall_mm": FALLBACK_ANNUAL_MM,
            "monsoon_rainfall_mm": round(FALLBACK_ANNUAL_MM * 0.88, 1),
            "runoff_depth_mm": round(FALLBACK_ANNUAL_MM * FALLBACK_RUNOFF_COEFF, 1),
            "runoff_coefficient": FALLBACK_RUNOFF_COEFF,
            "max_daily_rainfall_mm": None,
            "rain_days_per_year": None,
            "yearly": [],
            "period": None,
            "method": "Fallback: regional normal x runoff coefficient (rainfall service unreachable)",
            "rainfall_source": "Regional normal (IMD) - live data unavailable",
            "is_fallback": True,
        }
    p = np.asarray(series["precip"], dtype=np.float64)
    years = np.array([t[:4] for t in series["time"]])
    months = np.array([int(t[5:7]) for t in series["time"]])
    q = scs_runoff_mm(p, cn, months)
    yearly = []
    for y in np.unique(years):
        m = years == y
        yearly.append({"year": int(y), "rain_mm": round(float(p[m].sum()), 1), "runoff_mm": round(float(q[m].sum()), 1)})
    n_years = len(yearly)
    annual_p = float(p.sum() / n_years)
    annual_q = float(q.sum() / n_years)
    monsoon = float(p[(months >= 6) & (months <= 9)].sum() / n_years)
    return {
        "annual_rainfall_mm": round(annual_p, 1),
        "monsoon_rainfall_mm": round(monsoon, 1),
        "runoff_depth_mm": round(annual_q, 1),
        "runoff_coefficient": round(annual_q / annual_p, 3) if annual_p > 0 else 0.0,
        "max_daily_rainfall_mm": round(float(p.max()), 1),
        "rain_days_per_year": round(float((p >= 2.5).sum() / n_years), 1),
        "yearly": yearly,
        "period": f"{yearly[0]['year']}-{yearly[-1]['year']}",
        "method": f"SCS Curve Number (CN {cn:g}, antecedent-moisture adjusted) on daily rainfall, "
                  f"averaged over {n_years} years",
        "rainfall_source": "Open-Meteo historical archive (ERA5 reanalysis)",
        "is_fallback": False,
    }
