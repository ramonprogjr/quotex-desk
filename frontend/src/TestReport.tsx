import { useEffect, useState } from "react";
import { api, type TestReport as Report, type TestScore } from "./api";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const usd = (v: number) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const day = (iso: string) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });

const RESULT: Record<string, [string, string]> = {
  win: ["✔ WIN", "up"], loss: ["✘ LOSS", "down"], push: ["= EMPATE", ""], pending: ["… EM ABERTO", ""], unknown: ["? SEM SALDO", ""],
};

/** Placar do modo teste OTC: entradas do robô na DEMO (fora do diário). Atualiza sozinho. */
export function TestReport() {
  const [r, setR] = useState<Report | null>(null);
  const [by, setBy] = useState<"setup" | "asset" | "tf">("setup");

  useEffect(() => {
    const load = () => api.testReport().then(setR).catch(() => {});
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);

  if (!r || r.total.n === 0) return <p className="note">Nenhuma entrada do teste OTC ainda. Cada entrada do robô aparece aqui na hora, e o resultado quando vence.</p>;
  const t = r.total;

  return (
    <div className="treport">
      <div className="tr-total">
        <div><label>ENTRADAS</label><b>{t.n}</b></div>
        <div><label>WIN</label><b className="up">{t.wins}</b></div>
        <div><label>LOSS</label><b className="down">{t.losses}</b></div>
        <div><label>ACERTO</label><b>{pct(t.rate)}</b></div>
        <div><label>EMPATE (payout {pct(t.payout)})</label><b>{pct(t.breakeven)}</b></div>
        <div><label>RESULTADO</label><b className={t.pnl >= 0 ? "up" : "down"}>{usd(t.pnl)}</b></div>
      </div>
      <p className={`note ${t.proven ? "" : "warn"}`}>
        {t.proven
          ? `Pior cenário do acerto (${pct(t.lo)}) acima do empate: vantagem estatística no total.`
          : `Sem vantagem comprovada: o acerto real pode estar entre ${pct(t.lo)} e ${pct(t.hi)}. Precisa de 100 ou mais entradas para estreitar.`}
      </p>

      <div className="tr-tabs">
        {(["setup", "asset", "tf"] as const).map((g) => (
          <button key={g} className={by === g ? "on" : ""} onClick={() => setBy(g)}>
            {g === "setup" ? "por setup" : g === "asset" ? "por par" : "por timeframe"}
          </button>
        ))}
      </div>
      <table>
        <thead><tr><th></th><th>entr.</th><th>W</th><th>L</th><th>acerto</th><th>intervalo</th><th>empate</th><th>$</th></tr></thead>
        <tbody>
          {r.by[by].map((g: TestScore & { key: string }) => (
            <tr key={g.key}>
              <td>{g.key}</td><td>{g.n}</td><td className="up">{g.wins}</td><td className="down">{g.losses}</td>
              <td>{pct(g.rate)}</td><td className="hint">{g.lo == null ? "—" : `${pct(g.lo)}–${pct(g.hi)}`}</td>
              <td className="hint">{pct(g.breakeven)}</td>
              <td className={g.pnl >= 0 ? "up" : "down"}>{usd(g.pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <table className="tr-list">
        <thead><tr><th>dia</th><th>hora</th><th>par</th><th></th><th>setup</th><th>dir.</th><th>payout</th><th>resultado</th><th>$</th></tr></thead>
        <tbody>
          {r.trades.map((x) => {
            const [label, cls] = RESULT[x.result] ?? [x.result, ""];
            return (
              <tr key={x.id}>
                <td className="hint">{day(x.at)}</td><td>{hhmm(x.at)}</td><td>{x.quotex}</td><td>M{x.tf}</td>
                <td>{x.setup}</td><td className={x.direction === "CALL" ? "up" : "down"}>{x.direction}</td>
                <td>{pct(x.payout)}</td><td className={cls}>{label}</td>
                <td className={(x.pnl ?? 0) >= 0 ? "up" : "down"}>{x.pnl == null ? "" : usd(x.pnl)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
