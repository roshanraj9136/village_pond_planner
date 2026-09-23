import { fmt } from '../lib/format'

/**
 * Cross-section of the suggested pond, drawn to scale from the computed numbers:
 * top width, usable depth and side slope, with water filled to the level the
 * annual runoff reaches (capped at full). Vertical scale is exaggerated and says so.
 */
export default function PondSection({ pond, fillPercent }) {
  const W = 360
  const H = 150
  const top = pond.side_m
  const depth = pond.depth_m
  const slope = pond.side_slope
  const bottom = Math.max(top - 2 * slope * depth, 0)

  const padX = 26
  const groundY = 34
  const avail = H - groundY - 26
  const sx = Math.min((W - padX * 2) / top, avail / depth) // small deep ponds shrink to fit
  const vex = Math.max(1, Math.min(12, avail / depth / sx)) // vertical exaggeration
  const sy = sx * vex
  const x0 = (W - top * sx) / 2
  const x1 = x0 + top * sx
  const bx0 = x0 + ((top - bottom) / 2) * sx
  const bx1 = bx0 + bottom * sx
  const by = groundY + depth * sy

  // water level: volume fraction -> height fraction (solve frustum numerically)
  const frac = Math.max(0, Math.min(1, (fillPercent ?? 0) / 100))
  const vol = (h) => {
    const b = bottom
    const t = b + 2 * slope * h
    return (h / 3) * (t * t + b * b + t * b)
  }
  const full = vol(depth)
  let lo = 0
  let hi = depth
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2
    if (vol(mid) < frac * full) lo = mid
    else hi = mid
  }
  const wh = hi
  const wy = by - wh * sy
  const wHalf = (bottom / 2 + slope * wh) * sx
  const cx = (x0 + x1) / 2

  const label = `Pond cross-section: ${fmt(top, 1)} m wide at the top, ${fmt(bottom, 1)} m at the bottom, ${fmt(depth, 1)} m deep, water filled to ${fmt(frac * 100)} percent of capacity.`

  return (
    <figure className="section-fig">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        <defs>
          <pattern id="soil" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="8" stroke="var(--soil-line)" strokeWidth="1.2" />
          </pattern>
          <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--water-light)" />
            <stop offset="1" stopColor="var(--water)" />
          </linearGradient>
        </defs>
        {/* soil around the excavation */}
        <path d={`M0 ${groundY} H${x0} L${bx0} ${by} H${bx1} L${x1} ${groundY} H${W} V${H} H0 Z`} fill="url(#soil)" />
        <path d={`M0 ${groundY} H${x0} L${bx0} ${by} H${bx1} L${x1} ${groundY} H${W}`} fill="none" stroke="var(--ink)" strokeWidth="1.5" />
        {/* water */}
        {wh > 0.001 && (
          <path d={`M${cx - wHalf} ${wy} L${bx0} ${by} H${bx1} L${cx + wHalf} ${wy} Z`} fill="url(#water)" opacity="0.92" />
        )}
        {wh > 0.001 && <line x1={cx - wHalf} x2={cx + wHalf} y1={wy} y2={wy} stroke="#fff" strokeWidth="1.2" />}
        {/* dimensions */}
        <g className="dim" fontSize="11">
          <line x1={x0} x2={x1} y1={groundY - 16} y2={groundY - 16} />
          <line x1={x0} x2={x0} y1={groundY - 21} y2={groundY - 11} />
          <line x1={x1} x2={x1} y1={groundY - 21} y2={groundY - 11} />
          <text x={cx} y={groundY - 20} textAnchor="middle">{fmt(top, 1)} m</text>
          <line x1={x1 + 12} x2={x1 + 12} y1={groundY} y2={by} />
          <line x1={x1 + 7} x2={x1 + 17} y1={by} y2={by} />
          <text x={x1 + 14} y={(groundY + by) / 2 + 4} textAnchor="start" className="dim-v">{fmt(depth, 1)} m</text>
          {bottom > 0 && <text x={cx} y={by + 14} textAnchor="middle">{fmt(bottom, 1)} m</text>}
          <text x={(x0 + bx0) / 2 - 6} y={(groundY + by) / 2 + 4} textAnchor="end">{slope}:1</text>
        </g>
      </svg>
      <figcaption>
        Section through the pond, vertical scale ×{fmt(vex, 0)}. Water shows the average year&apos;s runoff
        {frac >= 0.999 ? ' filling it completely.' : ` filling ${fmt(frac * 100)}% of it.`}
      </figcaption>
    </figure>
  )
}
