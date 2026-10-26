import { useEffect, useState } from "react";
import { api, pct, type LabReport, type State } from "./api";
import { SETUP_PT } from "./QuickEntry";

const FAMILY_PT: Record<string, string> = {
  reversao: "Reversão", tendencia: "Tendência", rompimento: "Rompimento", price_action: "Price action",
};
const FAMILY_COLOR: Record<string, string> = {
  reversao: "#22e39b", tendencia: "#3a86ff", rompimento: "#ff9f1c", price_action: "#a259ff",
};
const GENE_PT: Record<string, string> = {
  rsi_p: "RSI período", rsi_lo: "RSI limite", bb_n: "Bollinger n", bb_k: "Bollinger desvios",
  ema_f: "EMA rápida", ema_s: "EMA lenta", touch: "toque (ATR)", lb: "lookback", body_k: "corpo × média",
  fade: "inverter (fade)", wick: "pavio mín.", streak: "sequência antes", atr_min: "volatilidade mín.",
  atr_span: "faixa de volat.", adx_min: "ADX mín.", adx_span: "faixa ADX", "sessões": "sessões UTC (4 h)",
};

/** Laboratório Evolutivo: cria, testa e elimina estratégias — só passa quem sobrevive ao cofre. */
export function Lab({ asset, tf, state }: { asset: string; tf: number; state: State | null }) {
  const [data, setData] = useState<{ running: boolean; report: LabReport | null; live: string[] } | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [gens, setGens] = useState(40);

  useEffect(() => {
    let alive = true;
    const load = () => api.lab(asset, tf).then((d) => alive && setData(d)).catch(() => {});
    load();
    const i = setInterval(load, data?.running ? 3000 : 30000);
    return () => { alive = false; clearInterval(i); };
  }, [asset, tf, data?.running]);

  const r = data?.report;
  const run = async () => { await api.runLab(asset, tf, gens); setData((d) => d ? { ...d, running: true } : d); };
  const edgeOk = new Set((state?.approved_cells ?? []).filter((c) => c.asset === asset && c.tf === tf).map((c) => c.setup));

  const head = (
    <div className="lab-head">
      <span>{data?.running ? <b className="blink">● evoluindo {asset} M{tf}…</b> : r ? `última evolução: ${new Date(r.finished ?? r.started).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · semente ${r.seed}` : "nenhuma evolução ainda"}</span>
      <label>gerações <input type="number" min={5} max={300} value={gens} onChange={(e) => setGens(+e.target.value)} /></label>
      <button onClick={run} disabled={data?.running}>{data?.running ? "rodando…" : "▶ Rodar evolução"}</button>
    </div>
  );
  if (!r) return <div className="lab">{head}<p className="note">Rode uma evolução para este ativo/timeframe. Leva alguns minutos.</p></div>;

  const last = r.generations[r.generations.length - 1];
  const tested = r.total_tested ?? last?.tested ?? 0;
  const hall = r.hall_size ?? last?.hall ?? 0;
  const promoted = r.finalists.filter((f) => f.promoted);
  const live = data?.live ?? [];
  const funnel = [
    { label: "configurações testadas", n: tested },
    { label: "passaram na validação", n: hall },
    { label: "finalistas no cofre", n: r.finalists.length },
    { label: "aprovadas no cofre", n: promoted.length },
    { label: "aprovadas no Edge (ao vivo)", n: live.filter((id) => edgeOk.has(id)).length },
  ];
  const kill = tested ? 1 - promoted.length / tested : 0;
  const chosen = r.finalists.find((f) => f.id === sel) ?? promoted[0] ?? r.finalists[0];

  return (
    <div className="lab">
      {head}
      <div className="lab-grid">
        <div>
          <h4>Seleção natural · kill rate {pct(kill, 2)}</h4>
          {funnel.map((f) => (
            <div key={f.label} className="funnel">
              <label>{f.label}</label>
              <div><i style={{ width: `${tested ? Math.max(1.5, (Math.log10(f.n + 1) / Math.log10(tested + 1)) * 100) : 0}%` }} /></div>
              <b>{f.n.toLocaleString("pt-BR")}</b>
            </div>
          ))}
          <p className="note">Escala logarítmica. O cofre ({r.splits.vault[0].slice(0, 10)} → {r.splits.vault[1].slice(0, 10)}) só foi aberto uma vez, para os finalistas, com z de Bonferroni {r.z_bonferroni?.toFixed(2)}.</p>
          <h4>Fitness por geração (z contra o empate, treino)</h4>
          <FitnessChart gens={r.generations} />
        </div>

        <div>
          <h4>Finalistas · clique para ver o DNA</h4>
          <table className="finalists">
            <thead><tr><th>estratégia</th><th>validação</th><th>cofre</th><th /></tr></thead>
            <tbody>
              {r.finalists.map((f) => (
                <tr key={f.id} className={f.id === chosen?.id ? "sel" : ""} onClick={() => setSel(f.id)}>
                  <td><i className="dot" style={{ background: FAMILY_COLOR[f.family] }} />{FAMILY_PT[f.family]} <small>{f.id}</small></td>
                  <td>{pct(f.val.rate)} <small>n={f.val.n}</small></td>
                  <td className={f.promoted ? "up" : "down"}>{pct(f.vault.rate)} <small>inf. {pct(f.vault.lo)}</small></td>
                  <td>{f.promoted ? (live.includes(f.id) ? (edgeOk.has(f.id) ? "✔ ao vivo" : "Edge vetou") : "✔ promovida") : "✘"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {chosen && (
            <div className="dna">
              <h4>DNA · {chosen.id} <small>treino {pct(chosen.train.rate)} (n={chosen.train.n})</small></h4>
              {chosen.dna.map((d) => (
                <div key={d.gene} className="gene">
                  <label>{GENE_PT[d.gene] ?? d.gene}</label>
                  <div><i style={{ width: `${Math.max(3, d.norm * 100)}%`, background: FAMILY_COLOR[chosen.family] }} /></div>
                  <b>{typeof d.value === "number" ? +d.value.toFixed(2) : d.value}</b>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <h4>Referências honestas no cofre · empate {pct(r.breakeven, 2)}</h4>
          <table className="refs">
            <thead><tr><th /><th>acerto</th><th>n</th></tr></thead>
            <tbody>
              {[...r.baselines].sort((a, b) => (b.rate ?? 0) - (a.rate ?? 0)).map((b) => (
                <tr key={b.name} className={b.evolved ? "evo" : ""}>
                  <td>{b.evolved ? `🧬 ${b.name.replace("evoluída: ", "")}${b.promoted ? "" : " (reprovada)"}` : b.name.startsWith("fixo: ") ? `fixo: ${SETUP_PT[b.name.slice(6)] ?? b.name.slice(6)}` : b.name === "confluencia" ? "Confluência (IA)" : b.name}</td>
                  <td className={b.rate != null && b.rate > r.breakeven ? "up" : "down"}>{pct(b.rate)}</td>
                  <td>{b.n.toLocaleString("pt-BR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">"Sempre CALL/PUT" é o buy &amp; hold das binárias. Se uma evoluída não bate essas referências, ela não serve. Mesmo aprovadas, as evoluídas passam pelo Edge, pelo Closer e pelo disjuntor como qualquer estratégia.</p>
        </div>
      </div>
    </div>
  );
}

function FitnessChart({ gens }: { gens: LabReport["generations"] }) {
  if (!gens.length) return null;
  const W = 360, H = 120;
  const ys = gens.flatMap((g) => [g.best, g.mean]);
  const min = Math.min(...ys), max = Math.max(...ys);
  const x = (i: number) => (i / Math.max(gens.length - 1, 1)) * W;
  const y = (v: number) => H - 8 - ((v - min) / (max - min || 1)) * (H - 16);
  const path = (k: "best" | "mean") => gens.map((g, i) => `${i ? "L" : "M"}${x(i)},${y(g[k])}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="fitness" preserveAspectRatio="none">
      {min < 0 && max > 0 && <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="#4b5468" strokeDasharray="3 4" />}
      <path d={path("mean")} fill="none" stroke="#3a86ff" strokeDasharray="4 3" strokeWidth={1.5} />
      <path d={path("best")} fill="none" stroke="#22e39b" strokeWidth={2} />
      <text x={4} y={12} className="orbit-mini">melhor z {gens[gens.length - 1].best.toFixed(2)} · média {gens[gens.length - 1].mean.toFixed(2)}</text>
    </svg>
  );
}
