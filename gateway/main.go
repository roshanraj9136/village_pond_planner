// JalDrishti gateway: the single public entry point (sys1:3297).
//
//   - serves the React frontend (pre-gzipped assets, SPA fallback)
//   - load-balances API calls over the analysis workers (least in-flight, health-checked)
//   - admission control: at most -slots requests per worker (each worker has 1 CPU);
//     extra requests wait in a bounded queue, and are shed with 503 when it is full
//   - retries on another worker when a worker is down or reports itself busy
//   - caches analysis results (gzip, LRU by bytes, TTL) and collapses identical
//     concurrent requests into one upstream call (single-flight)
//   - /gateway/status exposes live metrics for the status page
package main

import (
	"bytes"
	"compress/gzip"
	"container/list"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"mime"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// ---------------------------------------------------------------- workers

type Worker struct {
	Name     string
	URL      string
	alive    atomic.Bool
	inflight atomic.Int64
	served   atomic.Uint64
	failures atomic.Uint64
	busy503  atomic.Uint64
	ewmaMs   atomic.Uint64 // float64 bits
	version  atomic.Value  // string
	lastSeen atomic.Int64  // unix ms of last good health check
}

func float64bits(f float64) uint64     { return math.Float64bits(f) }
func float64frombits(b uint64) float64 { return math.Float64frombits(b) }

func (w *Worker) observe(d time.Duration) {
	ms := float64(d.Microseconds()) / 1000
	for {
		old := w.ewmaMs.Load()
		prev := float64frombits(old)
		next := ms
		if prev > 0 {
			next = 0.8*prev + 0.2*ms
		}
		if w.ewmaMs.CompareAndSwap(old, float64bits(next)) {
			return
		}
	}
}

type Pool struct {
	workers []*Worker
	slots   int64
	mu      sync.Mutex
	wake    chan struct{} // closed and replaced whenever a slot frees up
	rr      atomic.Uint64
	waiting atomic.Int64
	maxWait int64
}

func NewPool(workers []*Worker, slots int, maxWait int) *Pool {
	return &Pool{workers: workers, slots: int64(slots), wake: make(chan struct{}), maxWait: int64(maxWait)}
}

var errOverloaded = errors.New("overloaded")

// acquire picks the alive worker with the fewest in-flight requests that still has a free
// slot, skipping workers in `avoid`. It waits for a slot until ctx expires.
func (p *Pool) acquire(ctx context.Context, avoid map[*Worker]bool) (*Worker, error) {
	queued := false
	defer func() {
		if queued {
			p.waiting.Add(-1)
		}
	}()
	for {
		p.mu.Lock()
		var best *Worker
		n := len(p.workers)
		start := int(p.rr.Add(1))
		for i := 0; i < n; i++ {
			w := p.workers[(start+i)%n]
			if !w.alive.Load() || avoid[w] || w.inflight.Load() >= p.slots {
				continue
			}
			if best == nil || w.inflight.Load() < best.inflight.Load() {
				best = w
			}
		}
		if best != nil {
			best.inflight.Add(1)
			p.mu.Unlock()
			return best, nil
		}
		wake := p.wake
		p.mu.Unlock()
		if !queued {
			if p.waiting.Add(1) > p.maxWait {
				p.waiting.Add(-1)
				return nil, errOverloaded
			}
			queued = true
		}
		select {
		case <-wake:
		case <-time.After(500 * time.Millisecond): // re-check health changes too
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
}

func (p *Pool) release(w *Worker) {
	w.inflight.Add(-1)
	p.mu.Lock()
	close(p.wake)
	p.wake = make(chan struct{})
	p.mu.Unlock()
}

func (p *Pool) healthLoop(client *http.Client, every time.Duration) {
	check := func(w *Worker) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, w.URL+"/api/health", nil)
		resp, err := client.Do(req)
		ok := err == nil && resp.StatusCode == http.StatusOK
		if err == nil {
			var h struct {
				Version string `json:"version"`
			}
			_ = json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&h)
			resp.Body.Close()
			if h.Version != "" {
				w.version.Store(h.Version)
			}
		}
		was := w.alive.Swap(ok)
		if ok {
			w.lastSeen.Store(time.Now().UnixMilli())
		}
		if was != ok {
			log.Printf("worker %s is now %s", w.Name, map[bool]string{true: "UP", false: "DOWN"}[ok])
			p.mu.Lock()
			close(p.wake)
			p.wake = make(chan struct{})
			p.mu.Unlock()
		}
	}
	for {
		var wg sync.WaitGroup
		for _, w := range p.workers {
			wg.Add(1)
			go func(w *Worker) { defer wg.Done(); check(w) }(w)
		}
		wg.Wait()
		time.Sleep(every)
	}
}

