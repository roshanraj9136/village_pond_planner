"""Outbound HTTPS that copes with the lab network.

The lab host's uplink drops a share of new TCP connections (which ones depends on the
connection's source port), and a dropped connect only fails when the whole socket timeout
runs out. So the connect and TLS handshake get their own short timeout, every retry opens a
new connection (and so a new source port), and an established transfer keeps a longer read
timeout. A deadline bounds the total time spent on one URL.
"""
from __future__ import annotations

import http.client
import ssl
import time
import urllib.parse
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

_TLS = ssl.create_default_context()
_tracer = trace.get_tracer("jaldrishti.netfetch")


class HTTPStatusError(OSError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} from {urllib.parse.urlsplit(url).hostname}")
        self.status = status


@contextmanager
def get(url: str, *, headers: dict | None = None, connect_timeout: float = 3.0, read_timeout: float = 30.0,
        deadline_s: float = 30.0):
    """GET `url` and yield the 200 response (read it inside the block). Raises
    HTTPStatusError for other statuses, OSError when no attempt got through in time."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise ValueError("https only")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    give_up = time.monotonic() + deadline_s
    last: Exception | None = None
    # One client span for the whole download; `netfetch.attempts` shows how many new
    # connections it took (the lab uplink drops some of them).
    with _tracer.start_as_current_span(f"GET {u.hostname}", kind=SpanKind.CLIENT, attributes={
            "http.request.method": "GET", "server.address": u.hostname, "server.port": u.port or 443,
            "url.full": f"https://{u.hostname}{u.path}"}) as span:
        attempts = 0
        while time.monotonic() < give_up:
            attempts += 1
            span.set_attribute("netfetch.attempts", attempts)
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=connect_timeout, context=_TLS)
            try:
                conn.connect()
                conn.sock.settimeout(read_timeout)
                conn.request("GET", path, headers=headers or {})
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                last = exc
                span.add_event("connection attempt failed", {"exception.type": type(exc).__name__, "exception.message": str(exc)})
                time.sleep(0.3)  # instant failures (DNS, refused) must not spin
                continue
            try:
                span.set_attribute("http.response.status_code", resp.status)
                if resp.status != 200:
                    raise HTTPStatusError(resp.status, url)
                yield resp
                return
            finally:
                conn.close()
        span.set_status(Status(StatusCode.ERROR, "deadline exceeded"))
        raise OSError(f"could not reach {u.hostname} within {deadline_s:.0f} s ({last})")
