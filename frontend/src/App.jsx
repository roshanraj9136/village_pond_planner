import { useState, useEffect, useCallback, useRef } from 'react';
import { MapContainer, TileLayer, useMapEvents, ZoomControl, useMap, LayersControl, Circle, Popup, GeoJSON, CircleMarker } from 'react-leaflet';
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

  }, []);

  return null;
}

function MapController({ targetCenter, targetBounds }) {
  const map = useMap();
  useEffect(() => {
    if (targetBounds) {
      map.fitBounds(targetBounds, { padding: [40, 40], duration: 1.5 });
    } else if (targetCenter) {
      map.flyTo(targetCenter, 14, { duration: 1.5 });
    }
  }, [targetCenter, targetBounds, map]);
  return null;
}

function App() {
  const [activeTab, setActiveTab] = useState('contour');
  const [searchQuery, setSearchQuery] = useState('');
  const [targetCenter, setTargetCenter] = useState(null);
  const [targetBounds, setTargetBounds] = useState(null);
  const [selectedLocation, setSelectedLocation] = useState(null);
  const [locationData, setLocationData] = useState(null);
  const [analysisData, setAnalysisData] = useState(null);
  const [visionData, setVisionData] = useState(null);
  const [legalData, setLegalData] = useState(null);
  const [savedReports, setSavedReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const [contourData, setContourData] = useState(null);
  const [uploadFileName, setUploadFileName] = useState('');
  const fileInputRef = useRef(null);

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
        setTargetBounds(null);
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

  const handleFileUpload = async (file) => {
    if (!file) return;
    setLoading(true);
    setUploadFileName(file.name);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch('http://localhost:8000/analyzeContour', {
        method: 'POST',
        body: formData
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to analyze contour file.");
      }

      const data = await res.json();
      setContourData(data);

      const bbox = data.contour_metadata.bounding_box;
      setTargetBounds([
        [bbox.min_lat, bbox.min_lng],
        [bbox.max_lat, bbox.max_lng]
      ]);
    } catch (error) {
      console.error("Contour analysis error:", error);
      alert(error.message || "Failed to process contour file.");
    } finally {
      setLoading(false);
    }
  };

  const handleLoadSampleContour = async () => {
    setLoading(true);
    setUploadFileName("contours_1m.kml (Sample Dataset)");

    try {
      const res = await fetch('http://localhost:8000/api/sampleContour');
      if (!res.ok) {
        throw new Error("Failed to load sample contour dataset.");
      }
      const data = await res.json();
      setContourData(data);

      const bbox = data.contour_metadata.bounding_box;
      setTargetBounds([
        [bbox.min_lat, bbox.min_lng],
        [bbox.max_lat, bbox.max_lng]
      ]);
    } catch (error) {
      console.error("Sample dataset error:", error);
      alert("Failed to load sample contour map.");
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
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px', marginBottom: '8px' }}>
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" style={{width: '36px', height: '36px', color: '#38bdf8'}}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S12 3 12 3s-4.5 3.97-4.5 9 2.015 9 4.5 9zm0 0c-2.485 0-4.5-4.03-4.5-9 0-3.931 1.745-6.814 3-8.243M12 21c2.485 0 4.5-4.03 4.5-9 0-3.931-1.745-6.814-3-8.243" />
            </svg>
            <h1 style={{ marginBottom: 0 }}>JalDrishti</h1>
          </div>
          <p>Advanced Village Pond & Catchment Planner</p>
        </div>

        <div className="scroll-content">

          <div className="mode-tabs">
            <button 
              className={`mode-tab ${activeTab === 'contour' ? 'active' : ''}`}
              onClick={() => setActiveTab('contour')}
            >
               Contour Map (KML/KMZ)
            </button>
            <button 
              className={`mode-tab ${activeTab === 'point' ? 'active' : ''}`}
              onClick={() => setActiveTab('point')}
            >
               Pinpoint Analysis
            </button>
          </div>

          {activeTab === 'contour' && (
            <div style={{ animation: 'fadeIn 0.3s ease-out' }}>
              <input 
                type="file" 
                ref={fileInputRef} 
                accept=".kml,.kmz" 
                style={{ display: 'none' }}
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    handleFileUpload(e.target.files[0]);
                  }
                }}
              />

              <div 
                className="upload-dropzone"
                onClick={() => fileInputRef.current?.click()}
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" style={{width: '32px', height: '32px', margin: '0 auto 8px auto', display: 'block', color: '#38bdf8'}}><path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" /></svg>
                <div className="upload-title">
                  {uploadFileName || "Upload Contour Map (.KML / .KMZ)"}
                </div>
                <div className="upload-subtitle">
                  Click to select or drag & drop terrain contour files
                </div>
              </div>

              <button 
                className="btn-sample-demo"
                onClick={handleLoadSampleContour}
                disabled={loading}
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" style={{width: '18px', height: '18px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" /></svg> Run Sample Map (<code>contours_1m.kml</code>)
              </button>

              {loading && (
                <div style={{ padding: '16px', background: 'rgba(56,189,248,0.1)', border: '1px solid rgba(56,189,248,0.2)', borderRadius: '8px', marginBottom: '16px', textAlign: 'center' }}>
                  <p style={{ color: 'var(--accent-color)', fontSize: '0.85rem', fontWeight: 600 }}>
                    Interpolating Elevation Grid & Computing Catchment...
                  </p>
                </div>
              )}

              {contourData && (
                <div style={{ animation: 'fadeIn 0.4s ease-out' }}>

                  <div className="report-section">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <h3>Contour Map Properties</h3>
                      <span className="badge-tag badge-blue">KML / KMZ</span>
                    </div>
                    <div className="data-grid">
                      <div className="data-item">
                        <span className="data-label">Contour Lines</span>
                        <span className="data-value">{contourData.contour_metadata.total_contour_lines}</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Contour Interval</span>
                        <span className="data-value">{contourData.contour_metadata.contour_interval_meters} m</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Elevation Min</span>
                        <span className="data-value">{contourData.contour_metadata.elevation_min_meters} m</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Elevation Max</span>
                        <span className="data-value">{contourData.contour_metadata.elevation_max_meters} m</span>
                      </div>
                    </div>
                  </div>

                  <div className="report-section">
                    <h3>Terrain Topography</h3>
                    <div className="data-grid">
                      <div className="data-item">
                        <span className="data-label">Topological Slope</span>
                        <span className="data-value">{contourData.terrain_metrics.average_slope_percent}%</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Terrain Type</span>
                        <span className="data-value">{contourData.terrain_metrics.terrain_classification}</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Runoff Coeff (C)</span>
                        <span className="data-value">{contourData.terrain_metrics.runoff_coefficient}</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Annual Rainfall</span>
                        <span className="data-value">{contourData.terrain_metrics.annual_rainfall_mm} mm</span>
                      </div>
                    </div>
                  </div>

                  <div className="report-section" style={{ borderLeft: '4px solid #10b981' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <h3>Optimal Pond Site</h3>
                      <span className="badge-tag badge-green">Natural Sink</span>
                    </div>
                    <p style={{ fontSize: '0.85rem', marginBottom: '8px' }}>
                      <strong>Coordinates:</strong> {contourData.pond_location.latitude.toFixed(5)}, {contourData.pond_location.longitude.toFixed(5)}
                    </p>
                    <p style={{ fontSize: '0.85rem', marginBottom: '8px' }}>
                      <strong>Basin Elevation:</strong> {contourData.pond_location.elevation_meters} m
                    </p>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {contourData.pond_location.site_suitability}
                    </p>
                  </div>

                  <div className="report-section">
                    <h3>Catchment & Sizing Metrics</h3>
                    <div className="data-grid">
                      <div className="data-item">
                        <span className="data-label">Catchment Area</span>
                        <span className="data-value highlight-success">{contourData.catchment_analysis.catchment_area_hectares} ha</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Est. Runoff Volume</span>
                        <span className="data-value highlight-success">{contourData.catchment_analysis.estimated_runoff_volume_cubic_meters.toLocaleString()} m</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Pond Surface Area</span>
                        <span className="data-value">{contourData.catchment_analysis.recommended_pond_surface_area_sq_meters.toLocaleString()} m</span>
                      </div>
                      <div className="data-item">
                        <span className="data-label">Optimal Pond Depth</span>
                        <span className="data-value">{contourData.catchment_analysis.recommended_pond_depth_meters} m</span>
                      </div>
                    </div>
                    <div style={{ marginTop: '12px', padding: '12px', background: 'rgba(16, 185, 129, 0.08)', borderRadius: '8px', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                      <span style={{ fontSize: '0.8rem', color: '#10b981', fontWeight: 600 }}>
                        Estimated Storage Capacity: {contourData.catchment_analysis.estimated_storage_capacity_cubic_meters.toLocaleString()} m
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'point' && (
            <div style={{ animation: 'fadeIn 0.3s ease-out' }}>
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
                        <span className="data-value">{analysisData.estimated_runoff_volume_cubic_meters.toLocaleString()} m</span>
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
                        <span className="data-value highlight-success">{analysisData.estimated_storage_capacity.toLocaleString()} m</span>
                      </div>
                    </div>
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
                      <button className="btn-secondary" onClick={saveReport} style={{ marginTop: '20px', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" style={{width: '18px', height: '18px', marginRight: '8px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>
                        Save Approved Site to Database
                      </button>
                    )}
                  </div>
                </div>
              )}
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
                    <li key={r.id} style={{ padding: '12px 0', borderTop: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }} onClick={() => {
                      setTargetBounds(null);
                      setTargetCenter([r.lat, r.lng]);
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" style={{width: '18px', height: '18px', color: '#ef4444', marginRight: '8px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1115 0z" /></svg> 
                        <span style={{ color: '#ededed' }}>{r.display_name.split(',')[0]} - <strong style={{ color: '#ffffff' }}>{r.storage_capacity.toLocaleString()} m</strong></span>
                      </div>
                      <button onClick={(e) => deleteReport(r.id, e)} style={{ background: 'none', border: 'none', color: '#71717a', cursor: 'pointer', fontSize: '1.2rem', marginLeft: '8px' }} title="Delete site">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" style={{width: '18px', height: '18px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                      </button>
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
        {isSidebarOpen ? (
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor" style={{width: '18px', height: '18px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" /></svg>
        ) : (
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor" style={{width: '18px', height: '18px'}}><path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" /></svg>
        )}
      </button>

      <div className="map-container">
        {activeTab === 'point' && (
          <div className="center-target">
            <div className="target-horizontal"></div>
            <div className="target-vertical"></div>
            <div className="target-center"></div>
          </div>
        )}

        <MapContainer 
          center={[21.2628, 81.2870]}
          zoom={15}
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
          <MapController targetCenter={targetCenter} targetBounds={targetBounds} />

          {contourData && contourData.catchment_analysis.boundary_geojson && (
            <GeoJSON 
              key={JSON.stringify(contourData.pond_location)}
              data={contourData.catchment_analysis.boundary_geojson}
              style={{
                color: '#38bdf8',
                weight: 3,
                opacity: 0.9,
                fillColor: '#0284c7',
                fillOpacity: 0.35,
                dashArray: '6, 6'
              }}
            />
          )}

          {contourData && contourData.pond_location && (
            <>
              <CircleMarker
                center={[contourData.pond_location.latitude, contourData.pond_location.longitude]}
                radius={12}
                pathOptions={{ color: '#10b981', fillColor: '#10b981', fillOpacity: 0.8, weight: 3 }}
              >
                <Popup>
                  <div style={{ color: '#0f172a', padding: '4px' }}>
                    <strong style={{ fontSize: '1rem', color: '#047857' }}>Optimal Pond Site</strong><br/>
                    <strong>Elevation:</strong> {contourData.pond_location.elevation_meters} m<br/>
                    <strong>Catchment:</strong> {contourData.catchment_analysis.catchment_area_hectares} ha<br/>
                    <strong>Storage:</strong> {contourData.catchment_analysis.estimated_storage_capacity_cubic_meters.toLocaleString()} m<br/>
                    <strong>Depth:</strong> {contourData.catchment_analysis.recommended_pond_depth_meters} m
                  </div>
                </Popup>
              </CircleMarker>
              <Circle 
                center={[contourData.pond_location.latitude, contourData.pond_location.longitude]} 
                radius={Math.sqrt(contourData.catchment_analysis.recommended_pond_surface_area_sq_meters / Math.PI)}
                pathOptions={{ color: '#059669', fillColor: '#34d399', fillOpacity: 0.5, weight: 2 }}
              />
            </>
          )}

          {savedReports.map(report => (
            <Circle 
              key={report.id}
              center={[report.lat, report.lng]} 
              radius={Math.sqrt((report.storage_capacity / report.pond_depth) / Math.PI)}
              pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.6, weight: 2 }}
            >
              <Popup>
                <strong>{report.display_name.split(',')[0]}</strong><br/>
                Capacity: {report.storage_capacity.toLocaleString()} m<br/>
                Vegetation: {report.vegetation}%
              </Popup>
            </Circle>
          ))}

          {activeTab === 'point' && analysisData && locationData?.is_suitable && selectedLocation && (
            <>
              <Circle 
                center={[selectedLocation.lat, selectedLocation.lng]} 
                radius={Math.sqrt(analysisData.catchment_area_sq_meters / Math.PI)}
                pathOptions={{ color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.2, dashArray: '5, 5' }}
              >
                <Popup>Estimated Catchment Area: {(analysisData.catchment_area_sq_meters / 10000).toFixed(1)} ha</Popup>
              </Circle>

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
