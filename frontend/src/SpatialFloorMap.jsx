import { useEffect, useRef, useState } from "react";

const STATUS_COLORS = {
  active: "#3b82f6",
  in_queue: "#f59e0b",
  converted: "#10b981",
  exited: "#475569",
};

export default function SpatialFloorMap({ spatial, heatmap, storeId }) {
  const [animating, setAnimating] = useState({});
  const prevPos = useRef({});

  useEffect(() => {
    if (!spatial?.visitors) return;
    const next = {};
    spatial.visitors.forEach(v => {
      const prev = prevPos.current[v.visitor_id];
      if (prev && (prev.x !== v.x || prev.y !== v.y)) {
        next[v.visitor_id] = true;
        setTimeout(() => setAnimating(a => ({ ...a, [v.visitor_id]: false })), 800);
      }
      prevPos.current[v.visitor_id] = { x: v.x, y: v.y };
    });
  }, [spatial]);

  if (!spatial) {
    return <div style={{textAlign: "center", padding: "4rem", color: "var(--text-dim)"}}>Initializing Spatial Matrix...</div>;
  }

  const zones = spatial.zones || {};
  const visitors = spatial.visitors || [];
  const activeVisitors = visitors.filter(v => v.is_active);

  return (
    <div className="sl-card" style={{padding: "2rem"}}>
      <div className="sl-card-header">
        <div>
          <h3>Spatial Intelligence Matrix</h3>
          <p>Real-time consumer mapping · Store {storeId}</p>
        </div>
        <div style={{display: "flex", gap: "1rem"}}>
           <div className="sl-status-tag">ACTIVE: {spatial.active_visitors}</div>
           <div className="sl-status-tag">TOTAL: {spatial.total_tracked}</div>
        </div>
      </div>

      <div className="spatial-wrap" style={{display: "grid", gridTemplateColumns: "1fr 300px", gap: "2.5rem"}}>
        <div className="spatial-map-container" style={{background: "#070a10", borderRadius: "24px", border: "1px solid var(--border-glass)", position: "relative", overflow: "hidden"}}>
          <svg viewBox="0 0 100 72" className="spatial-svg" style={{width: "100%", height: "auto"}}>
            <defs>
              <filter id="glow-spatial"><feGaussianBlur stdDeviation="1" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
            </defs>
            <rect width="100" height="72" fill="#070a10" />
            
            {/* Zones */}
            {Object.entries(zones).map(([name, z]) => {
              if (!z.polygon) return null;
              const heat = heatmap?.zones?.[name]?.score ?? 10;
              const pts = z.polygon.map(p => `${p[0]},${p[1] * 0.72}`).join(" ");
              return (
                <g key={name}>
                  <polygon points={pts} fill={z.color} fillOpacity={0.05 + (heat / 250)} stroke={z.color} strokeWidth="0.2" strokeOpacity="0.4" />
                  <text x={z.centroid.x} y={z.centroid.y * 0.72} textAnchor="middle" fill="var(--text-dim)" fontSize="1.8" fontWeight="800" style={{textTransform: "uppercase", letterSpacing: "0.5px"}}>{name}</text>
                </g>
              );
            })}

            {/* Active Visitors Only (Hide Exited) */}
            {activeVisitors.map(v => {
              const color = STATUS_COLORS[v.status] || STATUS_COLORS.active;
              const yScaled = v.y * 0.72; // Scale 0-100 to 0-72 viewBox
              return (
                <g key={v.visitor_id} style={{ transition: "all 0.8s cubic-bezier(0.34, 1.56, 0.64, 1)" }} transform={`translate(${v.x}, ${yScaled})`}>
                  {v.is_active && <circle r="2.5" fill={color} fillOpacity="0.1" filter="url(#glow-spatial)"><animate attributeName="r" values="2;3.5;2" dur="3s" repeatCount="indefinite"/></circle>}
                  <circle r="1" fill={color} stroke="#000" strokeWidth="0.1" />
                  <text y="-2.5" textAnchor="middle" fill="#fff" fontSize="1.4" fontWeight="800" style={{pointerEvents: "none"}}>{v.display_id}</text>
                </g>
              );
            })}
          </svg>
          
          <div className="spatial-legend" style={{position: "absolute", bottom: "1.5rem", left: "1.5rem", display: "flex", gap: "1rem", background: "rgba(0,0,0,0.6)", padding: "0.6rem 1rem", borderRadius: "10px", fontSize: "0.7rem", border: "1px solid var(--border-glass)"}}>
             {Object.entries(STATUS_COLORS).map(([k, c]) => (
               <span key={k} style={{display: "flex", alignItems: "center", gap: "0.4rem"}}><i style={{width: 6, height: 6, background: c, borderRadius: "50%"}}/> {k.toUpperCase()}</span>
             ))}
          </div>
        </div>

        <div className="sl-sidebar-content">
          <div className="sl-card" style={{padding: "1.5rem", background: "rgba(255,255,255,0.01)", marginBottom: "1.5rem"}}>
            <h4 style={{fontSize: "0.8rem", color: "var(--text-dim)", marginBottom: "1rem"}}>LIVE JOURNEYS</h4>
            <div className="sl-alerts-stack" style={{maxHeight: 300}}>
              {activeVisitors.slice(0, 8).map(v => (
                <div key={v.visitor_id} style={{display: "flex", alignItems: "center", gap: "1rem", padding: "0.75rem 0", borderBottom: "1px solid var(--border-glass)"}}>
                  <div style={{width: 8, height: 8, borderRadius: "50%", background: STATUS_COLORS[v.status]}} />
                  <div style={{flex: 1}}>
                    <div style={{fontWeight: 700, fontSize: "0.85rem"}}>{v.display_id}</div>
                    <div style={{fontSize: "0.7rem", color: "var(--text-dim)"}}>{v.zone || "Entry Area"}</div>
                  </div>
                  <div style={{fontSize: "0.65rem", color: "var(--text-dim)", fontFamily: "var(--mono)"}}>{v.x.toFixed(0)}%,{v.y.toFixed(0)}%</div>
                </div>
              ))}
              {activeVisitors.length === 0 && <div style={{textAlign: "center", padding: "2rem", color: "var(--text-dim)", fontSize: "0.85rem"}}>Monitoring for entry...</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
