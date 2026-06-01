import { useEffect, useState, useCallback } from "react";
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

export default function App() {
  const [storeId, setStoreId] = useState(DEFAULT_STORE_ID);
  const [metrics, setMetrics] = useState(null);
  const [heatmap, setHeatmap] = useState(null);
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
        setHeatmap(zoneScores);
        setFunnel(f);
        setAnomalies(a);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [storeId]);

  useEffect(() => {
    refreshData();
    let interval;
    if (autoRefresh) {
      interval = setInterval(refreshData, 3000); // Live update every 3s
    }
    return () => clearInterval(interval);
  }, [autoRefresh, refreshData]);

  const handleIngest = async () => {
    setIngesting(true);
    try {
      const resp = await fetch(`${API}/admin/reload-from-file?path=${ingestPath}`, { method: 'POST' });
      if(resp.ok) {
        refreshData();
      } else {
        const err = await resp.json();
        alert("Ingest failed: " + JSON.stringify(err));
      }
    } catch(err) {
      alert("Error: " + err.message);
    }
    setIngesting(false);
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: 'rgba(255, 255, 255, 0.7)' } },
      title: { display: false }
    },
    scales: {
      x: { 
        ticks: { color: 'rgba(255,255,255,0.7)' },
        grid: { color: 'rgba(255,255,255,0.1)' }
      },
      y: { 
        ticks: { color: 'rgba(255,255,255,0.7)' },
        grid: { color: 'rgba(255,255,255,0.1)' }
      }
    }
  };

  const chartData = heatmap
    ? {
        labels: Object.keys(heatmap),
        datasets: [
          {
            label: "Zone Traffic",
            data: Object.values(heatmap),
            backgroundColor: "rgba(139, 92, 246, 0.7)",
            borderColor: "rgba(139, 92, 246, 1)",
            borderWidth: 1,
            borderRadius: 4
          },
        ],
      }
    : null;

  const funnelData = funnel?.stages
    ? {
        labels: funnel.stages.map((s) => s.stage),
        datasets: [
          {
            label: "Funnel Drop-off",
            data: funnel.stages.map((s) => s.count),
            backgroundColor: "rgba(236, 72, 153, 0.7)",
            borderColor: "rgba(236, 72, 153, 1)",
            borderWidth: 1,
            fill: true,
            tension: 0.4
          },
        ],
      }
    : null;

  return (
    <div className="app-container">
      <div className="bg-orbs">
        <div className="orb orb-1"></div>
        <div className="orb orb-2"></div>
        <div className="orb orb-3"></div>
      </div>
      
      <main className="dashboard z-10 glass-panel">
        <header className="header flex-between">
          <div>
            <h1 className="gradient-text">Apex Store Intelligence</h1>
            <p className="subtitle">Real-Time Operations Center</p>
          </div>
          <div className="controls flex gap-2">
            <div className="input-group">
              <input type="text" value={storeId} onChange={e => setStoreId(e.target.value)} placeholder="Store ID (e.g. ST1008)" className="glass-input" style={{ width: '150px' }} />
            </div>
            <div className="input-group">
              <input type="text" value={ingestPath} onChange={e => setIngestPath(e.target.value)} placeholder="Dataset path..." className="glass-input" style={{ width: '150px' }} />
              <button onClick={handleIngest} disabled={ingesting} className="glass-btn primary">
                {ingesting ? '...' : 'Ingest Events'}
              </button>
            </div>
            <button 
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`glass-btn ${autoRefresh ? 'active' : ''}`}
            >
              {autoRefresh ? '🔴 Live Mode' : '⚪ Paused'}
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner glow-red">
            ⚠ Connection Error: {error} - Please start API servers
          </div>
        )}

        {!metrics && !error ? (
          <div className="loading-state">Initializing analytics engine...</div>
        ) : metrics ? (
          <>
            <div className="kpi-grid mb-4">
              <div className="kpi-card glass-panel bounce-hover">
                <div className="kpi-label">Unique Visitors</div>
                <div className="kpi-value text-glow-blue">{metrics.visitors}</div>
              </div>
              <div className="kpi-card glass-panel bounce-hover">
                <div className="kpi-label">Conversion Rate</div>
                <div className="kpi-value text-glow-green">{metrics.conversion_rate}%</div>
              </div>
              <div className="kpi-card glass-panel bounce-hover">
                <div className="kpi-label">Queue Depth</div>
                <div className="kpi-value text-glow-orange">{metrics.queue_depth}</div>
              </div>
              <div className="kpi-card glass-panel bounce-hover">
                <div className="kpi-label">Avg Dwell Time</div>
                <div className="kpi-value text-glow-purple">{metrics.avg_dwell_seconds}s</div>
              </div>
            </div>

            <div className="charts-grid">
              <div className="chart-container glass-panel fade-in">
                <h3 className="section-title">Zone Traffic Heatmap</h3>
                <div className="chart-wrapper">
                  {chartData && <Bar data={chartData} options={chartOptions} />}
                </div>
              </div>
              <div className="chart-container glass-panel fade-in delay-1">
                <h3 className="section-title">Customer Conversions</h3>
                <div className="chart-wrapper">
                  {funnelData && <Line data={funnelData} options={chartOptions} />}
                </div>
              </div>
              <div className="chart-container glass-panel anomalies-panel fade-in delay-2">
                <h3 className="section-title">AI Operational Anomalies ({anomalies.length})</h3>
                <div className="anomalies-list">
                  {anomalies.length === 0 && <div className="no-anomalies" style={{ opacity: 0.7 }}>All systems optimal. No anomalies detected.</div>}
                  {anomalies.map((a, i) => (
                    <div key={i} className={`anomaly-card ${(a.severity||"").toLowerCase()} slide-in`} style={{ animationDelay: `${i * 0.1}s` }}>
                      <div className="flex-between">
                        <strong className="anomaly-type">{a.type}</strong>
                        <span className="severity-badge">{a.severity}</span>
                      </div>
                      <p className="anomaly-msg">{a.message}</p>
                      {a.suggested_action && <div className="action-txt">💡 Action: {a.suggested_action}</div>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
