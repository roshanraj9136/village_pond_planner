// OpenTelemetry for the gateway: traces, metrics and logs, exported over OTLP/HTTP.
//
// Switched on by OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://otel-collector:4318, set in
// docker-compose.yml). Without it the gateway behaves exactly as before: the global
// providers stay no-ops and logs only go to stderr.
//
//	traces   a server span per API request (otelhttp), with child spans for the wait for a
//	         worker slot and for every attempt to forward the request to a worker; cache
//	         hits and shared (single-flight) results link to the trace that computed them
//	metrics  HTTP server metrics by route and cache result, queue wait, upstream latency per
//	         worker, retries, shed requests, live gauges (queue, in-flight, worker up, cache),
//	         Go runtime metrics
//	logs     log/slog records, each carrying the trace and span id of its request
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"time"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	otelruntime "go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.43.0"
	"go.opentelemetry.io/otel/trace"
)

const scope = "github.com/roshanraj9136/village_pond_planner/gateway"

// The global tracer and meter delegate to the SDK once setupTelemetry installs it.
var (
	tracer = otel.Tracer(scope)
	meter  = otel.Meter(scope)
)

// setupTelemetry installs the SDK providers and the default slog logger. The returned
// function flushes everything still buffered; call it on shutdown.
func setupTelemetry(ctx context.Context, version, instance string) (func(context.Context) error, error) {
	text := traceIDs{slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})}
	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		slog.SetDefault(slog.New(text))
		return func(context.Context) error { return nil }, nil
	}

	// OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES are read by WithFromEnv.
	res, err := resource.New(ctx, resource.WithFromEnv(), resource.WithTelemetrySDK(), resource.WithHost(),
		resource.WithAttributes(semconv.ServiceVersion(version), semconv.ServiceInstanceID(instance),
			attribute.String("service.namespace", "jaldrishti")))
	if err != nil {
		return nil, err
	}

	traceExp, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, err
	}
	tp := sdktrace.NewTracerProvider(sdktrace.WithBatcher(traceExp), sdktrace.WithResource(res))
	otel.SetTracerProvider(tp)
	// W3C traceparent between gateway and workers (and from the browser)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))

	metricExp, err := otlpmetrichttp.New(ctx)
	if err != nil {
		return nil, err
	}
	// the export interval comes from OTEL_METRIC_EXPORT_INTERVAL (compose sets 10 s)
	mp := sdkmetric.NewMeterProvider(sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExp)), sdkmetric.WithResource(res))
	otel.SetMeterProvider(mp)

	logExp, err := otlploghttp.New(ctx)
	if err != nil {
		return nil, err
	}
	lp := sdklog.NewLoggerProvider(sdklog.WithProcessor(sdklog.NewBatchProcessor(logExp)), sdklog.WithResource(res))
	otel.SetLoggerProvider(lp)
	// every slog record goes to stderr (as before) and to the collector
	slog.SetDefault(slog.New(slog.NewMultiHandler(text, otelslog.NewHandler(scope, otelslog.WithLoggerProvider(lp)))))

	if err := otelruntime.Start(otelruntime.WithMinimumReadMemStatsInterval(5 * time.Second)); err != nil {
		return nil, err
	}

	return func(ctx context.Context) error {
		return errors.Join(tp.Shutdown(ctx), mp.Shutdown(ctx), lp.Shutdown(ctx))
	}, nil
}

// traceIDs adds the trace id to stderr lines, so a line can be looked up in Jaeger.
type traceIDs struct{ slog.Handler }

func (h traceIDs) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r = r.Clone()
		r.AddAttrs(slog.String("trace_id", sc.TraceID().String()))
	}
	return h.Handler.Handle(ctx, r)
}

func (h traceIDs) WithAttrs(as []slog.Attr) slog.Handler { return traceIDs{h.Handler.WithAttrs(as)} }
func (h traceIDs) WithGroup(name string) slog.Handler    { return traceIDs{h.Handler.WithGroup(name)} }

// ---------------------------------------------------------------- instruments

type otelMetrics struct {
	cacheLookups metric.Int64Counter
	queueWait    metric.Float64Histogram
	upstream     metric.Float64Histogram
	retries      metric.Int64Counter
	shed         metric.Int64Counter
}

