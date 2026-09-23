import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import {
  CircleMarker, GeoJSON, MapContainer, Marker, Pane, Polygon, Polyline, Rectangle, TileLayer, Tooltip,
  ZoomControl, useMap, useMapEvents,
} from 'react-leaflet'
import { fmt, fmtArea, ringArea } from '../lib/format'
import { BASES } from './mapConfig'

const pondIcon = L.divIcon({
  className: 'pond-pin',
  iconSize: [34, 44],
  iconAnchor: [17, 42],
  html: `<svg viewBox="0 0 34 44" width="34" height="44" aria-hidden="true">
    <path d="M17 42C17 42 31 26.5 31 17A14 14 0 0 0 3 17C3 26.5 17 42 17 42Z" fill="#0B4F94" stroke="#fff" stroke-width="2"/>
    <path d="M17 8.5s6 6.3 6 10.3a6 6 0 0 1-12 0c0-4 6-10.3 6-10.3Z" fill="#fff"/>
  </svg>`,
})

function FitTo({ focus }) {
  const map = useMap()
  useEffect(() => {
    if (!focus?.bounds) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    map.fitBounds(focus.bounds, { padding: [48, 48], maxZoom: focus.maxZoom ?? 17, animate: !reduce })
  }, [focus, map])
  return null
}

