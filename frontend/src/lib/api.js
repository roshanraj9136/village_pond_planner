// All calls are relative, so the app works behind the gateway on any host/port.

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function parse(res) {
  const text = await res.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!res.ok) {
    let detail = body?.detail
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join('; ')
    const fallback =
      res.status === 503
        ? 'All analysis workers are busy. Try again in a few seconds.'
        : res.status === 502
          ? 'The server could not reach an analysis worker.'
          : `Request failed (${res.status}).`
    throw new ApiError(detail || fallback, res.status)
  }
  return { body, meta: { worker: res.headers.get('X-Worker'), cache: res.headers.get('X-Cache') } }
}

const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(t)
      reject(new DOMException('Aborted', 'AbortError'))
    })
  })

// The campus network path to the lab host drops some *new* TCP connections (about half;
// which ones depends on the connection's source port), and a dropped connect only fails
// after ~20 s, so the site would seem to hang. Before each API call the app therefore makes
// sure the browser holds a working connection: a tiny request to the gateway, given up
// after 2.5 s and retried (each retry opens a new connection). The real request then reuses
// that open keep-alive connection, and a slow ping keeps it open while the page is idle.
const PROBE_TIMEOUT_MS = 2500
const PROBE_ATTEMPTS = 8

async function probe() {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS)
  try {
    const res = await fetch('/gateway/health', { cache: 'no-store', signal: ctl.signal })
    await res.arrayBuffer()
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

let probing = null // concurrent callers share one probe

async function ensureConnection(signal) {
  probing ??= (async () => {
    try {
      for (let i = 0; i < PROBE_ATTEMPTS; i++) if (await probe()) return true
      return false
    } finally {
      probing = null
    }
  })()
  let onAbort
  const aborted = new Promise((_, reject) => {
    onAbort = () => reject(new DOMException('Aborted', 'AbortError'))
    if (signal?.aborted) onAbort()
    signal?.addEventListener('abort', onAbort, { once: true })
  })
  try {
    if (!(await Promise.race([probing, aborted]))) {
      throw new ApiError('Cannot reach the server. Check your network connection and try again.', 0)
    }
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}

/** Keeps one working connection open while the page is visible; returns a cleanup function. */
export function keepConnectionWarm(everyMs = 30000) {
  const ping = () => {
    if (document.visibilityState === 'visible') probe()
  }
  const timer = setInterval(ping, everyMs)
  document.addEventListener('visibilitychange', ping)
  return () => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', ping)
  }
}

/** Analyses are idempotent, so a 503 ("all workers busy") is retried after the server's
 *  Retry-After, with jitter so many clients do not come back in lockstep. Idempotent calls
 *  are also repeated when the connection fails; saving a site is not, to avoid duplicates. */
async function request(url, options = {}, { retries = 0, onRetry, idempotent = true } = {}) {
  let networkFailures = 0
  for (let attempt = 0; ; attempt++) {
    await ensureConnection(options.signal)
    let res
    try {
      res = await fetch(url, options)
    } catch (err) {
      if (err.name === 'AbortError') throw err
      if (idempotent && networkFailures++ < 2) continue
      throw new ApiError('Cannot reach the server. Check your network connection and try again.', 0)
    }
    if (res.status === 503 && attempt < retries) {
      const wait = Math.min(8, Number(res.headers.get('Retry-After')) || 2) * 1000
      onRetry?.(attempt + 1)
      await sleep(wait * (0.75 + Math.random() * 0.5), options.signal)
      continue
    }
    return parse(res)
  }
}

export function analyzeParcel(area, params, signal, onRetry) {
  return request(
    '/api/analyze',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ area, ...params }), signal },
    { retries: 4, onRetry },
  )
}

export function analyzeContour({ file, useSample, area, params }, signal, onRetry) {
  const form = new FormData()
  if (file) form.append('contour_map', file, file.name)
  if (useSample) form.append('use_sample', 'true')
  if (area) form.append('area', JSON.stringify(area))
  for (const [k, v] of Object.entries(params)) form.append(k, String(v))
  return request('/api/analyze/contour', { method: 'POST', body: form, signal }, { retries: 4, onRetry })
}

export const listSites = () => request('/api/sites')
export const deleteSite = (id) => request(`/api/sites/${id}`, { method: 'DELETE' })
export const saveSite = (site) =>
  request(
    '/api/sites',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(site) },
    { idempotent: false },
  )
export const gatewayStatus = (signal) => request('/gateway/status', { signal })
export const coverage = () => request('/api/coverage')

export async function searchPlace(query, signal) {
  const url = `https://nominatim.openstreetmap.org/search?format=json&limit=5&countrycodes=in&accept-language=en&q=${encodeURIComponent(query)}`
  const res = await fetch(url, { signal })
  if (!res.ok) throw new ApiError('Place search is unavailable right now.', res.status)
  return res.json()
}

export async function placeName(lat, lng) {
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&zoom=14&accept-language=en&lat=${lat}&lon=${lng}`,
    )
    if (!res.ok) return null
    const data = await res.json()
    const a = data.address || {}
    return a.village || a.hamlet || a.suburb || a.town || a.city || a.county || null
  } catch {
    return null
  }
}
