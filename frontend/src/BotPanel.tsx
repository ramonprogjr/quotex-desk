import { useEffect, useState } from "react";
import { api, type BotState, type State } from "./api";

/** Robô de execução — SOMENTE conta DEMO da Quotex. */
export function BotPanel({ state, asset, live }: { state: State | null; asset: string; live: BotState | null }) {
  const [bot, setBot] = useState<BotState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [testAsset, setTestAsset] = useState("EURUSD");

  useEffect(() => {
    const load = () => api.bot().then(setBot).catch(() => {});
    load();
    const i = setInterval(load, 3000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => { if (live) setBot(live); }, [live]);
  useEffect(() => { if (state?.assets[asset] && !state.assets[asset].unavailable) setTestAsset(asset); }, [asset]);

  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); setBot(await api.bot()); } catch (e) { setErr(String(e).replace(/^Error: \d+: /, "")); }
  };
  const b = bot;
  const isDemo = b?.account === "DEMO";
  const tone = !b?.process ? "off" : b.account === "REAL" ? "real" : b.enabled ? "on" : "idle";

  return (
    <div className="botp">
      <div className={`bot-status ${tone}`}>
        <b>{!b?.process ? "DESLIGADO" : b.status?.toUpperCase()}</b>
        {b?.process && <span>{b.account ? `conta ${b.account}` : "sem login"}{b.balance != null ? ` · saldo $${b.balance.toLocaleString("en-US")}` : ""}</span>}
      </div>

      {!b?.process ? (
        <button className="bot-main" onClick={() => act(api.botLaunch)}>▶ Abrir navegador do robô</button>
      ) : (
        <div className="bot-row">
          <button className={`bot-main ${b.enabled ? "pause" : ""}`} disabled={!isDemo}
            onClick={() => act(() => api.botEnable(!b.enabled))}>
            {b.enabled ? "⏸ Pausar robô" : "⚡ Ligar robô (DEMO)"}
          </button>
          <button onClick={() => act(api.botDiagnose)}>Diagnóstico</button>
          <button className="x" title="fechar o navegador do robô" onClick={() => act(api.botStop)}>■</button>
        </div>
      )}

      {b?.process && (
        <div className="bot-test">
          <span>teste mecânico ($1):</span>
          <select value={testAsset} onChange={(e) => setTestAsset(e.target.value)}>
            {Object.entries(state?.assets ?? {}).map(([k, a]) => <option key={k} value={k}>{a.quotex}</option>)}
          </select>
          <button disabled={!isDemo} onClick={() => act(() => api.botTest(testAsset, "CALL", 5))}>CALL</button>
          <button disabled={!isDemo} onClick={() => act(() => api.botTest(testAsset, "PUT", 5))}>PUT</button>
        </div>
      )}

      {b?.checks && (
        <ul className="bot-checks">
          {Object.entries(b.checks).map(([k, v]) => <li key={k} className={v ? "up" : "down"}>{v ? "✔" : "✘"} {k}{v ? `: ${v}` : ""}</li>)}
        </ul>
      )}
      {err && <p className="note warn">{err}</p>}
      <p className="note">
        {!b?.process ? "Abre um Chrome separado: faça login na Quotex nele e escolha a CONTA DEMO. Sua senha não passa pelo código." :
          !isDemo ? "Selecione a CONTA DEMO no navegador do robô. Com conta REAL ele fica travado." :
            `Opera só sinais APROVADOS (Edge + Closer + disjuntor) no M${(b.timeframes ?? [5]).join("/M")}, ativos fora do OTC, 1 por vez, com o valor do Kelly. Use o modo "Tempo" (cronômetro) na Quotex.`}
      </p>
    </div>
  );
}