func newOtelMetrics(g *Gateway) (*otelMetrics, error) {
	m := &otelMetrics{}
	var errs []error
	add := func(err error) { errs = append(errs, err) }
	var err error

	m.cacheLookups, err = meter.Int64Counter("pond.gateway.cache.lookups", metric.WithUnit("{request}"),
		metric.WithDescription("Cacheable API requests by result: hit, miss (computed), shared (joined an identical request in flight), bypass"))
	add(err)
	m.queueWait, err = meter.Float64Histogram("pond.gateway.queue.wait", metric.WithUnit("s"),
		metric.WithDescription("Time a request waited for a free worker slot"),
		metric.WithExplicitBucketBoundaries(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20))
	add(err)
	m.upstream, err = meter.Float64Histogram("pond.gateway.upstream.duration", metric.WithUnit("s"),
		metric.WithDescription("Time one forwarding attempt to a worker took"),
		metric.WithExplicitBucketBoundaries(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120))
	add(err)
	m.retries, err = meter.Int64Counter("pond.gateway.retries", metric.WithUnit("{attempt}"),
		metric.WithDescription("Requests sent again to another worker, by why the previous attempt failed"))
	add(err)
	m.shed, err = meter.Int64Counter("pond.gateway.shed", metric.WithUnit("{request}"),
		metric.WithDescription("Requests answered 503 because no worker slot became free in time"))
	add(err)

	waiting, err := meter.Int64ObservableGauge("pond.gateway.queue.waiting", metric.WithUnit("{request}"),
		metric.WithDescription("Requests waiting for a worker slot right now"))
	add(err)
	inflight, err := meter.Int64ObservableGauge("pond.gateway.worker.inflight", metric.WithUnit("{request}"),
		metric.WithDescription("Requests running or queued on each worker"))
	add(err)
	up, err := meter.Int64ObservableGauge("pond.gateway.worker.up", metric.WithUnit("1"),
		metric.WithDescription("1 when the worker passed its last health check, else 0"))
	add(err)
	entries, err := meter.Int64ObservableGauge("pond.gateway.cache.entries", metric.WithUnit("{entry}"),
		metric.WithDescription("Results held in the response cache"))
	add(err)
	size, err := meter.Int64ObservableGauge("pond.gateway.cache.size", metric.WithUnit("By"),
		metric.WithDescription("Compressed bytes held in the response cache"))
	add(err)
	_, err = meter.RegisterCallback(func(_ context.Context, o metric.Observer) error {
		o.ObserveInt64(waiting, g.pool.waiting.Load())
		for _, wk := range g.pool.workers {
			attrs := metric.WithAttributes(attribute.String("pond.worker", wk.Name))
			o.ObserveInt64(inflight, wk.inflight.Load(), attrs)
			alive := int64(0)
			if wk.alive.Load() {
				alive = 1
			}
			o.ObserveInt64(up, alive, attrs)
		}
		g.cache.mu.Lock()
		n, b := len(g.cache.items), g.cache.bytes
		g.cache.mu.Unlock()
		o.ObserveInt64(entries, int64(n))
		o.ObserveInt64(size, b)
		return nil
	}, waiting, inflight, up, entries, size)
	add(err)
	return m, errors.Join(errs...)
}

// ---------------------------------------------------------------- routes

var apiRoutes = map[string]bool{
	"/api/analyze": true, "/api/analyze/contour": true, "/api/sample": true, "/api/sample/contour_map": true,
	"/api/coverage": true, "/api/rainfall": true, "/api/sites": true, "/api/health": true,
	"/analyzeContour": true, "/findCatchment": true, "/api/analyzeContour": true, "/api/findCatchment": true,
	"/api/sampleContour": true, "/docs": true, "/redoc": true, "/openapi.json": true, "/docs/oauth2-redirect": true,
	"/gateway/status": true, "/gateway/health": true, "/gateway/register": true, "/otel/v1/traces": true,
}

// routeOf maps a path to a low-cardinality route for span names and metric labels.
func routeOf(p string) string {
	switch {
	case apiRoutes[p]:
		return p
	case len(p) > len("/api/sites/") && p[:len("/api/sites/")] == "/api/sites/":
		return "/api/sites/{id}"
	case isAPI(p):
		return "/api/other"
	default:
		return "static"
	}
}

// traced decides which requests get a server span. Health probes, worker registration,
// the browser's own span uploads and static files would only bury the API requests.
func traced(r *http.Request) bool {
	switch routeOf(r.URL.Path) {
	case "static", "/gateway/health", "/gateway/register", "/gateway/status", "/api/health", "/otel/v1/traces":
		return false
	}
	return true
}

// browserTraces forwards spans exported by the browser (same origin, so no CORS) to the
// collector. Disabled unless OTEL_BROWSER_TRACES_URL is set; it has no authentication, so
// it is meant for a local or lab deployment only.
func browserTraces() http.Handler {
	target := os.Getenv("OTEL_BROWSER_TRACES_URL")
	if target == "" {
		return nil
	}
	u, err := url.Parse(target)
	if err != nil || u.Host == "" {
		slog.Warn("ignoring bad OTEL_BROWSER_TRACES_URL", "value", target)
		return nil
	}
	rp := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(u)
			pr.Out.URL.Path = u.Path
			pr.Out.Header.Del("Cookie")
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			slog.Warn("could not forward browser spans to the collector", "error", err)
			w.WriteHeader(http.StatusBadGateway)
		},
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		rp.ServeHTTP(w, r)
	})
}
