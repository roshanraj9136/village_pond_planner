import { useState, useEffect, useCallback } from 'react';
import { MapContainer, TileLayer, useMapEvents, ZoomControl, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import './index.css';

function MapCenterTracker({ onCenterChange }) {
  const map = useMapEvents({
    moveend() {
      onCenterChange(map.getCenter());
    },
    zoomend() {
      onCenterChange(map.getCenter());
    }
  });

  useEffect(() => {
    onCenterChange(map.getCenter());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

function MapController({ targetCenter }) {
  const map = useMap();
  useEffect(() => {
    if (targetCenter) {
      map.flyTo(targetCenter, 14, { duration: 1.5 });
    }
  }, [targetCenter, map]);
  return null;
}

function App() {
  const [searchQuery, setSearchQuery] = useState('');
  const [targetCenter, setTargetCenter] = useState(null);
  const [selectedLocation, setSelectedLocation] = useState(null);
  const [locationData, setLocationData] = useState(null);
  const [analysisData, setAnalysisData] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleCenterChange = useCallback((latlng) => {
    setSelectedLocation(latlng);
    setAnalysisData(null); 
    setLocationData(null);
  }, []);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(searchQuery)}&format=json&limit=1&countrycodes=in`);
      const data = await res.json();
      if (data && data.length > 0) {
        setTargetCenter([parseFloat(data[0].lat), parseFloat(data[0].lon)]);
      } else {
        alert("Location not found. Please try adding 'India' to your search.");
      }
    } catch (err) {
      console.error(err);
      alert("Failed to search location.");
    }
  };

  const runAnalysis = async () => {
    if (!selectedLocation) return;
    setLoading(true);

    try {
      const payload = { lat: selectedLocation.lat, lng: selectedLocation.lng };
      
      const locRes = await fetch('http://localhost:8000/api/analyze/location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const locData = await locRes.json();
      setLocationData(locData);

      if (!locData.is_suitable) {
        setLoading(false);
        return;
      }

      const rainRes = await fetch('http://localhost:8000/api/analyze/rainfall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const rainData = await rainRes.json();

      const terrainRes = await fetch('http://localhost:8000/api/analyze/terrain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const terrainData = await terrainRes.json();

      setAnalysisData({ ...rainData, ...terrainData });
    } catch (error) {
      console.error("Analysis Error:", error);
      alert("Failed to connect to backend server.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <div className="sidebar">
        <div className="header">
          <h1>PondSight System</h1>
          <p>Geospatial AI Analysis Dashboard</p>
        </div>
        
        <div className="scroll-content">
          <form className="search-box" onSubmit={handleSearch}>
            <input 
              type="text" 
              placeholder="Search for a village or town..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
            />
            <button type="submit" className="btn-search">Search</button>
          </form>

          <div className="location-info">
            <p>
              Latitude 
              <span className="value">{selectedLocation ? selectedLocation.lat.toFixed(5) : "---"}</span>
            </p>
            <p style={{ marginTop: '8px' }}>
              Longitude 
              <span className="value">{selectedLocation ? selectedLocation.lng.toFixed(5) : "---"}</span>
            </p>
          </div>

          <button 
            className="btn-primary" 
            onClick={runAnalysis} 
            disabled={!selectedLocation || loading}
          >
            {loading ? "Processing Data..." : "Execute Terrain Analysis"}
          </button>

          {locationData && (
            <div className="report-section" style={{ animation: 'fadeIn 0.5s ease-out', borderLeft: locationData.is_suitable ? '4px solid #10b981' : '4px solid #ef4444' }}>
              <h3>Location Verification</h3>
              <p style={{ fontSize: '0.85rem', marginBottom: '8px' }}><strong>Address:</strong> {locationData.display_name}</p>
              <p style={{ fontSize: '0.85rem' }}><strong>Land Designation:</strong> {locationData.land_type}</p>
              
              {!locationData.is_suitable && (
                <div style={{ marginTop: '12px', padding: '12px', background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', border: '1px solid rgba(239, 68, 68, 0.2)', borderRadius: '8px', fontSize: '0.85rem', fontWeight: 500 }}>
                  {locationData.warning_message}
                </div>
              )}
            </div>
          )}

          {analysisData && locationData?.is_suitable && (
            <div style={{ animation: 'fadeIn 0.4s' }}>
              <div className="report-section">
                <h3>Hydrological Math</h3>
                <div className="data-grid">
                  <div className="data-item">
                    <span className="data-label">Catchment Area (A)</span>
                    <span className="data-value">{(analysisData.catchment_area_sq_meters / 10000).toFixed(1)} ha</span>
                  </div>
                  <div className="data-item">
                    <span className="data-label">Annual Rainfall (I)</span>
                    <span className="data-value">{analysisData.annual_rainfall_mm} mm</span>
                  </div>
                  <div className="data-item">
                    <span className="data-label">Runoff Coefficient (C)</span>
                    <span className="data-value">{analysisData.runoff_coefficient}</span>
                  </div>
                  <div className="data-item">
                    <span className="data-label">Runoff Volume (Q)</span>
                    <span className="data-value">{analysisData.estimated_runoff_volume_cubic_meters.toLocaleString()} m³</span>
                  </div>
                </div>
              </div>

              <div className="report-section">
                <h3>System Recommendations</h3>
                <div className="data-grid">
                  <div className="data-item">
                    <span className="data-label">Optimal Pond Depth</span>
                    <span className="data-value highlight-success">{analysisData.recommended_pond_depth_meters} m</span>
                  </div>
                  <div className="data-item">
                    <span className="data-label">Storage Capacity</span>
                    <span className="data-value highlight-success">{analysisData.estimated_storage_capacity.toLocaleString()} m³</span>
                  </div>
                </div>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '16px', lineHeight: '1.5' }}>
                  Depth calculation factors in 1.5m annual evaporation loss and 0.5m seepage loss specific to the Indian subcontinent.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="map-container">
        <div className="center-target">
          <div className="target-horizontal"></div>
          <div className="target-vertical"></div>
          <div className="target-center"></div>
        </div>

        <MapContainer 
          center={[28.6139, 77.2090]}
          zoom={16}
          zoomControl={false}
          style={{ width: '100%', height: '100%' }}
        >
          <ZoomControl position="bottomright" />
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            attribution='&copy; <a href="https://www.esri.com/">Esri</a>'
          />
          <MapCenterTracker onCenterChange={handleCenterChange} />
          <MapController targetCenter={targetCenter} />
        </MapContainer>
      </div>
    </div>
  );
}

export default App;