// ---------------------------------------------------------------- cache

type entry struct {
	key     string
	status  int
	header  http.Header
	gz      []byte
	expires time.Time
	elem    *list.Element
}

type Cache struct {
	mu       sync.Mutex
	items    map[string]*entry
	lru      *list.List
	bytes    int64
	maxBytes int64
	ttl      time.Duration
	hits     atomic.Uint64
	misses   atomic.Uint64
	shared   atomic.Uint64
}

func NewCache(maxBytes int64, ttl time.Duration) *Cache {
	return &Cache{items: map[string]*entry{}, lru: list.New(), maxBytes: maxBytes, ttl: ttl}
}

func (c *Cache) get(key string) *entry {
	c.mu.Lock()
	defer c.mu.Unlock()
	e := c.items[key]
	if e == nil {
		return nil
	}
	if time.Now().After(e.expires) {
		c.removeLocked(e)
		return nil
	}
	c.lru.MoveToFront(e.elem)
	return e
}

func (c *Cache) put(e *entry) {
	size := int64(len(e.gz)) + 512
	if size > c.maxBytes/8 {
		return // never let one response evict most of the cache
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if old := c.items[e.key]; old != nil {
		c.removeLocked(old)
	}
	e.expires = time.Now().Add(c.ttl)
	e.elem = c.lru.PushFront(e)
	c.items[e.key] = e
	c.bytes += size
	for c.bytes > c.maxBytes {
		c.removeLocked(c.lru.Back().Value.(*entry))
	}
}

func (c *Cache) removeLocked(e *entry) {
	c.lru.Remove(e.elem)
	delete(c.items, e.key)
	c.bytes -= int64(len(e.gz)) + 512
}

type flight struct {
	done chan struct{}
	res  *entry
	err  error
}

type Group struct {
	mu sync.Mutex
	m  map[string]*flight
}

func (g *Group) do(key string, fn func() (*entry, error)) (*entry, error, bool) {
	g.mu.Lock()
	if f, ok := g.m[key]; ok {
		g.mu.Unlock()
		<-f.done
		return f.res, f.err, true
	}
	f := &flight{done: make(chan struct{})}
	g.m[key] = f
	g.mu.Unlock()
	f.res, f.err = fn()
	close(f.done)
	g.mu.Lock()
	delete(g.m, key)
	g.mu.Unlock()
	return f.res, f.err, false
}

// ---------------------------------------------------------------- metrics

type Metrics struct {
	start     time.Time
	requests  atomic.Uint64
	api       atomic.Uint64
	status2xx atomic.Uint64
	status4xx atomic.Uint64
	status5xx atomic.Uint64
	shed      atomic.Uint64
	retries   atomic.Uint64
	mu        sync.Mutex
	lat       [2048]float64
	latN      int
	recent    [60]uint64 // requests per second, ring buffer
	recentAt  [60]int64
}

func (m *Metrics) record(status int, d time.Duration, api bool) {
	switch {
	case status >= 500:
		m.status5xx.Add(1)
	case status >= 400:
		m.status4xx.Add(1)
	default:
		m.status2xx.Add(1)
	}
	now := time.Now().Unix()
	m.mu.Lock()
	i := now % 60
	if m.recentAt[i] != now {
		m.recentAt[i] = now
		m.recent[i] = 0
	}
	m.recent[i]++
	if api {
		m.lat[m.latN%len(m.lat)] = float64(d.Microseconds()) / 1000
		m.latN++
	}
	m.mu.Unlock()
}

func (m *Metrics) snapshot() (rps float64, p50, p95, p99 float64, samples int) {
	now := time.Now().Unix()
	m.mu.Lock()
	var total uint64
	for i := range m.recent {
		if now-m.recentAt[i] < 60 && m.recentAt[i] != now {
			total += m.recent[i]
		}
	}
	n := min(m.latN, len(m.lat))
	vals := make([]float64, n)
	copy(vals, m.lat[:n])
	m.mu.Unlock()
	rps = float64(total) / 59
	if n == 0 {
		return rps, 0, 0, 0, 0
	}
	sort.Float64s(vals)
	q := func(p float64) float64 { return vals[min(n-1, int(p*float64(n)))] }
	return rps, q(0.50), q(0.95), q(0.99), n
}

// ---------------------------------------------------------------- gateway

type Gateway struct {
	pool         *Pool
	cache        *Cache
	group        *Group
	client       *http.Client
	static       string
	maxBody      int64
	queueTimeout time.Duration
	metrics      *Metrics
	version      string
	writeLimiter *Limiter
}

var cacheable = map[string]bool{
	"POST /api/analyze":         true,
	"POST /api/analyze/contour": true,
	"POST /analyzeContour":      true,
	"POST /findCatchment":       true,
	"POST /api/analyzeContour":  true,
	"POST /api/findCatchment":   true,
	"GET /api/sample":           true,
	"GET /analyzeContour":       true,
	"GET /findCatchment":        true,
	"GET /api/sampleContour":    true,
	"GET /api/rainfall":         true,
	"GET /openapi.json":         true,
}

func isAPI(p string) bool {
	switch p {
	case "/docs", "/redoc", "/openapi.json", "/analyzeContour", "/findCatchment", "/docs/oauth2-redirect":
		return true
	}
	return strings.HasPrefix(p, "/api/")
}

func (g *Gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	t0 := time.Now()
	g.metrics.requests.Add(1)
	rec := &statusRecorder{ResponseWriter: w, status: 200}
	h := rec.Header()
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("Referrer-Policy", "strict-origin-when-cross-origin")
	switch {
	case r.URL.Path == "/gateway/status":
		g.status(rec)
	case r.URL.Path == "/gateway/health":
		writeJSON(rec, 200, map[string]any{"status": "ok"})
	case isAPI(r.URL.Path):
		g.metrics.api.Add(1)
		g.proxy(rec, r)
		g.metrics.record(rec.status, time.Since(t0), r.Method == http.MethodPost || strings.HasPrefix(r.URL.Path, "/api/sample"))
		return
	default:
		g.serveStatic(rec, r)
	}
	g.metrics.record(rec.status, time.Since(t0), false)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func errJSON(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"status": "error", "detail": msg})
}

