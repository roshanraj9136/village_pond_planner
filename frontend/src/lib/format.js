const M_PER_DEG = (Math.PI * 6371008.8) / 180

/** Planar area (m^2) of a [lng, lat] ring on a local equirectangular projection. */
export function ringArea(ring) {
  if (!ring || ring.length < 3) return 0
  const lat0 = ring.reduce((s, p) => s + p[1], 0) / ring.length
  const k = Math.cos((lat0 * Math.PI) / 180)
  let a = 0
  for (let i = 0; i < ring.length; i++) {
    const [x1, y1] = ring[i]
    const [x2, y2] = ring[(i + 1) % ring.length]
    a += x1 * k * y2 - x2 * k * y1
  }
  return (Math.abs(a) / 2) * M_PER_DEG * M_PER_DEG
}

export function toPolygon(ring) {
  const closed = [...ring, ring[0]]
  return { type: 'Polygon', coordinates: [closed] }
}

const nf = (d) => new Intl.NumberFormat('en-IN', { maximumFractionDigits: d, minimumFractionDigits: 0 })

export const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? '–' : nf(d).format(v))

export function fmtArea(m2) {
  if (m2 == null) return '–'
  if (m2 < 10000) return `${fmt(m2)} m²`
  return `${fmt(m2 / 10000, m2 < 1e6 ? 2 : 1)} ha`
}

export const acres = (m2) => m2 / 4046.8564224

/** Indian units: 1 lakh litres = 100 m^3, 1 crore litres = 10,000 m^3. */
export function fmtLitres(m3) {
  const litres = m3 * 1000
  if (litres >= 1e7) return `${fmt(litres / 1e7, 2)} crore litres`
  if (litres >= 1e5) return `${fmt(litres / 1e5, 1)} lakh litres`
  return `${fmt(litres)} litres`
}

export const fmtCoord = (v) => (v == null ? '–' : v.toFixed(5))

export function boundsOf(geojsons) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  const visit = (c) => {
    if (typeof c[0] === 'number') {
      minX = Math.min(minX, c[0])
      maxX = Math.max(maxX, c[0])
      minY = Math.min(minY, c[1])
      maxY = Math.max(maxY, c[1])
    } else c.forEach(visit)
  }
  for (const g of geojsons) if (g?.coordinates) visit(g.coordinates)
  if (!Number.isFinite(minX)) return null
  return [
    [minY, minX],
    [maxY, maxX],
  ]
}
