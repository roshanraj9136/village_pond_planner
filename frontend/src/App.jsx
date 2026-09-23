import { useCallback, useEffect, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import './styles.css'
import Icon from './components/Icons'
import Logo from './components/Logo'
import MapView from './components/MapView'
import { BASES, LAYERS } from './components/mapConfig'
import Results from './components/Results'
import HowItWorks from './components/HowItWorks'
import StatusPage from './components/StatusPage'
import { analyzeContour, analyzeParcel, coverage as fetchCoverage, deleteSite, listSites, saveSite, searchPlace } from './lib/api'
import { acres, boundsOf, fmt, fmtArea, ringArea, toPolygon } from './lib/format'

const REPO = 'https://github.com/roshanraj9136/village_pond_planner'

const LAND_COVER = [
  { cn: 80, label: 'Farmland (kharif crops)' },
  { cn: 74, label: 'Grassland or pasture' },
  { cn: 70, label: 'Forest or dense scrub' },
  { cn: 88, label: 'Fallow or rocky land' },
  { cn: 90, label: 'Village or built-up area' },
]

const inCoverage = (ring, tiles) =>
  ring.every(([lng, lat]) => tiles.some((t) => lat >= t.south && lat < t.north && lng >= t.west && lng < t.east))

export default function App() {
  if (window.location.pathname.replace(/\/+$/, '') === '/status') return <StatusPage />
  return <Planner />
}

function Planner() {
  const [source, setSource] = useState('satellite')
  const [base, setBase] = useState('satellite')
  const [drawMode, setDrawMode] = useState(null)
  const [parcel, setParcel] = useState(null)
  const [contour, setContour] = useState(null) // { file } | { sample: true }
  const [contourPreview, setContourPreview] = useState(null)
  const [params, setParams] = useState({ pond_depth_m: 3, curve_number: 80, max_pond_area_m2: 50000 })
  const [result, setResult] = useState(null)
  const [meta, setMeta] = useState(null)
  const [resultKey, setResultKey] = useState(null)
  const [busy, setBusy] = useState(false)
  const [slow, setSlow] = useState(false)
  const [retrying, setRetrying] = useState(0)
  const [error, setError] = useState(null)
  const [visible, setVisible] = useState({ parcel: true, pond: true, catchment: true, streams: true, contours: true })
  const [focus, setFocus] = useState(null)
  const [sites, setSites] = useState([])
  const [sitesNote, setSitesNote] = useState(null)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)
  const [tiles, setTiles] = useState([])
  const [howOpen, setHowOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [version, setVersion] = useState(0)
  const abortRef = useRef(null)
  const fileRef = useRef(null)

  const refreshSites = useCallback(
    () =>
      listSites()
        .then(({ body }) => {
          setSites(body.sites || [])
          setSitesNote(null)
        })
        .catch((e) => setSitesNote(e.status === 503 ? 'Saved sites are unavailable right now.' : e.message)),
    [],
  )

  useEffect(() => {
    refreshSites()
    fetchCoverage()
      .then(({ body }) => setTiles(body.tiles || []))
      .catch(() => setTiles([]))
  }, [refreshSites])

  useEffect(() => {
    if (!toast) return undefined
    const t = setTimeout(() => setToast(null), 3500)
    return () => clearTimeout(t)
  }, [toast])

  const currentKey = JSON.stringify([source, parcel, params, contour?.file?.name, contour?.file?.size, !!contour?.sample])
  const stale = result && resultKey !== currentKey

  const run = async ({ contourInput = contour, area = parcel, fit = true } = {}) => {
    abortRef.current?.abort()
    const ctl = new AbortController()
    abortRef.current = ctl
    setBusy(true)
    setError(null)
    setSlow(false)
    setRetrying(0)
    const slowTimer = setTimeout(() => setSlow(true), 5000)
    try {
      const { body, meta: m } =
        source === 'satellite'
          ? await analyzeParcel(toPolygon(area), params, ctl.signal, setRetrying)
          : await analyzeContour(
              { file: contourInput?.file, useSample: !!contourInput?.sample, area: area ? toPolygon(area) : null, params },
              ctl.signal,
              setRetrying,
            )
      setResult(body)
      setMeta(m)
      setVersion((v) => v + 1)
      setResultKey(JSON.stringify([source, area, params, contourInput?.file?.name, contourInput?.file?.size, !!contourInput?.sample]))
      if (body.layers?.contours && source === 'contour' && !area) setContourPreview(body.layers.contours)
      if (fit) {
        const b = boundsOf([body.catchment?.geojson, body.pond?.footprint_geojson, body.selected_area?.geojson])
        if (b) setFocus({ bounds: b })
      }
      setPanelOpen(true)
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      clearTimeout(slowTimer)
      if (abortRef.current === ctl) {
        setBusy(false)
        setSlow(false)
      }
    }
  }

  const cancel = () => {
    abortRef.current?.abort()
    setBusy(false)
    setSlow(false)
  }

  const chooseSource = (s) => {
    if (s === source) return
    cancel()
    setSource(s)
    setResult(null)
    setError(null)
    setContour(null)
    setContourPreview(null)
    setDrawMode(null)
  }

  const narrow = () => window.matchMedia('(max-width: 820px)').matches

  const startDraw = (mode) => {
    const next = drawMode === mode ? null : mode
    setDrawMode(next)
    if (next && narrow()) setPanelOpen(false) // the map needs the whole screen to draw on a phone
  }

  const onDrawDone = (ring) => {
    setDrawMode(null)
    setParcel(ring)
    setError(null)
    if (narrow()) setPanelOpen(true)
  }

  const loadContour = (input) => {
    setContour(input)
    setParcel(null)
    setResult(null)
    setContourPreview(null)
    run({ contourInput: input, area: null })
  }

  const onFile = (file) => {
    if (!file) return
    const ok = /\.(kml|kmz)$/i.test(file.name)
    if (!ok) {
      setError('Choose a .kml or .kmz contour map.')
      return
    }
    loadContour({ file })
  }

  const onSave = async (name) => {
    setSaving(true)
    try {
      await saveSite({
        name,
        lat: result.pond.lat,
        lng: result.pond.lng,
        source_type: result.source.type,
        parcel_ha: result.selected_area ? result.selected_area.area_ha : null,
        catchment_ha: result.catchment.area_ha,
        runoff_m3: result.water.harvestable_runoff_m3,
        capacity_m3: result.pond.storage_capacity_m3,
        collectable_m3: result.water.collectable_volume_m3,
        pond_area_m2: result.pond.surface_area_m2,
        depth_m: result.pond.depth_m,
        area_geojson: result.selected_area?.geojson || null,
      })
      setToast(`Saved "${name}"`)
      refreshSites()
    } catch (e) {
      setToast(e.message)
    } finally {
      setSaving(false)
    }
  }

  const onDelete = async (site) => {
    try {
      await deleteSite(site.id)
      setToast(`Removed "${site.name}"`)
      refreshSites()
    } catch (e) {
      setToast(e.message)
    }
  }

  const openSite = (site) => {
    const ring = site.area_geojson?.coordinates?.[0]
    if (ring && source === 'satellite') {
      setParcel(ring.slice(0, -1))
      setResult(null)
    }
    setFocus({ bounds: boundsOf([site.area_geojson || { coordinates: [site.lng, site.lat] }]), maxZoom: 16 })
  }

  const parcelArea = parcel ? ringArea(parcel) : 0
  const canRun = source === 'satellite' ? !!parcel : !!contour
  const outside = source === 'satellite' && parcel && tiles.length > 0 && !inCoverage(parcel, tiles)
  const runLabel = result && !stale ? 'Analysis is up to date' : result ? 'Update results' : 'Find the pond site'

  return (
    <div className={`app ${panelOpen ? '' : 'panel-closed'}`}>
      <aside className="panel" aria-label="Pond planner">
        <div className="panel-top">
          <Logo />
          <button className="icon-btn only-mobile" onClick={() => setPanelOpen((v) => !v)} aria-label={panelOpen ? 'Show map' : 'Show panel'}>
            <Icon name={panelOpen ? 'close' : 'menu'} />
          </button>
        </div>

        <div className="panel-body">
          <PlaceSearch onPick={(b) => setFocus({ bounds: b, maxZoom: 16 })} />

          <ol className="steps">
            <li className="step">
              <h2><span className="n">1</span>Terrain data</h2>
              <div className="segmented" role="radiogroup" aria-label="Terrain data">
                <button role="radio" aria-checked={source === 'satellite'} onClick={() => chooseSource('satellite')}>Satellite elevation</button>
                <button role="radio" aria-checked={source === 'contour'} onClick={() => chooseSource('contour')}>Contour map file</button>
              </div>
              {source === 'satellite' ? (
                <p className="hint">
                  30 m Copernicus elevation works anywhere in India. The Durg–Bhilai–Raipur region is pre-loaded for instant
                  results; elsewhere the first analysis downloads the terrain (about a minute).
                </p>
              ) : (
                <div className="contour-pick">
                  <input ref={fileRef} type="file" accept=".kml,.kmz" hidden onChange={(e) => onFile(e.target.files?.[0])} />
                  <button className="dropzone" onClick={() => fileRef.current?.click()}
                    onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]) }}>
                    <Icon name="upload" size={22} />
                    <span>{contour?.file ? contour.file.name : 'Upload a contour map (.kml or .kmz)'}</span>
                    <small>Drop the file here or click to choose</small>
                  </button>
                  <div className="row">
                    <button className="btn btn-quiet" onClick={() => loadContour({ sample: true })}>
                      <Icon name="file" size={18} />Use the sample map
                    </button>
                    <a className="link-btn" href="/api/sample/contour_map" download>
                      <Icon name="download" size={16} />contours_1m.kml
                    </a>
                  </div>
                </div>
              )}
            </li>

            <li className="step">
              <h2><span className="n">2</span>Your land</h2>
              <p className="hint">
                {source === 'satellite'
                  ? 'Mark the land where a pond can be dug, such as panchayat or government land.'
                  : 'Optional: mark your land inside the contour map to keep the pond within it.'}
              </p>
              <div className="row">
                <button className={`btn ${drawMode === 'polygon' ? 'active' : 'btn-quiet'}`} aria-pressed={drawMode === 'polygon'}
                  onClick={() => startDraw('polygon')}>
                  <Icon name="polygon" size={18} />Draw outline
                </button>
                <button className={`btn ${drawMode === 'rectangle' ? 'active' : 'btn-quiet'}`} aria-pressed={drawMode === 'rectangle'}
                  onClick={() => startDraw('rectangle')}>
                  <Icon name="rectangle" size={18} />Rectangle
                </button>
                {parcel && (
                  <button className="btn btn-quiet" onClick={() => { setParcel(null); setDrawMode(null) }}>
                    <Icon name="clear" size={18} />Clear
                  </button>
                )}
              </div>
              {parcel && (
                <p className="area-read">
                  <strong>{fmtArea(parcelArea)}</strong> selected ({fmt(acres(parcelArea), 2)} acres)
                </p>
              )}
              {outside && <p className="hint warn"><Icon name="info" size={16} />Outside the pre-loaded region: expect about a minute for the first run.</p>}
            </li>

            <li className="step">
              <h2><span className="n">3</span>Pond design</h2>
              <label className="field">
                <span>Pond depth <output>{params.pond_depth_m} m</output></span>
                <input type="range" min="1.5" max="5" step="0.5" value={params.pond_depth_m}
                  onChange={(e) => setParams({ ...params, pond_depth_m: Number(e.target.value) })} />
              </label>
              <label className="field">
                <span>Land cover in the catchment</span>
                <select value={params.curve_number} onChange={(e) => setParams({ ...params, curve_number: Number(e.target.value) })}>
                  {LAND_COVER.map((o) => <option key={o.cn} value={o.cn}>{o.label} (CN {o.cn})</option>)}
                </select>
              </label>
              <label className="field">
                <span>Largest pond to suggest <output>{fmt(params.max_pond_area_m2 / 10000, 1)} ha</output></span>
                <input type="range" min="5000" max="100000" step="5000" value={params.max_pond_area_m2}
                  onChange={(e) => setParams({ ...params, max_pond_area_m2: Number(e.target.value) })} />
              </label>
            </li>

            <li className="step">
              <h2><span className="n">4</span>Results</h2>
              {busy ? (
                <div className="progress" role="status">
                  <span className="spinner" aria-hidden="true" />
                  <span>
                    {retrying
                      ? `The servers are busy. Trying again (${retrying} of 4)…`
                      : slow
                        ? source === 'satellite' ? 'Downloading terrain for a new region. This happens once per region.' : 'Still working on the contour map…'
                        : 'Tracing where the water flows…'}
                  </span>
                  <button className="link-btn" onClick={cancel}>Cancel</button>
                </div>
              ) : (
                <button className="btn btn-primary wide" disabled={!canRun || (result && !stale)} onClick={() => run()}>
                  <Icon name="drop" size={18} />{runLabel}
                </button>
              )}
              {!canRun && !busy && (
                <p className="hint">{source === 'satellite' ? 'Draw your land on the map to begin.' : 'Load a contour map to begin.'}</p>
              )}
              {error && <p className="banner error" role="alert"><Icon name="warning" size={18} />{error}</p>}
              {stale && !busy && <p className="hint">Inputs changed since this result. Update to recalculate.</p>}
            </li>
          </ol>

          {result && <Results key={`${result.pond.lat},${result.pond.lng}`} result={result} meta={meta} parcel={parcel}
            onZoomPond={() => setFocus({ bounds: boundsOf([result.pond.footprint_geojson]) || [[result.pond.lat, result.pond.lng], [result.pond.lat, result.pond.lng]], maxZoom: 18 })}
            onSave={onSave} saving={saving} />}

          <section className="saved" aria-labelledby="saved-title">
            <h2 id="saved-title">Saved sites</h2>
            {sitesNote && <p className="hint">{sitesNote}</p>}
            {!sitesNote && sites.length === 0 && <p className="hint">No sites yet. Saved results appear here for everyone.</p>}
            <ul>
              {sites.map((s) => (
                <li key={s.id}>
                  <button className="site" onClick={() => openSite(s)}>
                    <Icon name="pin" size={18} />
                    <span><strong>{s.name}</strong><small>{fmt(s.collectable_m3)} m³/yr, catchment {fmt(s.catchment_ha, 1)} ha</small></span>
                  </button>
                  <button className="icon-btn" onClick={() => onDelete(s)} aria-label={`Delete ${s.name}`}><Icon name="clear" size={18} /></button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <footer className="panel-foot">
          <button className="link-btn" onClick={() => setHowOpen(true)}><Icon name="info" size={16} />How it works</button>
          <a className="link-btn" href="/docs" target="_blank" rel="noreferrer"><Icon name="book" size={16} />API docs</a>
          <a className="link-btn" href="/status"><Icon name="pulse" size={16} />System status</a>
          <a className="link-btn" href={REPO} target="_blank" rel="noreferrer"><Icon name="code" size={16} />Source</a>
        </footer>
      </aside>

      <main className="map-wrap" aria-label="Map">
        <MapView base={base} drawMode={drawMode} onDrawDone={onDrawDone} onDrawCancel={() => setDrawMode(null)}
          parcel={parcel} result={result} contourPreview={contourPreview} visible={visible} focus={focus}
          sites={sites} onSiteOpen={openSite} coverage={source === 'satellite' ? tiles : []} version={version} />

        <div className="map-bases" role="radiogroup" aria-label="Base map">
          {Object.entries(BASES).map(([id, b]) => (
            <button key={id} role="radio" aria-checked={base === id} onClick={() => setBase(id)}>{b.label}</button>
          ))}
        </div>

        {drawMode && (
          <div className="draw-hint" role="status">
            {drawMode === 'polygon'
              ? 'Click to add corners. Click the first corner, double-click or press Enter to finish. Backspace removes the last corner, Esc cancels.'
              : 'Click one corner, then the opposite corner. Esc cancels.'}
          </div>
        )}

        {(result || contourPreview || parcel) && (
          <div className="legend" aria-label="Map layers">
            {LAYERS.filter((l) => (l.id === 'parcel' ? parcel : l.id === 'contours' ? result || contourPreview : result)).map((l) => (
              <label key={l.id}>
                <input type="checkbox" checked={visible[l.id]} onChange={(e) => setVisible({ ...visible, [l.id]: e.target.checked })} />
                <Swatch kind={l.swatch} />
                {l.label}
              </label>
            ))}
          </div>
        )}

        {!panelOpen && (
          <button className="btn btn-primary show-panel only-mobile" onClick={() => setPanelOpen(true)}>
            <Icon name="sliders" size={18} />Planner
          </button>
        )}
        {toast && <div className="toast" role="status">{toast}</div>}
      </main>

      <HowItWorks open={howOpen} onClose={() => setHowOpen(false)} />
    </div>
  )
}

function Swatch({ kind }) {
  const common = { width: 26, height: 14, viewBox: '0 0 26 14', 'aria-hidden': true }
  switch (kind) {
    case 'parcel':
      return <svg {...common}><rect x="1.5" y="1.5" width="23" height="11" fill="#E8A317" fillOpacity="0.15" stroke="#E8A317" strokeWidth="2" strokeDasharray="4 3" /></svg>
    case 'pond':
      return <svg {...common}><rect x="4" y="2" width="18" height="10" rx="2" fill="#0B4F94" stroke="#fff" strokeWidth="1.2" /></svg>
    case 'catchment':
      return <svg {...common}><rect x="1.5" y="1.5" width="23" height="11" fill="#1766B5" fillOpacity="0.45" stroke="#A8D8FF" strokeWidth="1.5" /></svg>
    case 'stream':
      return <svg {...common}><path d="M1 11c6-1 7-8 13-8s7 3 11 2" fill="none" stroke="#8FD8FF" strokeWidth="2.4" strokeLinecap="round" /></svg>
    default:
      return <svg {...common}><path d="M1 10c5-5 10 1 13-2s6-6 11-4M1 13c6-3 11 1 14-1s6-4 10-3" fill="none" stroke="#F2B98A" strokeWidth="1.3" /></svg>
  }
}

function PlaceSearch({ onPick }) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState([])
  const [msg, setMsg] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    if (!q.trim()) return
    setMsg('Searching…')
    try {
      const res = await searchPlace(q.trim())
      setItems(res)
      setMsg(res.length ? null : 'No place found. Try the village with its district name.')
    } catch (err) {
      setMsg(err.message)
    }
  }
  const pick = (it) => {
    const [s, n, w, ea] = it.boundingbox.map(Number)
    onPick([[s, w], [n, ea]])
    setItems([])
    setQ(it.display_name.split(',')[0])
  }
  return (
    <form className="search" role="search" onSubmit={submit}>
      <label htmlFor="place" className="sr-only">Find a village or town</label>
      <Icon name="search" size={18} />
      <input id="place" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a village or town" autoComplete="off" />
      <button className="btn btn-quiet" type="submit">Go</button>
      {(items.length > 0 || msg) && (
        <div className="search-results">
          {msg && <p className="hint">{msg}</p>}
          {items.map((it) => (
            <button type="button" key={it.place_id} onClick={() => pick(it)}>
              <strong>{it.display_name.split(',')[0]}</strong>
              <small>{it.display_name.split(',').slice(1, 4).join(',')}</small>
            </button>
          ))}
        </div>
      )}
    </form>
  )
}
