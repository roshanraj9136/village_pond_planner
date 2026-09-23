"""Saved pond sites, shared by all workers through PostgreSQL (schema ``pond``).

Workers are stateless; this is the only shared state. If the database is down the
analysis endpoints keep working and only saving/listing reports 503.
"""
from __future__ import annotations

import json
import threading
import time

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS pond;
CREATE TABLE IF NOT EXISTS pond.sites (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lng             DOUBLE PRECISION NOT NULL,
    source_type     TEXT NOT NULL,
    parcel_ha       DOUBLE PRECISION,
    catchment_ha    DOUBLE PRECISION NOT NULL,
    runoff_m3       DOUBLE PRECISION NOT NULL,
    capacity_m3     DOUBLE PRECISION NOT NULL,
    collectable_m3  DOUBLE PRECISION NOT NULL,
    pond_area_m2    DOUBLE PRECISION NOT NULL,
    depth_m         DOUBLE PRECISION NOT NULL,
    area_geojson    JSONB
);
"""
MAX_ROWS = 500
COLUMNS = ("id", "created_at", "name", "lat", "lng", "source_type", "parcel_ha", "catchment_ha", "runoff_m3",
           "capacity_m3", "collectable_m3", "pond_area_m2", "depth_m", "area_geojson")


class SitesUnavailable(Exception):
    pass


class SiteStore:
    def __init__(self, dsn: str | None):
        self.dsn = dsn
        self._pool = None
        self._lock = threading.Lock()
        self._down_until = 0.0

    def _get_pool(self):
        if not self.dsn:
            raise SitesUnavailable("Saved sites are disabled (no database configured).")
        if self._pool is not None:
            return self._pool
        if time.time() < self._down_until:
            raise SitesUnavailable("Database temporarily unavailable.")
        with self._lock:
            if self._pool is None:
                try:
                    from psycopg2.pool import ThreadedConnectionPool

                    pool = ThreadedConnectionPool(1, 3, self.dsn, connect_timeout=3)
                    conn = pool.getconn()
                    try:
                        with conn, conn.cursor() as cur:
                            cur.execute("SELECT pg_advisory_xact_lock(559001)")  # one worker creates the schema
                            cur.execute(SCHEMA_SQL)
                    finally:
                        pool.putconn(conn)
                    self._pool = pool
                except Exception as exc:  # noqa: BLE001 - any driver error means "down"
                    self._down_until = time.time() + 30
                    raise SitesUnavailable("Database unavailable.") from exc
        return self._pool

    def _run(self, fn):
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                return fn(cur)
        except Exception as exc:  # noqa: BLE001
            pool.putconn(conn, close=True)
            conn = None
            raise SitesUnavailable("Database error.") from exc
        finally:
            if conn is not None:
                pool.putconn(conn)

    def list(self) -> list[dict]:
        def q(cur):
            cur.execute(f"SELECT {', '.join(COLUMNS)} FROM pond.sites ORDER BY id DESC LIMIT 200")
            out = []
            for row in cur.fetchall():
                d = dict(zip(COLUMNS, row))
                d["created_at"] = d["created_at"].isoformat()
                out.append(d)
            return out
        return self._run(q)

    def add(self, site: dict) -> dict:
        def q(cur):
            cur.execute(
                """INSERT INTO pond.sites (name, lat, lng, source_type, parcel_ha, catchment_ha, runoff_m3,
                       capacity_m3, collectable_m3, pond_area_m2, depth_m, area_geojson)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at""",
                (site["name"], site["lat"], site["lng"], site["source_type"], site.get("parcel_ha"),
                 site["catchment_ha"], site["runoff_m3"], site["capacity_m3"], site["collectable_m3"],
                 site["pond_area_m2"], site["depth_m"],
                 json.dumps(site["area_geojson"]) if site.get("area_geojson") else None))
            new_id, created = cur.fetchone()
            cur.execute("DELETE FROM pond.sites WHERE id <= (SELECT max(id) FROM pond.sites) - %s", (MAX_ROWS,))
            return {"id": new_id, "created_at": created.isoformat()}
        return self._run(q)

    def delete(self, site_id: int) -> bool:
        def q(cur):
            cur.execute("DELETE FROM pond.sites WHERE id = %s", (site_id,))
            return cur.rowcount > 0
        return self._run(q)
