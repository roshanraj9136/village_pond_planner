import { useEffect, useState } from 'react'
import Icon from './Icons'
import PondSection from './PondSection'
import RainChart from './RainChart'
import { placeName } from '../lib/api'
import { acres, fmt, fmtArea, fmtCoord, fmtLitres } from '../lib/format'

function downloadGeoJSON(result, parcel) {
  const feats = []
  const add = (geometry, properties) => geometry && feats.push({ type: 'Feature', geometry, properties })
  if (parcel) add({ type: 'Polygon', coordinates: [[...parcel, parcel[0]]] }, { layer: 'selected_land', area_m2: result.selected_area?.area_m2 })
  add({ type: 'Point', coordinates: [result.pond.lng, result.pond.lat] }, {
    layer: 'pond_site', elevation_m: result.pond.elevation_m, storage_capacity_m3: result.pond.storage_capacity_m3,
    depth_m: result.pond.depth_m, surface_area_m2: result.pond.surface_area_m2,
  })
  add(result.pond.footprint_geojson, { layer: 'pond_footprint' })
  add(result.catchment.geojson, { layer: 'catchment', area_m2: result.catchment.area_m2 })
  for (const f of result.layers?.streams?.features || []) add(f.geometry, { layer: 'flow_path', ...f.properties })
  const blob = new Blob([JSON.stringify({ type: 'FeatureCollection', features: feats })], { type: 'application/geo+json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `jaldrishti-pond-${result.pond.lat.toFixed(4)}-${result.pond.lng.toFixed(4)}.geojson`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

export default function Results({ result, meta, parcel, onZoomPond, onSave, saving }) {
  const { pond, catchment, water, source } = result
  const [name, setName] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let live = true
    placeName(pond.lat, pond.lng).then((n) => live && setName(n ? `Pond near ${n}` : 'Village pond'))
    return () => {
      live = false
    }
  }, [pond.lat, pond.lng])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${pond.lat.toFixed(6)}, ${pond.lng.toFixed(6)}`)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  const surplus = water.harvestable_runoff_m3 - pond.storage_capacity_m3

  return (
    <section className="results" aria-labelledby="results-title">
      <h2 id="results-title" className="sr-only">Results</h2>

      <div className="headline">
        <div className="hl hl-pond">
          <span className="hl-label">Pond location</span>
          <span className="hl-value coord">{fmtCoord(pond.lat)}° N, {fmtCoord(pond.lng)}° E</span>
          <span className="hl-sub">Ground level {fmt(pond.elevation_m, 1)} m above sea level</span>
          <div className="hl-actions">
            <button className="link-btn" onClick={onZoomPond}><Icon name="target" size={16} />Show on map</button>
            <button className="link-btn" onClick={copy}><Icon name={copied ? 'check' : 'copy'} size={16} />{copied ? 'Copied' : 'Copy'}</button>
          </div>
        </div>
        <div className="hl">
          <span className="hl-label">Catchment area</span>
          <span className="hl-value">{fmtArea(catchment.area_m2)}</span>
          <span className="hl-sub">{fmt(acres(catchment.area_m2), 1)} acres drain to the pond</span>
        </div>
        <div className="hl hl-water">
          <span className="hl-label">Water collected per year</span>
          <span className="hl-value">{fmt(water.collectable_volume_m3)} m³</span>
          <span className="hl-sub">{fmtLitres(water.collectable_volume_m3)} in an average year</span>
        </div>
      </div>

      <p className="why"><Icon name="drop" size={18} />{pond.reason}.</p>

      <h3>Suggested pond</h3>
      <PondSection pond={pond} fillPercent={water.pond_fill_percent} />
      <dl className="facts">
        <div><dt>Surface area</dt><dd>{fmtArea(pond.surface_area_m2)} ({fmt(pond.side_m, 0)} m × {fmt(pond.side_m, 0)} m)</dd></div>
        <div><dt>Depth</dt><dd>{fmt(pond.depth_m, 1)} m, sides sloped {pond.side_slope} : 1</dd></div>
        <div><dt>Storage capacity</dt><dd>{fmt(pond.storage_capacity_m3)} m³</dd></div>
        <div><dt>Runoff reaching it</dt><dd>{fmt(water.harvestable_runoff_m3)} m³ a year</dd></div>
        {surplus > 1 && <div><dt>Overflow</dt><dd>{fmt(surplus)} m³ a year leaves by the spillway</dd></div>}
      </dl>

      <h3>Rain and runoff at this site</h3>
      <dl className="facts">
        <div><dt>Rainfall</dt><dd>{fmt(water.annual_rainfall_mm)} mm a year ({fmt(water.monsoon_rainfall_mm)} mm in Jun–Sep)</dd></div>
        <div><dt>Runs off</dt><dd>{fmt(water.runoff_depth_mm)} mm, {fmt(water.runoff_coefficient * 100)}% of the rain</dd></div>
        <div><dt>Land cover</dt><dd>Curve number {water.curve_number}</dd></div>
      </dl>
      <RainChart yearly={water.yearly} />
      <p className="source-note">{water.rainfall_source}{water.period ? `, ${water.period}` : ''}. {water.method}.</p>

      <h3>Catchment and terrain</h3>
      <dl className="facts">
        <div><dt>Slope</dt><dd>{fmt(catchment.mean_slope_pct, 1)}% on average ({catchment.terrain.toLowerCase()})</dd></div>
        <div><dt>Height range</dt><dd>{fmt(catchment.relief_m, 1)} m within the catchment</dd></div>
        {result.selected_area && <div><dt>Your land</dt><dd>{fmtArea(result.selected_area.area_m2)} ({fmt(acres(result.selected_area.area_m2), 2)} acres)</dd></div>}
        <div><dt>Terrain data</dt><dd>{source.name}, {source.resolution}</dd></div>
      </dl>

      {result.notes?.length > 0 && (
        <ul className="notes">
          {result.notes.map((n) => (
            <li key={n}><Icon name="warning" size={18} /><span>{n}</span></li>
          ))}
        </ul>
      )}

      <form className="save" onSubmit={(e) => { e.preventDefault(); onSave(name.trim() || 'Village pond') }}>
        <label htmlFor="site-name">Save this site for others to see</label>
        <div className="save-row">
          <input id="site-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Site name" />
          <button className="btn" type="submit" disabled={saving}><Icon name="save" size={18} />{saving ? 'Saving…' : 'Save site'}</button>
        </div>
      </form>
      <button className="btn btn-quiet wide" onClick={() => downloadGeoJSON(result, parcel)}>
        <Icon name="download" size={18} />Download map layers (GeoJSON)
      </button>

      <p className="runinfo">
        Computed by {meta?.worker || result.served_by} in {fmt(result.timings_ms.total)} ms
        {meta?.cache === 'HIT' || meta?.cache === 'SHARED' ? ', answered from the gateway cache' : ''}.
      </p>
    </section>
  )
}