func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func (g *Gateway) proxy(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodOptions {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/api/sites") && r.Method != http.MethodGet && !g.writeLimiter.allow(clientIP(r)) {
		w.Header().Set("Retry-After", "10")
		errJSON(w, http.StatusTooManyRequests, "Too many changes from this address; wait a few seconds.")
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, g.maxBody))
	if err != nil {
		errJSON(w, http.StatusRequestEntityTooLarge, fmt.Sprintf("Request body is larger than %d MB.", g.maxBody>>20))
		return
	}
	route := r.Method + " " + r.URL.Path
	if !cacheable[route] {
		e, err := g.forward(r.Context(), r, body)
		g.respond(w, r, e, err, "BYPASS")
		return
	}
	sum := sha256.New()
	fmt.Fprintf(sum, "%s %s?%s\n%s\n", r.Method, r.URL.Path, r.URL.RawQuery, r.Header.Get("Content-Type"))
	sum.Write(body)
	key := hex.EncodeToString(sum.Sum(nil))
	if e := g.cache.get(key); e != nil {
		g.cache.hits.Add(1)
		g.respond(w, r, e, nil, "HIT")
		return
	}
	e, err, shared := g.group.do(key, func() (*entry, error) {
		// detached: other callers may be waiting on this result even if this client leaves
		e, err := g.forward(context.WithoutCancel(r.Context()), r, body)
		if err == nil && e.status == http.StatusOK {
			e.key = key
			g.cache.put(e)
		}
		return e, err
	})
	if shared {
		g.cache.shared.Add(1)
		g.respond(w, r, e, err, "SHARED")
		return
	}
	g.cache.misses.Add(1)
	g.respond(w, r, e, err, "MISS")
}

