"""Writes observability/grafana/dashboards/jaldrishti.json (run from the repository root).

python observability/grafana/make_dashboard.py
"""
import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
GW = 'job="jaldrishti/pond-gateway"'
WK = 'job="jaldrishti/pond-worker"'
panels = []
_id = 0


def panel(kind, title, x, y, w, h, targets, unit=None, desc="", legend=True, extra=None, ds=PROM, stack=False):
    global _id
    _id += 1
    p = {
        "id": _id, "type": kind, "title": title, "description": desc, "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": chr(65 + i), "datasource": ds, **t} for i, t in enumerate(targets)],
        "fieldConfig": {"defaults": {"custom": {}}, "overrides": []},
        "options": {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if kind == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2, "fillOpacity": 12, "showPoints": "never",
                                                 "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"}}
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": legend},
                        "tooltip": {"mode": "multi", "sort": "desc"}}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "area", "textMode": "value_and_name", "justifyMode": "auto"}
    if extra:
        for k, v in extra.items():
            p.setdefault(k, {}).update(v) if isinstance(v, dict) else p.__setitem__(k, v)
    panels.append(p)
    return p


def q(expr, legend="", exemplar=False, instant=False):
    t = {"expr": expr, "legendFormat": legend, "range": not instant, "instant": instant}
    if exemplar:
        t["exemplar"] = True
    return t


