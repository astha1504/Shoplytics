import { useEffect, useState, useCallback, useRef } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  PointElement,
  LineElement,
  Filler
} from "chart.js";
import { Bar, Line } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend, Filler);

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const DEFAULT_STORE_ID = import.meta.env.VITE_STORE_ID || "ST1008";

async function fetchJson(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path} failed`);
  return r.json();
}

// ─── Simulated detection persons for the Camera Feed tab ─────────────────────
const ZONE_LABELS = ["SKINCARE", "MAKEUP", "HAIRCARE", "FRAGRANCE", "BILLING"];
const ZONE_COLORS = {
  SKINCARE: "#34d399",
  MAKEUP: "#f472b6",
  HAIRCARE: "#60a5fa",
  FRAGRANCE: "#a78bfa",
  BILLING: "#fbbf24",
};

function generatePersons(n = 5) {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    x: 5 + Math.random() * 75,
    y: 10 + Math.random() * 70,
    w: 8 + Math.random() * 6,
    h: 18 + Math.random() * 8,
    zone: ZONE_LABELS[Math.floor(Math.random() * ZONE_LABELS.length)],
    confidence: (0.75 + Math.random() * 0.24).toFixed(2),
    dx: (Math.random() - 0.5) * 0.15,
    dy: (Math.random() - 0.5) * 0.08,
  }));
}

function useSimPersons() {
  const [persons, setPersons] = useState(generatePersons(5));
  const ref = useRef(persons);
  ref.current = persons;

  useEffect(() => {
    const id = setInterval(() => {
      setPersons(prev =>
        prev.map(p => {
          let nx = p.x + p.dx;
          let ny = p.y + p.dy;
          let dx = p.dx;
          let dy = p.dy;
          if (nx < 2 || nx > 88) dx = -dx;
          if (ny < 2 || ny > 78) dy = -dy;
          // Occasionally change zone
          const zone = Math.random() < 0.005
            ? ZONE_LABELS[Math.floor(Math.random() * ZONE_LABELS.length)]
            : p.zone;
          return { ...p, x: nx + dx, y: ny + dy, dx, dy, zone };
        })
      );
    }, 80);
    return () => clearInterval(id);
  }, []);

  return persons;
}

// ─── Health Widget ────────────────────────────────────────────────────────────
function HealthWidget({ storeId }) {
  const [health, setHealth] = useState(null);
  const [lastFetch, setLastFetch] = useState(null);

  const fetch_ = useCallback(() => {
    fetchJson("/health")
      .then(d => { setHealth(d); setLastFetch(new Date()); })
      .catch(() => {});
  }, []);

  useEffect(() => { fetch_(); const id = setInterval(fetch_, 5000); return () => clearInterval(id); }, [fetch_]);

  if (!health) return (
    <div className="glass-panel health-widget">
      <div className="health-meta">Loading health...</div>
    </div>
  );

  const storeHealth = health.stores?.[storeId] || Object.values(health.stores || {})[0];
  const warnings = storeHealth?.warnings || [];
  const isStale = warnings.includes("STALE_FEED");
  const noEvents = warnings.includes("NO_EVENTS");
  const isHealthy = !isStale && !noEvents;

  const lastAt = storeHealth?.last_event_at;
  let agoTxt = "No events";
  if (lastAt) {
    const diff = Math.floor((Date.now() - new Date(lastAt + (lastAt.endsWith("Z") ? "" : "Z")).getTime()) / 1000);
    agoTxt = diff < 60 ? `${diff}s ago` : `${Math.floor(diff / 60)}m ago`;
  }

  return (
    <div className="glass-panel health-widget">
      <div className="health-label">System Health</div>
      <div className="health-meta" style={{ color: "#94a3b8" }}>Store: {storeId}</div>
      {isHealthy && (
        <>
          <div className="health-status health-healthy">✅ Healthy</div>
          <div className="health-meta">Last Event: {agoTxt}</div>
        </>
      )}
      {isStale && (
        <>
          <div className="health-status health-stale">⚠️ STALE FEED</div>
          <div className="health-meta" style={{ color: "#fbbf24" }}>No events for {agoTxt}</div>
        </>
      )}
      {noEvents && (
        <>
          <div className="health-status health-stale">⚠️ NO EVENTS</div>
          <div className="health-meta">Awaiting data stream...</div>
        </>
      )}
      {lastFetch && (
        <div className="health-meta" style={{ marginTop: "0.5rem", fontSize: "0.78rem", color: "#475569" }}>
          Polled: {lastFetch.toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}

// ─── Store Layout Heatmap ─────────────────────────────────────────────────────
function StoreLayoutHeatmap({ heatmapRaw }) {
  if (!heatmapRaw || Object.keys(heatmapRaw).length === 0) {
    return (
      <div className="store-layout" style={{ alignItems: "center", justifyContent: "center" }}>
        <div style={{ color: "#94a3b8", textAlign: "center" }}>No zone data yet — ingest events first.</div>
      </div>
    );
  }

  const entries = Object.entries(heatmapRaw).sort((a, b) => b[1] - a[1]);
  const maxScore = Math.max(...entries.map(([, v]) => v), 1);

  const palette = [
    { bg: "rgba(139,92,246,VAL)", border: "#8b5cf6" },
    { bg: "rgba(236,72,153,VAL)", border: "#ec4899" },
    { bg: "rgba(56,189,248,VAL)", border: "#38bdf8" },
    { bg: "rgba(52,211,153,VAL)", border: "#34d399" },
    { bg: "rgba(251,191,36,VAL)", border: "#fbbf24" },
    { bg: "rgba(248,113,113,VAL)", border: "#f87171" },
  ];

  return (
    <div className="store-layout">
      {entries.map(([zone, score], i) => {
        const p = palette[i % palette.length];
        const intensity = 0.15 + 0.55 * (score / maxScore);
        const bg = p.bg.replace("VAL", intensity.toFixed(2));
        return (
          <div key={zone} className="zone-box" style={{ background: bg, borderColor: p.border + "80" }}>
            <div className="zone-name">{zone}</div>
            <div className="zone-score" style={{ color: p.border }}>{score}</div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", marginTop: "0.25rem" }}>traffic score</div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Anomalies Panel ─────────────────────────────────────────────────────────
function AnomaliesPanel({ anomalies }) {
  const severityOrder = { CRITICAL: 0, WARN: 1, INFO: 2 };
  const sorted = [...anomalies].sort((a, b) =>
    (severityOrder[a.severity] ?? 3) - (severityOrder[b.severity] ?? 3)
  );

  const sevClass = s => {
    if (s === "CRITICAL") return "sev-critical";
    if (s === "WARN") return "sev-warn";
    return "sev-info";
  };

  const typeIcon = t => {
    if (t?.includes("QUEUE")) return "🚨";
    if (t?.includes("CONVERSION")) return "📉";
    if (t?.includes("DEAD")) return "💤";
    if (t?.includes("ABANDON")) return "🔴";
    return "⚠️";
  };

  return (
    <div className="anomalies-full-panel">
      <div className="anomaly-panel-header">
        <span>Active Anomalies</span>
        <span className={`sev-badge ${anomalies.length > 0 ? "sev-critical" : "sev-ok"}`} style={{ fontSize: "0.9rem", padding: "0.3rem 0.8rem" }}>
          {anomalies.length === 0 ? "✅ All Clear" : `${anomalies.length} Alert${anomalies.length > 1 ? "s" : ""}`}
        </span>
      </div>

      {sorted.length === 0 ? (
        <div className="no-anomalies-full">
          <div style={{ fontSize: "3rem" }}>✅</div>
          <div style={{ color: "#34d399", fontWeight: 600, fontSize: "1.1rem" }}>All Systems Optimal</div>
          <div style={{ color: "#64748b", marginTop: "0.5rem" }}>No anomalies detected. Store is operating normally.</div>
        </div>
      ) : (
        <table className="anomalies-table">
          <thead>
            <tr>
              <th>Anomaly</th>
              <th>Severity</th>
              <th>Message</th>
              <th>Suggested Action</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((a, i) => (
              <tr key={i} className={`anomaly-row ${(a.severity || "").toLowerCase()}`}>
                <td>
                  <span style={{ marginRight: "0.5rem" }}>{typeIcon(a.type)}</span>
                  <strong style={{ fontSize: "0.9rem" }}>{a.type?.replace(/_/g, " ")}</strong>
                </td>
                <td>
                  <span className={`sev-badge ${sevClass(a.severity)}`}>{a.severity}</span>
                </td>
                <td className="action-text">{a.message}</td>
                <td className="action-text" style={{ color: "#94a3b8", fontStyle: "italic" }}>
                  💡 {a.suggested_action}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── Detection Preview Tab ────────────────────────────────────────────────────
function DetectionPreview() {
  const persons = useSimPersons();
  const [frameCount, setFrameCount] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setFrameCount(f => f + 1), 33);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div className="detection-header">
        <h3 className="section-title" style={{ margin: 0 }}>🎥 CCTV Detection Preview</h3>
        <div style={{ color: "#64748b", fontSize: "0.9rem" }}>
          YOLOv8n + ByteTrack — Simulated live feed &nbsp;·&nbsp; {persons.length} persons tracked
        </div>
      </div>

      <div className="pipeline-flow">
        {["CCTV Footage", "YOLO Detection", "ByteTrack", "Event Stream", "API Ingest", "Dashboard"].map((s, i, arr) => (
          <div key={s} style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <div className="pipeline-step">{s}</div>
            {i < arr.length - 1 && <div className="pipeline-arrow">→</div>}
          </div>
        ))}
      </div>

      <div className="feed-container">
        {/* Simulated camera grid background */}
        <div className="feed-scanlines" />
        <div className="cam-indicator">
          <div className="red-dot" /> CAM_FLOOR_01 &nbsp;|&nbsp; Frame #{frameCount}
        </div>

        {/* Zone region labels in feed */}
        {[
          { label: "SKINCARE", x: 5, y: 5 },
          { label: "MAKEUP", x: 38, y: 5 },
          { label: "HAIRCARE", x: 68, y: 5 },
          { label: "FRAGRANCE", x: 5, y: 55 },
          { label: "BILLING", x: 60, y: 55 },
        ].map(z => (
          <div key={z.label} className="zone-region-label" style={{ left: `${z.x}%`, top: `${z.y}%` }}>
            {z.label}
          </div>
        ))}

        {/* Bounding boxes */}
        {persons.map(p => {
          const col = ZONE_COLORS[p.zone] || "#34d399";
          return (
            <div
              key={p.id}
              className="feed-box"
              style={{
                left: `${p.x}%`,
                top: `${p.y}%`,
                width: `${p.w}%`,
                height: `${p.h}%`,
                borderColor: col,
                background: col + "18",
              }}
            >
              <div className="feed-label" style={{ background: col, color: "#000" }}>
                #{p.id} · {p.zone} · {p.confidence}
              </div>
            </div>
          );
        })}

        <div className="feed-overlay" />
      </div>

      {/* Person detail cards */}
      <div className="person-cards">
        {persons.map(p => (
          <div key={p.id} className="person-card glass-panel">
            <div className="person-id">Person #{p.id}</div>
            <div className="person-zone" style={{ color: ZONE_COLORS[p.zone] }}>
              Zone: {p.zone}
            </div>
            <div className="person-conf">Confidence: {p.confidence}</div>
            <div className="person-track">Track ID: {1000 + p.id}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [storeId, setStoreId] = useState(DEFAULT_STORE_ID);
  const [metrics, setMetrics] = useState(null);
  const [heatmapRaw, setHeatmapRaw] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [ingestPath, setIngestPath] = useState("events.jsonl");
  const [ingesting, setIngesting] = useState(false);

  const refreshData = useCallback(() => {
    Promise.all([
      fetchJson(`/stores/${storeId}/metrics`),
      fetchJson(`/stores/${storeId}/heatmap`),
      fetchJson(`/stores/${storeId}/funnel`),
      fetchJson(`/stores/${storeId}/anomalies`),
    ])
      .then(([m, h, f, a]) => {
        setMetrics(m);
        const zoneScores = {};
        Object.entries(h.zones || {}).forEach(([name, z]) => {
          zoneScores[name] = z.score ?? z.visits ?? 0;
        });
        setHeatmapRaw(zoneScores);
        setFunnel(f);
        setAnomalies(a);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [storeId]);

  useEffect(() => {
    refreshData();
    let interval;
    if (autoRefresh) interval = setInterval(refreshData, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, refreshData]);

  const handleIngest = async () => {
    setIngesting(true);
    try {
      const resp = await fetch(`${API}/admin/reload-from-file?path=${ingestPath}`, { method: "POST" });
      if (resp.ok) refreshData();
      else { const err = await resp.json(); alert("Ingest failed: " + JSON.stringify(err)); }
    } catch (err) { alert("Error: " + err.message); }
    setIngesting(false);
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "rgba(255,255,255,0.7)" } },
      title: { display: false }
    },
    scales: {
      x: { ticks: { color: "rgba(255,255,255,0.7)" }, grid: { color: "rgba(255,255,255,0.08)" } },
      y: { ticks: { color: "rgba(255,255,255,0.7)" }, grid: { color: "rgba(255,255,255,0.08)" } }
    }
  };

  const barData = heatmapRaw && Object.keys(heatmapRaw).length > 0
    ? {
      labels: Object.keys(heatmapRaw),
      datasets: [{
        label: "Zone Traffic Score",
        data: Object.values(heatmapRaw),
        backgroundColor: [
          "rgba(139,92,246,0.7)", "rgba(236,72,153,0.7)", "rgba(56,189,248,0.7)",
          "rgba(52,211,153,0.7)", "rgba(251,191,36,0.7)", "rgba(248,113,113,0.7)"
        ],
        borderColor: ["#8b5cf6","#ec4899","#38bdf8","#34d399","#fbbf24","#f87171"],
        borderWidth: 1, borderRadius: 6
      }]
    } : null;

  const funnelData = funnel?.stages
    ? {
      labels: funnel.stages.map(s => s.stage),
      datasets: [{
        label: "Funnel Count",
        data: funnel.stages.map(s => s.count),
        backgroundColor: "rgba(236,72,153,0.25)",
        borderColor: "rgba(236,72,153,1)",
        borderWidth: 2,
        fill: true,
        tension: 0.45,
        pointBackgroundColor: "#ec4899",
        pointRadius: 5
      }]
    } : null;

  const tabs = [
    { id: "dashboard", label: "📊 Dashboard" },
    { id: "anomalies", label: `🚨 Anomalies${anomalies.length > 0 ? ` (${anomalies.length})` : ""}` },
    { id: "camera", label: "🎥 Camera Feed" },
  ];

  return (
    <div className="app-container">
      <div className="bg-orbs">
        <div className="orb orb-1" />
        <div className="orb orb-2" />
        <div className="orb orb-3" />
      </div>

      <main className="dashboard z-10 glass-panel">
        <header className="header flex-between">
          <div>
            <h1 className="gradient-text">Apex Store Intelligence</h1>
            <p className="subtitle">Real-Time Operations Center · Store {storeId}</p>
          </div>
          <div className="controls flex gap-2">
            <div className="input-group">
              <input type="text" value={storeId} onChange={e => setStoreId(e.target.value)}
                placeholder="Store ID" className="glass-input" style={{ width: "110px" }} />
            </div>
            <div className="input-group">
              <input type="text" value={ingestPath} onChange={e => setIngestPath(e.target.value)}
                placeholder="events.jsonl" className="glass-input" style={{ width: "140px" }} />
              <button onClick={handleIngest} disabled={ingesting} className="glass-btn primary">
                {ingesting ? "⏳" : "▶ Ingest"}
              </button>
            </div>
            <button onClick={() => setAutoRefresh(!autoRefresh)} className={`glass-btn ${autoRefresh ? "active" : ""}`}>
              {autoRefresh ? "🔴 Live" : "⚪ Paused"}
            </button>
          </div>
        </header>

        {error && <div className="error-banner glow-red">⚠ {error} — Start the API: <code>uvicorn backend.main:app</code></div>}

        {/* Tabs */}
        <div className="tabs">
          {tabs.map(t => (
            <button key={t.id} className={`tab ${activeTab === t.id ? "active" : ""}`}
              onClick={() => setActiveTab(t.id)}>{t.label}</button>
          ))}
          {/* Health widget always visible */}
          <div style={{ marginLeft: "auto" }}>
            <HealthWidget storeId={storeId} />
          </div>
        </div>

        {/* Dashboard Tab */}
        {activeTab === "dashboard" && (
          <>
            {!metrics && !error && <div className="loading-state">Initializing analytics engine...</div>}
            {metrics && (
              <>
                {/* KPI Cards */}
                <div className="kpi-grid mb-4">
                  {[
                    { label: "Unique Visitors", value: metrics.visitors, cls: "text-glow-blue", icon: "👥" },
                    { label: "Conversion Rate", value: `${metrics.conversion_rate}%`, cls: "text-glow-green", icon: "💳" },
                    { label: "Queue Depth", value: metrics.queue_depth, cls: "text-glow-orange", icon: "🧾" },
                    { label: "Avg Dwell", value: `${metrics.avg_dwell_seconds}s`, cls: "text-glow-purple", icon: "⏱" },
                    { label: "Abandonment", value: `${metrics.abandonment_rate}%`, cls: "text-glow-red", icon: "🚪" },
                  ].map(k => (
                    <div key={k.label} className="kpi-card glass-panel bounce-hover">
                      <div className="kpi-icon">{k.icon}</div>
                      <div className="kpi-label">{k.label}</div>
                      <div className={`kpi-value ${k.cls}`}>{k.value}</div>
                    </div>
                  ))}
                </div>

                {/* Charts */}
                <div className="charts-grid mb-4">
                  <div className="chart-container glass-panel fade-in">
                    <h3 className="section-title">📍 Zone Traffic Heatmap</h3>
                    <div className="chart-wrapper">
                      {barData ? <Bar data={barData} options={chartOptions} /> : <div className="no-data">Awaiting zone events...</div>}
                    </div>
                  </div>
                  <div className="chart-container glass-panel fade-in delay-1">
                    <h3 className="section-title">📉 Conversion Funnel</h3>
                    <div className="chart-wrapper">
                      {funnelData ? <Line data={funnelData} options={chartOptions} /> : <div className="no-data">Awaiting funnel data...</div>}
                    </div>
                  </div>
                </div>

                {/* Store Layout Heatmap + Anomaly summary */}
                <div className="charts-grid">
                  <div className="chart-container glass-panel fade-in delay-2">
                    <h3 className="section-title">🏪 Store Layout</h3>
                    <div className="chart-wrapper" style={{ minHeight: "220px" }}>
                      <StoreLayoutHeatmap heatmapRaw={heatmapRaw} />
                    </div>
                  </div>
                  <div className="chart-container glass-panel fade-in delay-2 anomalies-panel">
                    <h3 className="section-title">
                      🔔 Live Anomalies
                      {anomalies.length > 0 && (
                        <span className="anom-badge">{anomalies.length}</span>
                      )}
                    </h3>
                    <div className="anomalies-list">
                      {anomalies.length === 0 && <div className="no-anomalies">✅ All systems optimal</div>}
                      {anomalies.map((a, i) => (
                        <div key={i} className={`anomaly-card ${(a.severity || "").toLowerCase()} slide-in`}
                          style={{ animationDelay: `${i * 0.1}s` }}>
                          <div className="flex-between">
                            <strong className="anomaly-type">
                              {a.type === "BILLING_QUEUE_SPIKE" ? "🚨" : a.type === "CONVERSION_DROP" ? "📉" : "💤"} {a.type?.replace(/_/g, " ")}
                            </strong>
                            <span className={`severity-badge sev-${(a.severity || "info").toLowerCase()}`}>{a.severity}</span>
                          </div>
                          <p className="anomaly-msg">{a.message}</p>
                          {a.suggested_action && <div className="action-txt">💡 {a.suggested_action}</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        {/* Anomalies Tab */}
        {activeTab === "anomalies" && (
          <div className="fade-in">
            <AnomaliesPanel anomalies={anomalies} />
          </div>
        )}

        {/* Camera Feed Tab */}
        {activeTab === "camera" && (
          <div className="fade-in">
            <DetectionPreview />
          </div>
        )}
      </main>
    </div>
  );
}