var hopHeaders = map[string]bool{"Connection": true, "Keep-Alive": true, "Transfer-Encoding": true, "Upgrade": true,
	"Proxy-Connection": true, "Te": true, "Trailer": true, "Content-Length": true, "Content-Encoding": true, "Date": true, "Server": true}

// forward sends the buffered request to a worker, retrying elsewhere on connection
// failures and on "busy" 503s. The response is returned gzip-compressed.
func (g *Gateway) forward(parent context.Context, r *http.Request, body []byte) (*entry, error) {
	ctx, cancel := context.WithTimeout(parent, g.queueTimeout+g.client.Timeout)
	defer cancel()
	avoid := map[*Worker]bool{}
	var lastErr error
	for attempt := 0; attempt < len(g.pool.workers); attempt++ {
		qctx, qcancel := context.WithTimeout(ctx, g.queueTimeout)
		wk, err := g.pool.acquire(qctx, avoid)
		qcancel()
		if err != nil {
			if lastErr != nil {
				return nil, lastErr
			}
			return nil, errOverloaded
		}
		if attempt > 0 {
			g.metrics.retries.Add(1)
		}
		t := time.Now()
		req, _ := http.NewRequestWithContext(ctx, r.Method, wk.URL+r.URL.RequestURI(), bytes.NewReader(body))
		for k, vs := range r.Header {
			if !hopHeaders[k] && k != "Accept-Encoding" {
				req.Header[k] = vs
			}
		}
		req.Header.Set("X-Forwarded-For", clientIP(r))
		resp, err := g.client.Do(req)
		if err != nil {
			g.pool.release(wk)
			wk.failures.Add(1)
			if ctx.Err() == nil {
				wk.alive.Store(false) // health loop brings it back
			}
			lastErr = fmt.Errorf("worker %s: %w", wk.Name, err)
			avoid[wk] = true
			continue
		}
		raw, rerr := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
		resp.Body.Close()
		g.pool.release(wk)
		if rerr != nil {
			wk.failures.Add(1)
			lastErr = rerr
			avoid[wk] = true
			continue
		}
		if resp.StatusCode == http.StatusServiceUnavailable && resp.Header.Get("X-Worker-Busy") != "" {
			wk.busy503.Add(1)
			avoid[wk] = true
			lastErr = errOverloaded
			continue
		}
		wk.served.Add(1)
		wk.observe(time.Since(t))
		hdr := http.Header{}
		for k, vs := range resp.Header {
			if !hopHeaders[k] {
				hdr[k] = vs
			}
		}
		var buf bytes.Buffer
		zw, _ := gzip.NewWriterLevel(&buf, gzip.BestSpeed)
		_, _ = zw.Write(raw)
		_ = zw.Close()
		return &entry{status: resp.StatusCode, header: hdr, gz: buf.Bytes()}, nil
	}
	if lastErr == nil {
		lastErr = errOverloaded
	}
	return nil, lastErr
}

func (g *Gateway) respond(w http.ResponseWriter, r *http.Request, e *entry, err error, cacheState string) {
	if err != nil {
		if errors.Is(err, errOverloaded) || errors.Is(err, context.DeadlineExceeded) {
			g.metrics.shed.Add(1)
			w.Header().Set("Retry-After", "3")
			errJSON(w, http.StatusServiceUnavailable, "All analysis workers are busy. Please retry in a few seconds.")
			return
		}
		if errors.Is(err, context.Canceled) {
			return
		}
		log.Printf("upstream error %s %s: %v", r.Method, r.URL.Path, err)
		errJSON(w, http.StatusBadGateway, "No analysis worker could complete the request.")
		return
	}
	h := w.Header()
	for k, vs := range e.header {
		h[k] = vs
	}
	h.Set("X-Cache", cacheState)
	h.Set("Vary", "Accept-Encoding")
	if cacheState != "BYPASS" {
		h.Set("Cache-Control", "no-cache")
	}
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		h.Set("Content-Encoding", "gzip")
		h.Set("Content-Length", fmt.Sprint(len(e.gz)))
		w.WriteHeader(e.status)
		if r.Method != http.MethodHead {
			_, _ = w.Write(e.gz)
		}
		return
	}
	zr, zerr := gzip.NewReader(bytes.NewReader(e.gz))
	if zerr != nil {
		errJSON(w, http.StatusInternalServerError, "corrupt cache entry")
		return
	}
	w.WriteHeader(e.status)
	_, _ = io.Copy(w, zr)
}