# row 1: is it up, how busy, how fast
panel("stat", "Workers up", 0, 0, 4, 4, [q(f"sum(pond_gateway_worker_up_ratio{{{GW}}})", "up")],
      desc="Workers that passed the gateway's last health check (out of 2).",
      extra={"fieldConfig": {"defaults": {"min": 0, "max": 2, "thresholds": {"mode": "absolute", "steps": [
          {"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}]}}}})
panel("stat", "API requests / s", 4, 0, 4, 4,
      [q(f'sum(rate(http_server_request_duration_seconds_count{{{GW}}}[1m]))', "req/s")], unit="reqps",
      desc="Requests through the gateway, last minute.")
panel("stat", "Cache hit ratio", 8, 0, 4, 4,
      [q(f'sum(rate(pond_gateway_cache_lookups_total{{{GW},pond_cache_result=~"hit|shared"}}[5m])) / '
         f'sum(rate(pond_gateway_cache_lookups_total{{{GW},pond_cache_result!="bypass"}}[5m]))', "hit ratio")],
      unit="percentunit", desc="Share of cacheable requests answered without new work (hit or shared).")
panel("stat", "p95 analysis latency", 12, 0, 4, 4,
      [q(f'histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{{{GW},http_route=~"/api/analyze.*|/api/sample|/analyzeContour|/findCatchment"}}[5m])))', "p95")],
      unit="s", desc="95th percentile end-to-end time of analysis requests at the gateway.")
panel("stat", "Shed with 503 (since start)", 16, 0, 4, 4, [q(f"sum(pond_gateway_shed_total{{{GW}}}) or vector(0)", "shed")],
      desc="Requests the gateway refused because no worker slot became free in time (counter since the gateway started).")
panel("stat", "Retries (since start)", 20, 0, 4, 4, [q(f"sum(pond_gateway_retries_total{{{GW}}}) or vector(0)", "retries")],
      desc="Requests the gateway had to send to a second worker (counter since the gateway started).")

# row 2: traffic and latency
panel("timeseries", "Gateway requests by route and cache result", 0, 4, 12, 8,
      [q(f'sum by (http_route, pond_cache) (rate(http_server_request_duration_seconds_count{{{GW}}}[1m]))',
         "{{http_route}} {{pond_cache}}")], unit="reqps", stack=True,
      desc="otelhttp server metric, labelled with the route and the gateway cache result.")
panel("timeseries", "Analysis latency at the gateway (p50 / p95 / p99)", 12, 4, 12, 8,
      [q(f'histogram_quantile({p}, sum by (le) (rate(http_server_request_duration_seconds_bucket{{{GW},http_route=~"/api/analyze.*|/api/sample|/analyzeContour|/findCatchment"}}[1m])))',
         f"p{int(float(p) * 100)}", exemplar=(p == "0.99")) for p in ("0.5", "0.95", "0.99")], unit="s",
      desc="Diamonds are exemplars: individual slow requests, each linked to its trace.")

# row 3: where the time goes
panel("timeseries", "Pipeline stage time (worker, average per call)", 0, 12, 12, 8,
      [q(f'sum by (pond_stage) (rate(pond_analysis_stage_duration_seconds_sum{{{WK}}}[2m])) / '
         f'sum by (pond_stage) (rate(pond_analysis_stage_duration_seconds_count{{{WK}}}[2m]))', "{{pond_stage}}")],
      unit="s", desc="Mean duration of each stage of the analysis pipeline (manual spans + histogram).")
panel("timeseries", "Queue wait and upstream time by worker", 12, 12, 12, 8,
      [q(f'histogram_quantile(0.95, sum by (le) (rate(pond_gateway_queue_wait_seconds_bucket{{{GW}}}[1m])))', "p95 wait for a worker slot"),
       q(f'histogram_quantile(0.95, sum by (le, pond_worker) (rate(pond_gateway_upstream_duration_seconds_bucket{{{GW}}}[1m])))',
         "p95 upstream {{pond_worker}}")],
      unit="s", desc="Admission-control queue (gateway) versus the time the workers take.")

# row 4: load and resources
panel("timeseries", "Load: waiting in the gateway queue and in flight per worker", 0, 20, 12, 8,
      [q(f"pond_gateway_queue_waiting{{{GW}}}", "waiting in gateway queue"),
       q(f"pond_gateway_worker_inflight{{{GW}}}", "in flight {{pond_worker}}")],
      unit="short", desc="Gauges observed by the gateway every 10 s.")
panel("timeseries", "Worker memory (RSS) and CPU", 12, 20, 12, 8,
      [q(f"process_memory_usage_bytes{{{WK}}}", "RSS {{instance}}"),
       q(f"sum by (instance) (rate(process_cpu_time_seconds_total{{{WK}}}[1m]))", "CPU cores {{instance}}")],
      desc="System metrics instrumentation in the Python workers (limit 512 MiB, 1 CPU each).",
      extra={"fieldConfig": {"overrides": [
          {"matcher": {"id": "byRegexp", "options": "RSS.*"}, "properties": [{"id": "unit", "value": "bytes"}]},
          {"matcher": {"id": "byRegexp", "options": "CPU.*"}, "properties": [
              {"id": "unit", "value": "short"}, {"id": "custom.axisPlacement", "value": "right"}]}]}})

# row 5: domain results and data sources
panel("timeseries", "Where rainfall data came from (lookups per 5 min)", 0, 28, 8, 8,
      [q(f"sum by (pond_rainfall_source) (increase(pond_rainfall_lookups_total{{{WK}}}[5m]))", "{{pond_rainfall_source}}")],
      unit="short", desc="memory / disk cache, download, nearby stand-in, or regional fallback.")
panel("timeseries", "Results: catchment area and collectable water (mean)", 8, 28, 8, 8,
      [q(f"sum(rate(pond_catchment_area_ha_sum{{{WK}}}[5m])) / sum(rate(pond_catchment_area_ha_count{{{WK}}}[5m]))", "catchment (ha)"),
       q(f"sum(rate(pond_water_collectable_m3_sum{{{WK}}}[5m])) / sum(rate(pond_water_collectable_m3_count{{{WK}}}[5m]))", "collectable water (m3)")],
      extra={"fieldConfig": {"overrides": [{"matcher": {"id": "byName", "options": "collectable water (m3)"},
                                             "properties": [{"id": "custom.axisPlacement", "value": "right"}]}]}},
      desc="What the analyses found, not just how fast they ran.")
panel("timeseries", "Gateway (Go) memory and goroutines", 16, 28, 8, 8,
      [q(f"sum(go_memory_used_bytes{{{GW}}})", "memory used"), q(f"go_goroutine_count{{{GW}}}", "goroutines")],
      extra={"fieldConfig": {"overrides": [
          {"matcher": {"id": "byName", "options": "memory used"}, "properties": [{"id": "unit", "value": "bytes"}]},
          {"matcher": {"id": "byName", "options": "goroutines"}, "properties": [{"id": "custom.axisPlacement", "value": "right"}]}]}},
      desc="Go runtime metrics (contrib/instrumentation/runtime).")

# row 6: logs
panel("logs", "Warnings and errors (all services)", 0, 36, 24, 8,
      [{"expr": '{service_namespace="jaldrishti"} | severity_text=~"WARN.*|ERROR|CRITICAL|FATAL"', "queryType": "range"}],
      ds=LOKI, extra={"options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                                  "enableLogDetails": True, "showLabels": True, "dedupStrategy": "none"}},
      desc="OpenTelemetry log records in Loki; open a line to jump to its trace.")

dashboard = {
    "uid": "jaldrishti", "title": "JalDrishti - pond planner (OpenTelemetry)",
    "description": "Gateway, workers and pipeline of the village pond planner, from OpenTelemetry metrics and logs.",
    "tags": ["jaldrishti", "opentelemetry"], "timezone": "browser", "editable": True, "graphTooltip": 1,
    "refresh": "10s", "schemaVersion": 41, "version": 1,
    "time": {"from": "now-30m", "to": "now"}, "panels": panels, "templating": {"list": []}, "annotations": {"list": []},
}
out = Path(__file__).resolve().parent / "dashboards" / "jaldrishti.json"
out.write_text(json.dumps(dashboard, indent=1) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(panels)} panels)")
