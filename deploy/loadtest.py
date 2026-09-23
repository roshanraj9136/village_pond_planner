"""Load test for the JalDrishti gateway (standard library only).

    python deploy/loadtest.py --scenario unique --concurrency 16 --requests 200
    python deploy/loadtest.py --scenario hot    --concurrency 64 --requests 2000
    python deploy/loadtest.py --scenario mixed  --concurrency 32 --requests 600

unique  every request is a different 400 m x 300 m parcel in the pre-loaded region, so
        each one is real work for an analysis worker (measures compute capacity)
hot     the same parcel every time (measures the gateway cache and single-flight)
mixed   80 % of requests pick from 10 popular parcels, 20 % are unique
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def parcel(rng: random.Random) -> dict:
    lat = rng.uniform(21.05, 21.45)
    lng = rng.uniform(81.15, 81.55)
    dlat, dlng = 0.0027, 0.0038  # ~300 m x ~400 m
    ring = [[lng, lat], [lng + dlng, lat], [lng + dlng, lat + dlat], [lng, lat + dlat], [lng, lat]]
    return {"area": {"type": "Polygon", "coordinates": [[[round(x, 6), round(y, 6)] for x, y in ring]]}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://10.1.75.53:3297")
    ap.add_argument("--scenario", choices=("unique", "hot", "mixed"), default="mixed")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--seed", type=int, default=None, help="default: new parcels every run (no warm cache)")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()

    rng = random.Random(args.seed if args.seed is not None else time.time_ns())
    popular = [parcel(rng) for _ in range(10)]
    hot = popular[0]
    bodies = []
    for _ in range(args.requests):
        if args.scenario == "hot":
            bodies.append(hot)
        elif args.scenario == "unique" or rng.random() < 0.2:
            bodies.append(parcel(rng))
        else:
            bodies.append(rng.choice(popular))

    lock = threading.Lock()
    lat_ok: list[float] = []
    codes: collections.Counter = collections.Counter()
    caches: collections.Counter = collections.Counter()
    workers: collections.Counter = collections.Counter()
    errors: collections.Counter = collections.Counter()

    def one(body: dict) -> None:
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{args.url}/api/analyze", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                resp.read()
                dt = time.perf_counter() - t
                with lock:
                    codes[resp.status] += 1
                    lat_ok.append(dt)
                    caches[resp.headers.get("X-Cache", "?")] += 1
                    workers[resp.headers.get("X-Worker", "?")] += 1
        except urllib.error.HTTPError as exc:
            with lock:
                codes[exc.code] += 1
        except Exception as exc:  # noqa: BLE001 - count every failure kind
            with lock:
                errors[type(exc).__name__] += 1

    print(f"{args.scenario}: {args.requests} requests, concurrency {args.concurrency} -> {args.url}")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(one, bodies))
    wall = time.perf_counter() - t0

    ok = codes.get(200, 0)
    print(f"  wall time        {wall:.1f} s")
    print(f"  throughput       {ok / wall:.1f} successful analyses / s")
    print(f"  success          {ok}/{args.requests} ({100 * ok / args.requests:.1f} %)")
    print(f"  status codes     {dict(codes)}  transport errors {dict(errors) or 0}")
    if lat_ok:
        q = statistics.quantiles(sorted(lat_ok), n=100)
        print(f"  latency (ok)     p50 {q[49] * 1e3:.0f} ms   p95 {q[94] * 1e3:.0f} ms   p99 {q[98] * 1e3:.0f} ms   max {max(lat_ok) * 1e3:.0f} ms")
    print(f"  gateway cache    {dict(caches)}")
    print(f"  served by        {dict(sorted(workers.items()))}")


if __name__ == "__main__":
    main()
