import { useEffect, useRef } from 'react'
import Icon from './Icons'

const steps = [
  ['Terrain', 'Copernicus 30 m satellite elevation, or a grid interpolated from your contour map'],
  ['Fill pits', 'Priority-Flood raises every hollow just enough to spill, so water can always move on'],
  ['Flow', 'D8: each cell sends its water to the steepest of its 8 neighbours'],
  ['Accumulate', 'Visiting cells from high to low adds up how much land drains through each one'],
  ['Pond site', 'The point inside your land where the most water arrives'],
  ['Catchment', 'Every cell whose water path passes through the pond site'],
  ['Water', 'SCS curve-number runoff on 10 years of daily rainfall, times the catchment area'],
  ['Pond size', 'A 1.5 : 1 sloped pond that holds the yearly runoff, within your land'],
]

/** The analysis pipeline as a diagram, for the "How it works" dialog. */
function Pipeline() {
  const W = 640
  const rowH = 58
  const H = steps.length * rowH + 10
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="pipeline" role="img"
      aria-label="Analysis pipeline: terrain, fill pits, flow directions, flow accumulation, pond site, catchment, water volume, pond size.">
      {steps.map(([title, text], i) => {
        const yy = 8 + i * rowH
        return (
          <g key={title}>
            {i < steps.length - 1 && <line x1="22" x2="22" y1={yy + 36} y2={yy + rowH + 2} className="pipe" />}
            <circle cx="22" cy={yy + 18} r="15" className={i === 4 ? 'node node-key' : 'node'} />
            <text x="22" y={yy + 23} textAnchor="middle" className="node-n">{i + 1}</text>
            <text x="50" y={yy + 15} className="step-title">{title}</text>
            <text x="50" y={yy + 35} className="step-text">{text}</text>
          </g>
        )
      })}
    </svg>
  )
}

export default function HowItWorks({ open, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-labelledby="how-title">
      <header>
        <h2 id="how-title">How the pond site is found</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
      </header>
      <Pipeline />
      <p className="muted">
        Water follows the ground. The planner rebuilds the ground as an elevation grid, traces where every
        drop of rain would flow, and puts the pond where the most water already arrives inside the land you
        selected. The catchment is all the land upstream of that point, which usually reaches beyond your
        parcel.
      </p>
    </dialog>
  )
}
