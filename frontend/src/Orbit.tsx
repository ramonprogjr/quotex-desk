import type { MarketMap, PreSignal, Signal } from "./api";
import type { Badge } from "./Chart";

/** Esteira de decisão (anel externo). Só Edge e Closer podem vetar. */
export const AGENTS = [
  { key: "spotter", name: "SPOTTER", color: "#22e39b", role: "detecta setup" },
  { key: "prior", name: "PRIOR", color: "#ff9f1c", role: "taxa histórica" },
  { key: "edge", name: "EDGE", color: "#ff3d8b", role: "prova vantagem" },
  { key: "kelly", name: "KELLY", color: "#a259ff", role: "tamanho" },
  { key: "closer", name: "CLOSER", color: "#3a86ff", role: "regras de risco" },
  { key: "forward", name: "FORWARD", color: "#ff5e3a", role: "teste ao vivo" },
];

/** Especialistas (anel interno): votam o tempo todo, não precisam concordar. */
const SPECS = [
  { key: "mapeador", name: "S/R" },
  { key: "tendencia", name: "TEND" },
  { key: "exaustao", name: "EXAUST" },
  { key: "price_action", name: "P.A." },
  { key: "ia", name: "IA" },
];

const voteColor = (v: number) => (v > 0.05 ? "#22e39b" : v < -0.05 ? "#ff4d6d" : "#4b5468");

type Props = { signal: Signal | null; pre: PreSignal | null; map: MarketMap | null; tf: number; now: number; badge?: Badge | null };

