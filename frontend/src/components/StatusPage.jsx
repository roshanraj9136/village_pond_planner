import { useEffect, useState } from 'react'
import Icon from './Icons'
import { LogoMark } from './Logo'
import { gatewayStatus } from '../lib/api'
import { fmt } from '../lib/format'

const HOSTS = { sys1: 'Gateway host', sys2: 'Also runs PostgreSQL', sys3: '', sys4: '' }

export default function StatusPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let live = true
    const ctl = new AbortController()
    const tick = async () => {
      try {
        const { body } = await gatewayStatus(ctl.signal)
        if (live) {
          setData(body)
          setError(null)
        }
      } catch (e) {
        if (live && e.name !== 'AbortError') setError(e.message)
      }
    }
    tick()
    const t = setInterval(tick, 2000)
    return () => {
      live = false
      ctl.abort()
      clearInterval(t)
    }
  }, [])

  const g = data?.gateway
  const up = data?.workers?.filter((w) => w.alive).length ?? 0
  return (
    <main className="status-page">
      <header className="status-head">
        <a href="/" className="brand small"><LogoMark size={30} /><span className="brand-name">JalDrishti</span></a>
        <h1>System status</h1>
        <a className="link-btn" href="/">Back to the planner</a>
      </header>
      {error && <p className="banner error" role="alert"><Icon name="warning" size={18} />{error}</p>}
      {!data && !error && <p className="muted">Loading live metrics…</p>}
      {data && (
        <>
          <p className="status-summary">
            <span className={up === data.workers.length ? 'dot ok' : 'dot warn'} aria-hidden="true" />
            {up} of {data.workers.length} analysis workers are up. The page refreshes every 2 seconds.
          </p>
          <div className="metric-row">
            <div><span>{fmt(g.rps_1m, 1)}</span>requests / s (last minute)</div>
            <div><span>{fmt(data.latency_ms.p50)} ms</span>median API response</div>
            <div><span>{fmt(data.latency_ms.p95)} ms</span>95th percentile response</div>
            <div><span>{fmt(data.cache.hit_rate, 1)}%</span>answered from cache</div>
            <div><span>{fmt(g.queue_waiting)}</span>requests waiting</div>
          </div>
          <table className="workers">
            <caption>Analysis workers (one per system, 1 CPU and 512 MiB each)</caption>
            <thead>
              <tr><th scope="col">Worker</th><th scope="col">State</th><th scope="col">In progress</th><th scope="col">Served</th><th scope="col">Avg time</th><th scope="col">Busy / failed</th></tr>
            </thead>
            <tbody>
              {data.workers.map((w) => (
                <tr key={w.name}>
                  <th scope="row">{w.name}<small>{HOSTS[w.name] || ''}</small></th>
                  <td><span className={w.alive ? 'dot ok' : 'dot bad'} aria-hidden="true" />{w.alive ? 'Up' : 'Down'}</td>
                  <td>{w.in_flight} / {g.slots_per_worker}</td>
                  <td>{fmt(w.served)}</td>
                  <td>{w.avg_latency_ms ? `${fmt(w.avg_latency_ms)} ms` : '–'}</td>
                  <td>{w.busy_rejections} / {w.failures}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="facts status-facts">
            <div><dt>Requests handled</dt><dd>{fmt(g.requests)} ({fmt(g.api_requests)} to the API)</dd></div>
            <div><dt>Responses</dt><dd>{fmt(g.status_2xx)} ok, {fmt(g.status_4xx)} rejected input, {fmt(g.status_5xx)} errors</dd></div>
            <div><dt>Overload protection</dt><dd>{fmt(g.shed_503)} turned away when all {g.queue_limit} queue places were full, {fmt(g.retries)} retried on another worker</dd></div>
            <div><dt>Result cache</dt><dd>{fmt(data.cache.entries)} results, {fmt(data.cache.bytes / 1048576, 1)} of {fmt(data.cache.max_bytes / 1048576)} MB, kept {fmt(data.cache.ttl_s / 3600)} h</dd></div>
            <div><dt>Gateway</dt><dd>version {g.version}, up {fmt(g.uptime_s / 3600, 1)} h</dd></div>
          </dl>
        </>
      )}
    </main>
  )
}