// ---------------------------------------------------------------- static files

func (g *Gateway) serveStatic(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		errJSON(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	clean := path.Clean("/" + r.URL.Path)
	file := filepath.Join(g.static, filepath.FromSlash(clean))
	st, err := os.Stat(file)
	if err != nil || st.IsDir() {
		if path.Ext(clean) != "" && !strings.HasSuffix(clean, ".html") {
			http.NotFound(w, r)
			return
		}
		file = filepath.Join(g.static, "index.html") // SPA route
	}
	h := w.Header()
	if strings.HasPrefix(clean, "/assets/") {
		h.Set("Cache-Control", "public, max-age=31536000, immutable")
	} else {
		h.Set("Cache-Control", "no-cache")
	}
	ctype := mime.TypeByExtension(filepath.Ext(file))
	if ctype == "" {
		ctype = "application/octet-stream"
	}
	h.Set("Content-Type", ctype)
	h.Set("Vary", "Accept-Encoding")
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		if gz, err := os.Open(file + ".gz"); err == nil {
			defer gz.Close()
			if gst, err := gz.Stat(); err == nil {
				h.Set("Content-Encoding", "gzip")
				http.ServeContent(w, r, "", gst.ModTime(), gz)
				return
			}
		}
	}
	f, err := os.Open(file)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	fst, _ := f.Stat()
	http.ServeContent(w, r, "", fst.ModTime(), f)
}

// ---------------------------------------------------------------- status

func (g *Gateway) status(w http.ResponseWriter) {
	type ws struct {
		Name      string  `json:"name"`
		URL       string  `json:"url"`
		Alive     bool    `json:"alive"`
		InFlight  int64   `json:"in_flight"`
		Served    uint64  `json:"served"`
		Failures  uint64  `json:"failures"`
		Busy503   uint64  `json:"busy_rejections"`
		EwmaMs    float64 `json:"avg_latency_ms"`
		Version   string  `json:"version"`
		LastSeenS float64 `json:"last_health_ok_s_ago"`
	}
	rows := []ws{}
	for _, wk := range g.pool.workers {
		v, _ := wk.version.Load().(string)
		ago := -1.0
		if ls := wk.lastSeen.Load(); ls > 0 {
			ago = float64(time.Now().UnixMilli()-ls) / 1000
		}
		rows = append(rows, ws{wk.Name, wk.URL, wk.alive.Load(), wk.inflight.Load(), wk.served.Load(), wk.failures.Load(),
			wk.busy503.Load(), round1(float64frombits(wk.ewmaMs.Load())), v, round1(ago)})
	}
	rps, p50, p95, p99, n := g.metrics.snapshot()
	g.cache.mu.Lock()
	entries, bytesUsed := len(g.cache.items), g.cache.bytes
	g.cache.mu.Unlock()
	hits, misses, shared := g.cache.hits.Load(), g.cache.misses.Load(), g.cache.shared.Load()
	hitRate := 0.0
	if t := hits + misses + shared; t > 0 {
		hitRate = float64(hits+shared) / float64(t)
	}
	writeJSON(w, 200, map[string]any{
		"gateway": map[string]any{
			"version": g.version, "uptime_s": int(time.Since(g.metrics.start).Seconds()),
			"requests": g.metrics.requests.Load(), "api_requests": g.metrics.api.Load(),
			"rps_1m": round1(rps), "status_2xx": g.metrics.status2xx.Load(), "status_4xx": g.metrics.status4xx.Load(),
			"status_5xx": g.metrics.status5xx.Load(), "shed_503": g.metrics.shed.Load(), "retries": g.metrics.retries.Load(),
			"queue_waiting": g.pool.waiting.Load(), "queue_limit": g.pool.maxWait, "slots_per_worker": g.pool.slots,
		},
		"latency_ms": map[string]any{"p50": round1(p50), "p95": round1(p95), "p99": round1(p99), "samples": n},
		"cache": map[string]any{"entries": entries, "bytes": bytesUsed, "max_bytes": g.cache.maxBytes,
			"hits": hits, "misses": misses, "shared": shared, "hit_rate": round1(hitRate * 100), "ttl_s": int(g.cache.ttl.Seconds())},
		"workers": rows,
	})
}