export function Orbit({ signal, pre, map, tf, now, badge }: Props) {
  const W = 560, H = 300, cx = W / 2, cy = H / 2;
  const fresh = signal && now - Date.parse(signal.entry_at) < tf * 60_000; // sinal do candle atual
  const sig = fresh ? signal : null;
  const preTop = pre?.signals.find((s) => s.approved) ?? null;
  const preLive = !!preTop && Date.parse(pre!.closes_at) > now;

  const vetoAt = sig && !sig.approved
    ? sig.agents.edge && !sig.agents.edge.startsWith("vantagem") ? "edge" : "closer"
    : null;
  const order = AGENTS.map((a) => a.key);
  const passed = (k: string) => !sig ? false : sig.approved ? k !== "forward" : order.indexOf(k) < order.indexOf(vetoAt!);

  // votos atuais (−1..+1); IA convertida de probabilidade
  const votes: Record<string, number> = { ...(map?.snapshot?.votes ?? {}) };
  if (map?.p_model != null) votes.ia = Math.max(-1, Math.min(1, (map.p_model - 0.5) * 10));
  const net = SPECS.reduce((s, x) => s + (votes[x.key] ?? 0), 0);

  // centro
  const step = tf * 60;
  const left = Math.ceil(step - ((now / 1000) % step));
  let centerFill = "#f2f4f8", title = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`, sub = "próximo candle";
  if (sig?.approved) { centerFill = sig.direction === "CALL" ? "#22e39b" : "#ff4d6d"; title = sig.direction; sub = `ENTRAR · ${sig.expiry} min`; }
  else if (preLive) { centerFill = "#ff9f1c"; title = `${preTop!.direction}?`; sub = `pré-sinal · ${Math.max(0, Math.round((Date.parse(pre!.closes_at) - now) / 1000))}s`; }
  else if (sig) { centerFill = "#2a3242"; title = "VETADO"; sub = vetoAt === "edge" ? "sem vantagem" : "regra de risco"; }
  // o selo do gráfico manda: mesmo texto nos dois lugares ("CALL às 22:45")
  if (badge) {
    const col = badge.direction === "CALL" ? "#22e39b" : "#ff4d6d";
    centerFill = badge.kind === "pre" ? "#ff9f1c" : badge.kind === "canceled" ? "#2a3242" : col;
    title = badge.kind === "canceled" ? "CANCELADO" : badge.title.replace(" em curso", "");
    sub = badge.kind === "enter" ? "ENTRAR" : badge.kind === "running" ? "em curso" : badge.kind === "pre" ? "aguarde" : "não entre";
  }
  const darkText = centerFill !== "#2a3242";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="orbit">
      <defs>
        <radialGradient id="glow"><stop offset="0%" stopColor={preLive ? "#ff9f1c33" : "#ffffff22"} /><stop offset="100%" stopColor="#00000000" /></radialGradient>
      </defs>

      {/* trilhas girando */}
      <g className="spin slow" style={{ transformOrigin: `${cx}px ${cy}px` }}>
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <ellipse key={i} cx={cx} cy={cy} rx={230 - i * 9} ry={95 - i * 5} fill="none"
            stroke={i % 2 ? "#ff4d6d22" : "#22e39b22"} strokeWidth={1} transform={`rotate(${i * 4 - 12} ${cx} ${cy})`} />
        ))}
      </g>
      <circle cx={cx} cy={cy} r={95} fill="url(#glow)" className={preLive ? "pulse" : ""} />

      {/* anel interno: especialistas girando */}
      <g className="spin" style={{ transformOrigin: `${cx}px ${cy}px` }}>
        <ellipse cx={cx} cy={cy} rx={125} ry={62} fill="none" stroke="#1d2433" strokeDasharray="2 5" />
        {SPECS.map((s, i) => {
          const ang = (i / SPECS.length) * Math.PI * 2;
          const x = cx + Math.cos(ang) * 125, y = cy + Math.sin(ang) * 62;
          const v = votes[s.key] ?? 0;
          const r = 9 + Math.abs(v) * 7;
          return (
            <g key={s.key}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke={voteColor(v)} strokeOpacity={0.15 + Math.abs(v) * 0.5} />
              <circle cx={x} cy={y} r={r} fill={voteColor(v)} fillOpacity={0.25 + Math.abs(v) * 0.75} stroke={voteColor(v)} />
              <g className="spin reverse" style={{ transformOrigin: `${x}px ${y}px` }}>
                <text x={x} y={y + r + 11} textAnchor="middle" className="orbit-mini">{s.name} {v > 0.05 ? "▲" : v < -0.05 ? "▼" : "·"}</text>
              </g>
            </g>
          );
        })}
      </g>

      {/* anel externo: esteira de decisão */}
      {AGENTS.map((a, i) => {
        const ang = (-90 + i * 60) * (Math.PI / 180);
        const x = cx + Math.cos(ang) * 220, y = cy + Math.sin(ang) * 112;
        const on = passed(a.key);
        const veto = vetoAt === a.key;
        const watching = preLive && (a.key === "edge" || a.key === "closer");
        return (
          <g key={a.key} opacity={!sig || on || veto ? 1 : 0.35}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke={on ? a.color : "#2a3242"} strokeDasharray={on ? "" : "3 4"} className={on ? "flow" : ""} />
            <circle cx={x} cy={y} r={20} fill={veto ? "#1a0f14" : a.color} stroke={veto ? "#ff4d6d" : watching ? "#ff9f1c" : "none"}
              strokeWidth={2} className={watching ? "pulse" : ""} />
            <text x={x} y={y + 5} textAnchor="middle" className="orbit-glyph" fill={veto ? "#ff4d6d" : "#0a0d14"}>
              {veto ? "✕" : on ? "✓" : a.key === "edge" || a.key === "closer" ? "⚖" : "·"}
            </text>
            <text x={x} y={y + 35} textAnchor="middle" className="orbit-label">{a.name}</text>
          </g>
        );
      })}

      {/* centro */}
      <circle cx={cx} cy={cy} r={50} fill={centerFill} className={preLive ? "pulse" : ""} />
      <text x={cx} y={cy - 2} textAnchor="middle" className="orbit-center" fill={darkText ? "#0a0d14" : "#8b93a7"}>{title}</text>
      <text x={cx} y={cy + 16} textAnchor="middle" className="orbit-sub" fill={darkText ? "#0a0d14" : "#8b93a7"}>{sub}</text>
      <text x={cx} y={H - 6} textAnchor="middle" className="orbit-mini">
        especialistas: {net > 0.3 ? "inclinados a CALL" : net < -0.3 ? "inclinados a PUT" : "divididos"} · decide quem tem ⚖ (Edge + Closer)
      </text>
    </svg>
  );
}
