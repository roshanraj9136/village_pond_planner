import { useState, useEffect, useCallback } from 'react';
import { MapContainer, TileLayer, useMapEvents, ZoomControl, useMap, LayersControl, Circle, Popup } from 'react-leaflet';
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
  const [visionData, setVisionData] = useState(null);
  const [legalData, setLegalData] = useState(null);
  const [savedReports, setSavedReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const fetchReports = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/reports');
      const data = await res.json();
      setSavedReports(data);
    } catch (err) {
      console.error(err);
    }
  };
  
  useEffect(() => {
    fetchReports();
  }, []);

  const handleCenterChange = useCallback((latlng) => {
    setSelectedLocation(latlng);
    setAnalysisData(null); 
    setLocationData(null);
    setVisionData(null);
    setLegalData(null);
  }, []);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(searchQuery)}&format=json&limit=1&countrycodes=in&accept-language=en`);
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

      const legalRes = await fetch('http://localhost:8000/api/analyze/legal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const legalData = await legalRes.json();
      setLegalData(legalData);

      const rainRes = await fetch('http://localhost:8000/api/analyze/rainfall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const rainData = await rainRes.json();

      const terrainRes = await fetch('http://localhost:8000/api/analyze/terrain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, annual_rainfall_mm: rainData.annual_rainfall_mm })
      });
      const terrainData = await terrainRes.json();

      try {
        const visionRes = await fetch('http://localhost:8000/api/analyze/vision', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const visData = await visionRes.json();
        setVisionData(visData);
      } catch (err) {
        console.error("OpenCV processing failed", err);
      }

      setAnalysisData({ ...rainData, ...terrainData });
    } catch (error) {
      console.error("Analysis Error:", error);
      alert("Failed to connect to backend server.");
    } finally {
      setLoading(false);
    }
  };

  const saveReport = async () => {
    if (!analysisData || !locationData || !visionData) return;
    try {
      const payload = {
        lat: selectedLocation.lat,
        lng: selectedLocation.lng,
        display_name: locationData.display_name,
        catchment_area: analysisData.catchment_area_sq_meters,
        pond_depth: analysisData.recommended_pond_depth_meters,
        storage_capacity: analysisData.estimated_storage_capacity,
        vegetation: visionData.vegetation_percentage,
        water: visionData.water_body_percentage
      };
      await fetch('http://localhost:8000/api/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      alert('Report saved to database successfully!');
      fetchReports();
    } catch (err) {
      console.error(err);
      alert('Failed to save to database.');
    }
  };

  const deleteReport = async (id, e) => {
    e.stopPropagation();
    try {
      await fetch(`http://localhost:8000/api/reports/${id}`, { method: 'DELETE' });
      fetchReports();
    } catch (err) {
      console.error(err);
      alert('Failed to delete report.');
    }
  };

  return (
    <div className="app-container">
      <div className={`sidebar ${isSidebarOpen ? '' : 'closed'}`}>
        <div className="header">
          <h1>JalDrishti</h1>
          <p>Geospatial Watershed Analysis</p>
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
              <p style={{ fontSize: '0.85rem', marginBottom: '8px' }}><strong>Land Designation:</strong> {locationData.land_type}</p>
              
              {legalData && locationData.is_suitable && (
                <div style={{ marginTop: '12px', padding: '12px', background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.2)', borderRadius: '8px' }}>
                  <p style={{ fontSize: '0.85rem', marginBottom: '4px' }}><strong>Ownership Status:</strong> {legalData.owner_type}</p>
                  <p style={{ fontSize: '0.85rem', marginBottom: '4px' }}><strong>Legal Clearance:</strong> <span style={{ color: '#10b981', fontWeight: 600 }}>{legalData.clearance_status}</span></p>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{legalData.hurdles}</p>
                </div>
              )}
              
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

              <div className="report-section">
                <h3>Computer Vision Analysis</h3>
                {visionData ? (
                  <div className="data-grid">
                    <div className="data-item">
                      <span className="data-label">Vegetation Cover</span>
                      <span className="data-value">{visionData.vegetation_percentage}%</span>
                    </div>
                    <div className="data-item">
                      <span className="data-label">Surface Water</span>
                      <span className="data-value">{visionData.water_body_percentage}%</span>
                    </div>
                  </div>
                ) : (
                  <p style={{ fontSize: '0.85rem' }}>Processing satellite imagery with OpenCV...</p>
                )}
                
                {visionData && (
                  <button className="btn-secondary" onClick={saveReport} style={{ marginTop: '20px', width: '100%' }}>
                    💾 Save Approved Site to Database
                  </button>
                )}
              </div>
            </div>
          )}

          {savedReports.length > 0 && (
            <div className="report-section">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h3 style={{ margin: 0, borderBottom: 'none', paddingBottom: 0 }}>Database: Approved Sites</h3>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.1)', padding: '2px 8px', borderRadius: '12px' }}>{savedReports.length} Saved</span>
              </div>
              <div style={{ maxHeight: '220px', overflowY: 'auto', paddingRight: '4px' }}>
                <ul style={{ listStyle: 'none', padding: 0, fontSize: '0.85rem' }}>
                  {savedReports.map(r => (
                    <li key={r.id} style={{ padding: '12px 0', borderTop: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }} onClick={() => setTargetCenter([r.lat, r.lng])}>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <span style={{ color: '#ef4444', marginRight: '10px', fontSize: '1.1rem' }}>📍</span> 
                        <span style={{ color: '#ededed' }}>{r.display_name.split(',')[0]} - <strong style={{ color: '#ffffff' }}>{r.storage_capacity.toLocaleString()} m³</strong></span>
                      </div>
                      <button onClick={(e) => deleteReport(r.id, e)} style={{ background: 'none', border: 'none', color: '#71717a', cursor: 'pointer', fontSize: '1.2rem', marginLeft: '8px' }} title="Delete site">×</button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>

      <button 
        className={`toggle-sidebar-btn ${isSidebarOpen ? '' : 'closed'}`}
        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
        title="Toggle Dashboard"
      >
        {isSidebarOpen ? '◀' : '▶'}
      </button>

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
          <LayersControl position="topright">
            <LayersControl.BaseLayer checked name="Satellite">
              <TileLayer
                url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                attribution='&copy; <a href="https://www.esri.com/">Esri</a>'
              />
            </LayersControl.BaseLayer>
            <LayersControl.BaseLayer name="Contour (Topography)">
              <TileLayer
                url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
                attribution='Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap (CC-BY-SA)'
              />
            </LayersControl.BaseLayer>
          </LayersControl>
          <MapCenterTracker onCenterChange={handleCenterChange} />
          <MapController targetCenter={targetCenter} />

          {/* Database Saved Sites Overlays */}
          {savedReports.map(report => (
            <Circle 
              key={report.id}
              center={[report.lat, report.lng]} 
              radius={Math.sqrt((report.storage_capacity / report.pond_depth) / Math.PI)}
              pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.6, weight: 2 }}
            >
              <Popup>
                <strong>{report.display_name.split(',')[0]}</strong><br/>
                Capacity: {report.storage_capacity.toLocaleString()} m³<br/>
                Vegetation: {report.vegetation}%
              </Popup>
            </Circle>
          ))}

          {analysisData && locationData?.is_suitable && selectedLocation && (
            <>
              {/* Catchment Area */}
              <Circle 
                center={[selectedLocation.lat, selectedLocation.lng]} 
                radius={Math.sqrt(analysisData.catchment_area_sq_meters / Math.PI)}
                pathOptions={{ color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.2, dashArray: '5, 5' }}
              >
                <Popup>Estimated Catchment Area: {(analysisData.catchment_area_sq_meters / 10000).toFixed(1)} ha</Popup>
              </Circle>
              
              {/* Pond Location */}
              <Circle 
                center={[selectedLocation.lat, selectedLocation.lng]} 
                radius={Math.sqrt((analysisData.estimated_storage_capacity / analysisData.recommended_pond_depth_meters) / Math.PI)}
                pathOptions={{ color: '#10b981', fillColor: '#10b981', fillOpacity: 0.8 }}
              >
                <Popup>Recommended Pond Location</Popup>
              </Circle>
            </>
          )}
        </MapContainer>
      </div>
    </div>
  );
}

export default App;