func round1(v float64) float64 { return float64(int64(v*10+0.5)) / 10 }

// ---------------------------------------------------------------- write limiter

type Limiter struct {
	mu      sync.Mutex
	buckets map[string]*bucket
	rate    float64
	burst   float64
}

type bucket struct {
	tokens float64
	last   time.Time
}

func (l *Limiter) allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	b := l.buckets[key]
	if b == nil {
		if len(l.buckets) > 10000 {
			l.buckets = map[string]*bucket{}
		}
		b = &bucket{tokens: l.burst, last: now}
		l.buckets[key] = b
	}
	b.tokens = min(l.burst, b.tokens+now.Sub(b.last).Seconds()*l.rate)
	b.last = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

// ---------------------------------------------------------------- main

func main() {
	// The lab host forwards the public 10.1.75.53:3297 to port 3000 inside sys1, so listen on both.
	addr := flag.String("addr", "0.0.0.0:3000,0.0.0.0:3297", "comma-separated listen addresses")
	static := flag.String("static", "../frontend/dist", "built frontend directory")
	workersFlag := flag.String("workers", "sys1=http://127.0.0.1:8001", "comma-separated name=url list")
	slots := flag.Int("slots", 2, "concurrent requests per worker (1 running + 1 queued on a 1-CPU worker)")
	queueMax := flag.Int("queue", 256, "requests allowed to wait for a slot before shedding with 503")
	queueTimeout := flag.Duration("queue-timeout", 20*time.Second, "longest wait for a free worker slot")
	upstreamTimeout := flag.Duration("upstream-timeout", 240*time.Second, "worker response timeout (tile downloads can take a minute)")
	cacheMB := flag.Int("cache-mb", 96, "response cache size (compressed)")
	cacheTTL := flag.Duration("cache-ttl", 6*time.Hour, "response cache lifetime")
	maxBodyMB := flag.Int64("max-body-mb", 26, "largest request body (contour uploads)")
	version := flag.String("version", "dev", "build identifier shown in /gateway/status")
	flag.Parse()

	var workers []*Worker
	for _, part := range strings.Split(*workersFlag, ",") {
		name, url, ok := strings.Cut(strings.TrimSpace(part), "=")
		if !ok {
			log.Fatalf("bad -workers entry %q (want name=url)", part)
		}
		workers = append(workers, &Worker{Name: name, URL: strings.TrimRight(url, "/")})
	}
	transport := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: 3 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConnsPerHost: 16,
		IdleConnTimeout:     90 * time.Second,
	}
	pool := NewPool(workers, *slots, *queueMax)
	go pool.healthLoop(&http.Client{Transport: transport, Timeout: 2 * time.Second}, 2*time.Second)

	g := &Gateway{
		pool:         pool,
		cache:        NewCache(int64(*cacheMB)<<20, *cacheTTL),
		group:        &Group{m: map[string]*flight{}},
		client:       &http.Client{Transport: transport, Timeout: *upstreamTimeout},
		static:       *static,
		maxBody:      *maxBodyMB << 20,
		queueTimeout: *queueTimeout,
		metrics:      &Metrics{start: time.Now()},
		version:      *version,
		writeLimiter: &Limiter{buckets: map[string]*bucket{}, rate: 0.5, burst: 20},
	}
	srv := &http.Server{
		Handler:           g,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      *upstreamTimeout + *queueTimeout + 30*time.Second,
		IdleTimeout:       90 * time.Second,
		MaxHeaderBytes:    1 << 16,
	}
	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
		<-sig
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = srv.Shutdown(ctx)
	}()
	var listeners []net.Listener
	for _, a := range strings.Split(*addr, ",") {
		l, err := net.Listen("tcp", strings.TrimSpace(a))
		if err != nil {
			log.Fatalf("listen %s: %v", a, err)
		}
		listeners = append(listeners, l)
	}
	log.Printf("gateway %s listening on %s, %d workers, static=%s", *version, *addr, len(workers), *static)
	errs := make(chan error, len(listeners))
	for _, l := range listeners {
		go func(l net.Listener) { errs <- srv.Serve(l) }(l)
	}
	if err := <-errs; err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
