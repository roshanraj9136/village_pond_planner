export const BASES = {
  satellite: {
    label: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
    maxNativeZoom: 19,
    labels: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  },
  terrain: {
    label: 'Terrain',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: 'Map &copy; OpenStreetMap contributors, SRTM | Style &copy; OpenTopoMap (CC-BY-SA)',
    maxNativeZoom: 17,
  },
  street: {
    label: 'Street',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxNativeZoom: 19,
  },
}

export const LAYERS = [
  { id: 'parcel', label: 'Your land', swatch: 'parcel' },
  { id: 'pond', label: 'Suggested pond', swatch: 'pond' },
  { id: 'catchment', label: 'Catchment', swatch: 'catchment' },
  { id: 'streams', label: 'Water flow paths', swatch: 'stream' },
  { id: 'contours', label: 'Contour lines', swatch: 'contour' },
]
