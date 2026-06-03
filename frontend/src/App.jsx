import { useEffect, useState, useCallback, useRef } from "react";
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement, PointElement,
  LineElement, ArcElement, Title, Tooltip, Legend, Filler
} from "chart.js";
import { Bar, Line, Doughnut } from "react-chartjs-2";
import SpatialFloorMap from "./SpatialFloorMap.jsx";

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, ArcElement, Title, Tooltip, Legend, Filler);

const API = import.meta.env.VITE_API_URL || "https://shoplytics-2.onrender.com";
const STORE = import.meta.env.VITE_STORE_ID || "ST1008";

async function api(path, opts) {
  const r = await fetch(`${API}${path}`, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("json")) return r.json();
  return r.text();
}

const DashboardIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>;
const StreamIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>;
const SpatialIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon><line x1="8" y1="2" x2="8" y2="18"></line><line x1="16" y1="6" x2="16" y2="22"></line></svg>;

// ─── Live Stream Viewer (RESTORED WEBCAM) ────────────────────────────────────
function LiveStreamViewer({ storeId, onRefresh }) {
  const [tab, setTab] = useState("webcam");
  const [status, setStatus] = useState("Idle");
  const [detectStatus, setDetectStatus] = useState(null);
  const [frameKey, setFrameKey] = useState(0);
  const [error, setError] = useState(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const pollDetect = useCallback(() => {
    fetch(`${API}/detect/status`).then(r => r.ok ? r.json() : null).then(d => {
      if (d) { 
        setDetectStatus(d); 
        if (d.running) { setFrameKey(k => k + 1); onRefresh?.(); }
        if (d.error) { setError(`PIPELINE ERROR: ${d.error}`); setStatus("Pipeline Failed"); }
      }
    }).catch(() => {});
  }, [onRefresh]);

  useEffect(() => { pollDetect(); const id = setInterval(pollDetect, 1000); return () => clearInterval(id); }, [pollDetect]);

  const stopAll = async () => {
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    if (videoRef.current) videoRef.current.srcObject = null;
    await fetch(`${API}/detect/stop`, { method: "POST" }).catch(() => {});
    setStatus("Stopped");
  };

  const startLocalWebcam = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      streamRef.current = stream;
      if (videoRef.current) { videoRef.current.srcObject = stream; videoRef.current.play(); }
      setStatus("Local Webcam Live");
    } catch (e) { setError("Webcam Access Denied: " + e.message); }
  };

  const startYoloWebcam = async () => {
    setError(null);
    // Explicitly release local webcam first so backend can take it (Windows conflict)
    if (streamRef.current) {
        streamRef.current.getTracks().forEach(t => t.stop());
        streamRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
        setStatus("Switching to AI...");
    }

    const r = await fetch(`${API}/detect/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_type: "webcam", webcam_index: 0, role: "floor", store_id: storeId, realtime: true }),
    });
    if (!r.ok) setError("Backend detection busy or failed");
    else { setStatus("YOLO Pipeline Online"); setTab("yolo"); }
  };

  return (
    <div className="sl-card" style={{padding: "2rem"}}>
      <div className="sl-card-header">
        <div>
          <h3>Visual Intelligence Pipeline</h3>
          <p>Real-time computer vision source management</p>
        </div>
        <div className="sl-status-tag">{status}</div>
      </div>

      <div className="sl-grid-2-1">
        <div className="sl-video-box">
          {tab === "yolo" ? (
            <img key={frameKey} src={`${API}/detect/frame?t=${frameKey}`} alt="YOLO" style={{width:"100%", height:"100%", objectFit:"contain"}} />
          ) : (
            <video ref={videoRef} style={{width:"100%", height:"100%", objectFit:"contain"}} muted playsInline />
          )}
          <div className="sl-video-tag">{tab === "yolo" ? "AI ANNOTATED FEED" : "LOCAL OPTICAL FEED"}</div>
        </div>

        <div className="sl-stream-controls">
          <div className="sl-card" style={{background: "rgba(255,255,255,0.02)", padding:"1.5rem"}}>
            <h4 style={{fontSize:"0.85rem", marginBottom:"1rem"}}>Capture Sources</h4>
            <div style={{display:"flex", flexDirection:"column", gap:"0.75rem"}}>
              <button className="sl-btn primary" onClick={startLocalWebcam}>Open Device Camera</button>
              <button className="sl-btn primary" onClick={startYoloWebcam}>Start AI Detection</button>
              <button className="sl-btn" onClick={() => {
                  if (tab === "webcam" && streamRef.current) {
                      streamRef.current.getTracks().forEach(t => t.stop());
                      streamRef.current = null;
                      if (videoRef.current) videoRef.current.srcObject = null;
                  }
                  setTab(t => t === "yolo" ? "webcam" : "yolo");
              }}>Toggle View Mode</button>
              <button className="sl-btn" style={{color:"var(--danger)", borderColor:"var(--danger)"}} onClick={stopAll}>Kill All Streams</button>
            </div>
            {error && <div className="sl-alert-box critical" style={{marginTop:"1rem"}}>{error}</div>}
            
            <div style={{marginTop:"2rem"}}>
              <div className="sl-signal"><span>Frames Seen</span><strong>{detectStatus?.frames_processed || 0}</strong></div>
              <div className="sl-signal"><span>FPS</span><strong>{detectStatus?.fps?.toFixed(1) || "0.0"}</strong></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main Shoplytics Dashboard ────────────────────────────────────────────────
export default function App() {
  const [page, setPage] = useState("dashboard");
  const [storeId] = useState(STORE);
  const [dash, setDash] = useState(null);
  const [heatmap, setHeatmap] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [trend, setTrend] = useState([]);
  const [vibeHist, setVibeHist] = useState([]);
  const [sysStatus, setSysStatus] = useState(null);
  const [spatial, setSpatial] = useState(null);
  const [error, setError] = useState(null);
  const [clock, setClock] = useState(new Date());
  
  // Simulation Features
  const [audioOn, setAudioOn] = useState(true);
  const [scentOn, setScentOn] = useState(false);

  const refresh = useCallback(() => {
    Promise.all([
      api(`/stores/${storeId}/dashboard`),
      api(`/stores/${storeId}/heatmap`),
      api(`/stores/${storeId}/anomalies`),
      api(`/stores/${storeId}/occupancy-trend`),
      api(`/stores/${storeId}/vibe-history`),
      api(`/stores/${storeId}/spatial`),
      api("/system/status"),
    ]).then(([d, h, a, tr, vh, sp, sys]) => {
      setDash(d); setHeatmap(h); setAnomalies(a);
      setTrend(tr.readings || []);
      setVibeHist(vh.history || []);
      setSpatial(sp); setSysStatus(sys);
      setError(null);
    }).catch(e => {
        setError(e.message);
        console.error("Poll Error:", e);
    });
  }, [storeId]);

  useEffect(() => { refresh(); const id = setInterval(refresh, 2500); return () => clearInterval(id); }, [refresh]);
  useEffect(() => { const id = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(id); }, []);

  const occData = {
    labels: trend.map((_, i) => i + 1),
    datasets: [{
      label: "Live Flow", data: trend.map(r => r.occupancy),
      borderColor: "#3b82f6", backgroundColor: "rgba(59, 130, 246, 0.1)", fill: true, tension: 0.4, pointRadius: 2,
    }]
  };

  const peakData = {
    labels: ["9AM", "11AM", "1PM", "3PM", "5PM", "7PM", "9PM"],
    datasets: [{
      label: "Forecast", data: [4, 12, 18, 14, 25, 22, 8],
      borderColor: "#f59e0b", backgroundColor: "rgba(245, 158, 11, 0.1)", fill: true, tension: 0.4,
    }]
  };

  const vibeBarData = {
    labels: vibeHist.slice(-10).map((_, i) => i + 1),
    datasets: [
      { label: "Cozy", data: vibeHist.slice(-10).map(h => h.vibe === "cozy" ? h.occupancy : 0), backgroundColor: "#3b82f6" },
      { label: "Buzzing", data: vibeHist.slice(-10).map(h => h.vibe === "moderate" ? h.occupancy : 0), backgroundColor: "#f59e0b" },
      { label: "Crowded", data: vibeHist.slice(-10).map(h => h.vibe === "energetic" ? h.occupancy : 0), backgroundColor: "#f472b6" },
    ]
  };

  const chartOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { display: false }, grid: { display: false } },
      y: { ticks: { color: "#64748b", font: { size: 10 } }, grid: { color: "rgba(255,255,255,0.03)" } },
    },
  };

  return (
    <div className="sl-app">
      <aside className="sl-sidebar">
        <div className="sl-logo"><i /> <span>Shoplytics</span></div>
        <nav className="sl-side-nav">
          <button className={`sl-side-btn ${page === "dashboard" ? "active" : ""}`} onClick={() => setPage("dashboard")}><DashboardIcon /> <span>Operations</span></button>
          <button className={`sl-side-btn ${page === "stream" ? "active" : ""}`} onClick={() => setPage("stream")}><StreamIcon /> <span>Inference</span></button>
          <button className={`sl-side-btn ${page === "spatial" ? "active" : ""}`} onClick={() => setPage("spatial")}><SpatialIcon /> <span>Floor Map</span></button>
        </nav>
      </aside>

      <main className="sl-main">
        <header className="sl-header">
          <div>
            <h1 style={{fontSize:"1.6rem"}}>{page.toUpperCase()} CONTROL</h1>
            <p style={{color:"var(--text-dim)"}}>Store {storeId} · Advanced Intelligence Suite</p>
          </div>
          <div style={{display:"flex", gap:"1.5rem", alignItems:"center"}}>
            <button className="sl-btn" style={{color:"var(--danger)", borderColor:"rgba(239, 68, 68, 0.2)"}} onClick={() => {
                if(window.confirm("Wipe all data for a fresh demo?")) {
                    fetch(`${API}/admin/clear-db`, {method:"POST"}).then(refresh);
                }
            }}>Reset Demo</button>
            <div className="sl-status-tag"><span /> LIVE LINK</div>
            <div className="sl-clock" style={{fontVariantNumeric:"tabular-nums"}}>{clock.toLocaleTimeString()}</div>
          </div>
        </header>

        {error && <div className="sl-toast" style={{borderColor:"var(--danger)"}}>⚠️ API Synchronization Delayed... ({error})</div>}

        {page === "dashboard" && dash && (
          <>
            <div className="sl-kpi-grid">
              <div className="sl-kpi-card">
                <div className="sl-kpi-label">Real-time Occupancy</div>
                <div className="sl-kpi-value">{dash.occupancy}</div>
                <div className="sl-kpi-meta">Density Status: {dash.occupancy_level}</div>
              </div>
              <div className="sl-kpi-card">
                <div className="sl-kpi-label">Operational Mood</div>
                <div className="sl-kpi-value" style={{fontSize:"1.4rem"}}>{dash.store_vibe}</div>
                <div className="sl-kpi-meta">Atmosphere Index</div>
              </div>
              <div className="sl-kpi-card">
                <div className="sl-kpi-label">Ambient Control</div>
                <div className="sl-ambient-widget">
                    <div style={{display:"flex", justifyContent:"space-between", fontSize:"0.8rem"}}>
                        <span>Audio System</span>
                        <div className={`sl-btn-toggle ${audioOn ? "active" : ""}`} onClick={() => setAudioOn(!audioOn)} />
                    </div>
                    <div style={{display:"flex", justifyContent:"space-between", fontSize:"0.8rem"}}>
                        <span>Scent Dispenser</span>
                        <div className={`sl-btn-toggle ${scentOn ? "active" : ""}`} onClick={() => setScentOn(!scentOn)} />
                    </div>
                </div>
              </div>
              <div className="sl-kpi-card" style={{"--indicator-color": "#ef4444"}}>
                <div className="sl-kpi-label">Active Anomalies</div>
                <div className="sl-kpi-value">{dash.active_alerts}</div>
                <div className="sl-kpi-meta" style={{color: dash.active_alerts > 0 ? "var(--danger)" : "var(--success)"}}>
                    {dash.active_alerts > 0 ? "INTERVENTION REQ" : "NO RISKS DETECTED"}
                </div>
              </div>
            </div>

            <div className="sl-grid-2-1">
              <div className="sl-card">
                <div className="sl-card-header">
                  <div><h3>Traffic Progression</h3><p>Last 30 data points · 2.5s cadence</p></div>
                </div>
                <div className="sl-chart-container"><Line data={occData} options={chartOpts} /></div>
              </div>
              <div className="sl-card">
                <div className="sl-card-header"><div><h3>Vibe History</h3><p>Atmosphere segments</p></div></div>
                <div className="sl-chart-container"><Bar data={vibeBarData} options={{...chartOpts, scales:{...chartOpts.scales, x:{stacked:true}, y:{stacked:true}}}} /></div>
              </div>
            </div>

            <div className="sl-grid-2-1" style={{gridTemplateColumns: "1fr 2fr"}}>
              <div className="sl-card">
                <div className="sl-card-header"><div><h3>Live Alerts</h3><p>Real-time anomaly stream</p></div></div>
                <div className="sl-alerts-stack">
                  {anomalies.map((a, i) => (
                    <div key={i} className={`sl-alert-box ${a.severity?.toLowerCase()}`}>
                       <strong style={{fontSize:"0.7rem", textTransform:"uppercase"}}>{a.severity}</strong>
                       <div style={{fontSize:"0.85rem", margin:"0.25rem 0"}}>{a.message}</div>
                    </div>
                  ))}
                  {anomalies.length === 0 && <div style={{textAlign:"center", color:"var(--text-dim)", padding:"2rem"}}>All Systems Optimal</div>}
                </div>
              </div>
              <div className="sl-card">
                <div className="sl-card-header"><div><h3>Peak Hours Simulation</h3><p>Forecasting based on current visitor patterns</p></div></div>
                <div className="sl-chart-container"><Line data={peakData} options={chartOpts} /></div>
              </div>
            </div>
          </>
        )}

        {page === "stream" && <LiveStreamViewer storeId={storeId} onRefresh={refresh} />}
        {page === "spatial" && <SpatialFloorMap spatial={spatial} heatmap={heatmap} storeId={storeId} />}
      </main>
    </div>
  );
}
