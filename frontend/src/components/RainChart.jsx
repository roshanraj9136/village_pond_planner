import { useState } from 'react'
import { fmt } from '../lib/format'

/**
 * Ten years of rainfall at the site, each bar split into the part that runs off to the
 * pond (dark) and the part that soaks in or evaporates (light). One axis, millimetres.
 */
export default function RainChart({ yearly }) {
  const [hover, setHover] = useState(null)
  if (!yearly?.length) return null
  const W = 360
  const H = 150
  const padL = 34
  const padR = 6
  const padT = 12
  const padB = 22
  const maxRain = Math.max(...yearly.map((y) => y.rain_mm))
  const step = maxRain > 1500 ? 500 : 250
  const top = Math.ceil(maxRain / step) * step
  const plotH = H - padT - padB
  const slot = (W - padL - padR) / yearly.length
  const bw = Math.min(22, slot - 6)
  const y = (v) => padT + plotH - (v / top) * plotH
  const ticks = []
  for (let v = 0; v <= top; v += step) ticks.push(v)
  const avgRain = yearly.reduce((s, r) => s + r.rain_mm, 0) / yearly.length
  const avgRun = yearly.reduce((s, r) => s + r.runoff_mm, 0) / yearly.length
  const h = hover != null ? yearly[hover] : null

  return (
    <figure className="chart">
      <div className="chart-legend" aria-hidden="true">
        <span><i style={{ background: 'var(--series-runoff)' }} />Runoff to the pond</span>
        <span><i style={{ background: 'var(--series-rain)' }} />Soaks in or evaporates</span>
      </div>
      <div className="chart-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`Annual rainfall 2015 to 2024 averages ${fmt(avgRain)} millimetres, of which ${fmt(avgRun)} millimetres runs off.`}
          onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid" />
              <text x={padL - 5} y={y(t) + 3.5} textAnchor="end" className="axis">{fmt(t)}</text>
            </g>
          ))}
          {yearly.map((r, i) => {
            const x = padL + i * slot + (slot - bw) / 2
            const yr = y(r.rain_mm)
            const yq = y(r.runoff_mm)
            const base = y(0)
            return (
              <g key={r.year} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                aria-label={`${r.year}: ${fmt(r.rain_mm)} mm rain, ${fmt(r.runoff_mm)} mm runoff`}>
                <rect x={padL + i * slot} y={padT} width={slot} height={plotH} fill="transparent" />
                <path d={roundTop(x, yr, bw, Math.max(0, yq - yr - 2))} fill="var(--series-rain)" opacity={hover == null || hover === i ? 1 : 0.45} />
                <rect x={x} y={yq} width={bw} height={Math.max(0, base - yq)} fill="var(--series-runoff)" opacity={hover == null || hover === i ? 1 : 0.45} />
                <text x={x + bw / 2} y={H - 7} textAnchor="middle" className="axis">{`'${String(r.year).slice(2)}`}</text>
              </g>
            )
          })}
          <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} className="baseline" />
          <text x={padL - 5} y={padT - 2} textAnchor="end" className="axis">mm</text>
        </svg>
        {h && (
          <div className="chart-tip" style={{ left: `${((padL + hover * slot + slot / 2) / W) * 100}%` }} role="status">
            <strong>{h.year}</strong>
            <span>{fmt(h.rain_mm)} mm rain</span>
            <span>{fmt(h.runoff_mm)} mm runoff ({fmt((100 * h.runoff_mm) / h.rain_mm)}%)</span>
          </div>
        )}
      </div>
      <details className="table-view">
        <summary>Show as a table</summary>
        <table>
          <thead><tr><th scope="col">Year</th><th scope="col">Rain (mm)</th><th scope="col">Runoff (mm)</th></tr></thead>
          <tbody>
            {yearly.map((r) => (
              <tr key={r.year}><td>{r.year}</td><td>{fmt(r.rain_mm)}</td><td>{fmt(r.runoff_mm)}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}

function roundTop(x, yTop, w, h) {
  const r = Math.min(4, h, w / 2)
  return `M${x} ${yTop + h} V${yTop + r} Q${x} ${yTop} ${x + r} ${yTop} H${x + w - r} Q${x + w} ${yTop} ${x + w} ${yTop + r} V${yTop + h} Z`
}