function DrawTool({ mode, onDone, onCancel }) {
  const map = useMap()
  const [pts, setPts] = useState([])
  const [cursor, setCursor] = useState(null)
  const ptsRef = useRef(pts)
  useEffect(() => {
    ptsRef.current = pts
  }, [pts])

  useEffect(() => {
    const el = map.getContainer()
    if (mode) {
      el.classList.add('drawing')
      map.doubleClickZoom.disable()
    }
    return () => {
      el.classList.remove('drawing')
      map.doubleClickZoom.enable()
    }
  }, [mode, map])

  const finishPolygon = (list) => {
    if (list.length >= 3) onDone(list.map((p) => [p.lng, p.lat]))
  }

  useEffect(() => {
    if (!mode) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') onCancel()
      else if (e.key === 'Enter' && mode === 'polygon') finishPolygon(ptsRef.current)
      else if (e.key === 'Backspace' && mode === 'polygon') setPts((p) => p.slice(0, -1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps -- handlers read the latest points via ptsRef

  useMapEvents({
    click(e) {
      if (!mode) return
      const p = e.latlng
      if (mode === 'rectangle') {
        if (!pts.length) setPts([p])
        else {
          const a = pts[0]
          onDone([[a.lng, a.lat], [p.lng, a.lat], [p.lng, p.lat], [a.lng, p.lat]])
        }
        return
      }
      if (pts.length >= 3) {
        const first = map.latLngToContainerPoint(pts[0])
        if (first.distanceTo(e.containerPoint) < 12) return finishPolygon(pts)
      }
      if (pts.length) {
        const last = map.latLngToContainerPoint(pts[pts.length - 1])
        if (last.distanceTo(e.containerPoint) < 5) return // second click of a double-click
      }
      setPts([...pts, p])
    },
    dblclick() {
      if (mode === 'polygon') finishPolygon(pts)
    },
    mousemove(e) {
      if (mode) setCursor(e.latlng)
    },
  })

  if (!mode || !pts.length) return null
  if (mode === 'rectangle') {
    const b = cursor ? [pts[0], cursor] : null
    return b ? <Rectangle bounds={b} pathOptions={{ className: 'draft', color: '#E8A317', weight: 2, dashArray: '6 5', fillOpacity: 0.1 }} interactive={false} /> : null
  }
  const line = cursor ? [...pts, cursor] : pts
  const ring = line.map((p) => [p.lng, p.lat])
  return (
    <>
      <Polyline positions={line} pathOptions={{ color: '#E8A317', weight: 2.5, dashArray: '6 5' }} interactive={false} />
      {pts.map((p, i) => (
        <CircleMarker key={i} center={p} radius={i === 0 ? 7 : 5} interactive={false}
          pathOptions={{ color: '#fff', weight: 2, fillColor: '#E8A317', fillOpacity: 1 }} />
      ))}
      {cursor && pts.length >= 2 && (
        <CircleMarker center={cursor} radius={0} interactive={false} pathOptions={{ opacity: 0 }}>
          <Tooltip permanent direction="right" offset={[10, 0]} className="draw-tip">{fmtArea(ringArea(ring))}</Tooltip>
        </CircleMarker>
      )}
    </>
  )
}

function contourStyle(base) {
  const onDark = base === 'satellite'
  return (f) => ({
    color: onDark ? '#F2B98A' : '#B8643A',
    weight: f.properties.index ? 1.8 : 0.9,
    opacity: onDark ? 0.9 : 0.85,
  })
}

function streamStyle(f) {
  const ha = f.properties.upstream_ha || 0
  return {
    color: '#8FD8FF',
    weight: Math.min(4.5, 1 + Math.log10(1 + ha) * 1.2),
    opacity: f.properties.in_catchment ? 0.95 : 0.4,
    lineCap: 'round',
  }
}

export default function MapView({
  base, drawMode, onDrawDone, onDrawCancel, parcel, result, contourPreview, visible, focus, sites, onSiteOpen,
  coverage, version,
}) {
  const b = BASES[base]
  const layers = result?.layers
  const contours = layers?.contours || contourPreview
  // GeoJSON layers are immutable in react-leaflet; `version` (bumped by App on new data) remounts them
  const stamp = version

  return (
    <MapContainer center={[21.2635, 81.2960]} zoom={15} zoomControl={false} className="map" preferCanvas={false}>
      <TileLayer key={base} url={b.url} attribution={b.attribution} maxNativeZoom={b.maxNativeZoom} maxZoom={20} />
      {b.labels && <TileLayer url={b.labels} maxNativeZoom={19} maxZoom={20} opacity={0.85} />}
      <ZoomControl position="bottomright" />
      <FitTo focus={focus} />

      <Pane name="contours" style={{ zIndex: 405 }}>
        {visible.contours && contours?.features?.length > 0 && (
          <GeoJSON key={`c-${stamp}-${base}`} data={contours} style={contourStyle(base)} interactive
            onEachFeature={(f, l) => l.bindTooltip(`${fmt(f.properties.elev, 1)} m`, { sticky: true, className: 'map-tip' })} />
        )}
      </Pane>
      <Pane name="catchment" style={{ zIndex: 410 }}>
        {visible.catchment && result?.catchment?.geojson && (
          <GeoJSON key={`k-${stamp}`} data={result.catchment.geojson} interactive={false}
            style={{ color: '#A8D8FF', weight: 2, fillColor: '#1766B5', fillOpacity: 0.3 }} />
        )}
      </Pane>
      <Pane name="streams" style={{ zIndex: 420 }}>
        {visible.streams && layers?.streams?.features?.length > 0 && (
          <GeoJSON key={`s-${stamp}`} data={layers.streams} style={streamStyle} interactive={false} />
        )}
      </Pane>
      <Pane name="parcel" style={{ zIndex: 430 }}>
        {visible.parcel && parcel && (
          <Polygon positions={parcel.map(([lng, lat]) => [lat, lng])} interactive={false}
            pathOptions={{ color: '#E8A317', weight: 2.5, dashArray: '7 5', fillColor: '#E8A317', fillOpacity: 0.08 }} />
        )}
      </Pane>
      <Pane name="pond" style={{ zIndex: 440 }}>
        {visible.pond && result?.pond?.footprint_geojson && (
          <GeoJSON key={`p-${stamp}`} data={result.pond.footprint_geojson} interactive={false}
            style={{ color: '#FFFFFF', weight: 1.5, fillColor: '#0B4F94', fillOpacity: 0.88 }} />
        )}
      </Pane>
      {visible.pond && result?.pond && (
        <Marker position={[result.pond.lat, result.pond.lng]} icon={pondIcon} keyboard={false}
          alt="Suggested pond location">
          <Tooltip permanent direction="right" offset={[16, -24]} className="pond-label">
            <strong>{fmt(result.water.collectable_volume_m3)} m³</strong> of water a year
          </Tooltip>
        </Marker>
      )}
      {sites.map((s) => (
        <CircleMarker key={s.id} center={[s.lat, s.lng]} radius={6}
          pathOptions={{ color: '#fff', weight: 2, fillColor: '#E8A317', fillOpacity: 1 }}
          eventHandlers={{ click: () => onSiteOpen(s) }}>
          <Tooltip direction="top" offset={[0, -6]} className="map-tip">{s.name}: {fmt(s.collectable_m3)} m³/yr</Tooltip>
        </CircleMarker>
      ))}
      {coverage?.length > 0 && !result && !drawMode && (
        <Pane name="coverage" style={{ zIndex: 400 }}>
          {coverage.map((t) => (
            <Rectangle key={t.tile} bounds={[[t.south, t.west], [t.north, t.east]]} interactive={false}
              pathOptions={{ color: '#FFFFFF', weight: 1, opacity: 0.35, dashArray: '2 6', fill: false }} />
          ))}
        </Pane>
      )}
      <DrawTool key={drawMode || 'off'} mode={drawMode} onDone={onDrawDone} onCancel={onDrawCancel} />
    </MapContainer>
  )
}

