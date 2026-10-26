import { pct, type Catalog, type MarketMap } from "./api";
import { SETUP_PT } from "./QuickEntry";

const NAMES: Record<string, string> = {
  mapeador: "Mapeador S/R", tendencia: "Tendência", exaustao: "Exaustão", price_action: "Price action",
};

/** Barras de voto (−1 PUT … +1 CALL) de cada especialista + probabilidade da Confluência. */
export function Specialists({ map }: { map: MarketMap | null }) {
  const snap = map?.snapshot;
  if (!snap) return <div className="empty">carregando…</div>;
  const p = map.p_model;
  return (
    <div className="spec">
      {Object.entries(snap.votes).map(([k, v]) => (
        <div key={k} className="vote">
          <label>{NAMES[k] ?? k}</label>
          <div className="vbar">
            <i className={v >= 0 ? "pos" : "neg"} style={{ left: v >= 0 ? "50%" : `${50 + v * 50}%`, width: `${Math.abs(v) * 50}%` }} />
            <u />
          </div>
          <em className={v > 0 ? "up" : v < 0 ? "down" : ""}>{v > 0 ? "CALL" : v < 0 ? "PUT" : "neutro"}</em>
        </div>
      ))}
      <div className="vote conf">
        <label>Confluência (IA)</label>
        {p == null ? <span className="note">sem modelo — rode <code>cli train</code></span> : (
          <>
            <div className="vbar">
              <i className={p >= 0.5 ? "pos" : "neg"} style={{ left: p >= 0.5 ? "50%" : `${p * 100}%`, width: `${Math.abs(p - 0.5) * 100}%` }} />
              <u />
            </div>
            <em className={p >= 0.5 ? "up" : "down"}>{pct(Math.max(p, 1 - p), 1)} {p >= 0.5 ? "CALL" : "PUT"}</em>
          </>
        )}
      </div>
      <div className="ctx">
        <span>volatilidade <b>{snap.volatility_pct == null ? "—" : pct(snap.volatility_pct, 0)}</b></span>
        <span>ADX <b>{snap.adx?.toFixed(0) ?? "—"}</b></span>
        <span>RSI <b>{snap.rsi?.toFixed(0) ?? "—"}</b></span>
        <span>Estoc. <b>{snap.stoch_k?.toFixed(0) ?? "—"}</b></span>
        <span>sequência <b>{snap.streak ?? "—"}</b></span>
      </div>
      {map.forming && <p className="note">inclui o candle em formação — muda até fechar.</p>}
    </div>
  );
}

/** Catalogação: como cada estratégia se saiu nas últimas horas (informativo; quem aprova é o Edge). */
export function CatalogTable({ cat }: { cat: Catalog | null }) {
  if (!cat?.rows.length) return <div className="empty">catalogando…</div>;
  return (
    <div className="catalog">
      <table>
        <thead><tr><th>estratégia</th><th>últ. ops</th><th>acerto</th><th>sequência</th></tr></thead>
        <tbody>
          {cat.rows.map((r) => (
            <tr key={r.setup}>
              <td>{SETUP_PT[r.setup] ?? r.setup}</td>
              <td>{r.n}</td>
              <td className={r.winrate != null && r.winrate > r.breakeven ? "up" : "down"}>{pct(r.winrate, 0)}</td>
              <td className="seq">{r.last.map((x, i) => <i key={i} className={x > 0 ? "w" : "l"} />)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">Últimas 30 operações de cada estratégia nos candles em memória{cat.window ? ` (desde ${new Date(cat.window[0]).toLocaleString("pt-BR", { day: "2-digit", hour: "2-digit", minute: "2-digit" })})` : ""}. Poucas operações = muita sorte envolvida.</p>
    </div>
  );
}
