import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, connectWS, hhmmss, money, pct, type Block, type Candle, type Catalog, type LogEv, type MarketMap, type PreSignal,
  type Signal, type State, type Stats, type Trade,
} from "./api";
import { Chart, type Badge } from "./Chart";
import { Spectrum } from "./Spectrum";
import { AGENTS, Orbit } from "./Orbit";
import { QuickEntry, SETUP_PT } from "./QuickEntry";
import { CatalogTable, Specialists } from "./Specialists";
import { Lab } from "./Lab";
import { BotPanel } from "./BotPanel";
import { TestReport } from "./TestReport";
import type { BotState } from "./api";
/** "M5 rsi_bb_reversal" → "M5 · RSI + Bollinger (reversão)" */
const setupLabel = (k: string) => {
  const [a, b] = k.includes(" ") ? k.split(" ", 2) : ["", k];
  return `${a ? a + " · " : ""}${SETUP_PT[b] ?? b}`;
};
const loadTf = () => { try { return Number(localStorage.getItem("tf")) || 5; } catch { return 5; } };

function beep(freq = 880, ms = 250) {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = freq; g.gain.value = 0.08;
    o.start(); o.stop(ctx.currentTime + ms / 1000);
  } catch { /* sem áudio */ }
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(i); }, [ms]);
  return now;
}

export default function App() {
  const [state, setState] = useState<State | null>(null);
  const [asset, setAsset] = useState("BTCUSD");
  const [tf, setTf] = useState<number>(loadTf);
  const tfRef = useRef(tf);
  tfRef.current = tf;
  const chooseTf = (v: number) => { setTf(v); try { localStorage.setItem("tf", String(v)); } catch { /* ok */ } };
  const [candles, setCandles] = useState<Candle[]>([]);
  const [spectrum, setSpectrum] = useState<{ ts: string[]; setups: Record<string, number[]> }>({ ts: [], setups: {} });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [logs, setLogs] = useState<LogEv[]>([]);
  const [journal, setJournal] = useState<Trade[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [map, setMap] = useState<MarketMap | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [pres, setPres] = useState<Record<string, PreSignal>>({});
  const [preResult, setPreResult] = useState<Record<string, string>>({});
  const [allLogs, setAllLogs] = useState(false);
  const [botLive, setBotLive] = useState<BotState | null>(null);
  const [flashes, setFlashes] = useState<Record<string, { direction: "CALL" | "PUT"; entry_at: string; until: number }>>({});
  const [demo, setDemo] = useState<{ start: number; asset: string; tf: number; dir: "CALL" | "PUT" } | null>(null);
  const announced = useRef<Set<string>>(new Set());
  const assetRef = useRef(asset);
  assetRef.current = asset;
  const now = useNow(500);

  const refreshState = useCallback(() => api.state().then(setState).catch((e) => setError(String(e))), []);
  const refreshJournal = useCallback(() => {
    api.journal().then(setJournal);
    api.stats().then(setStats);
    refreshState();
  }, [refreshState]);
  const refreshMarket = useCallback((a: string, t: number) => {
    api.candles(a, t).then(setCandles);
    api.spectrum(a, t).then(setSpectrum);
    api.map(a, t).then(setMap).catch(() => {});
    api.catalog(a, t).then(setCatalog).catch(() => {});
  }, []);

  useEffect(() => {
    refreshState(); refreshJournal();
    api.signals().then(setSignals);
    api.activity().then(setLogs);
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
    return connectWS((m) => {
      if (m.type === "price") setPrices((p) => ({ ...p, [m.data.asset]: m.data.price }));
      else if (m.type === "candle") {
        if (m.data.asset === assetRef.current && m.data.tf === tfRef.current) refreshMarket(assetRef.current, tfRef.current);
      }
      else if (m.type === "signal") {
        setSignals((s) => [m.data, ...s].slice(0, 100));
        if (m.data.approved) {
          beep();
          if ("Notification" in window && Notification.permission === "granted")
            new Notification(`M${m.data.tf} · ${m.data.quotex} ${m.data.direction} ${m.data.expiry} min`, { body: `Valor ${money(m.data.stake)} — entrar em até ${Math.round((Date.parse(m.data.valid_until) - Date.parse(m.data.entry_at)) / 1000)} s` });
        }
      } else if (m.type === "presignal") {
        const p: PreSignal = m.data;
        const k = `${p.asset}|${p.tf}`;
        setPres((x) => ({ ...x, [k]: p }));
        setPreResult((x) => { const y = { ...x }; delete y[k]; return y; });
        for (const s of p.signals.filter((s) => s.approved)) {
          const id = `${k}|${p.candle_ts}|${s.setup}|${s.direction}`;
          if (!announced.current.has(id)) {
            announced.current.add(id);
            beep(520, 160);
            if ("Notification" in window && Notification.permission === "granted")
              new Notification(`PRÉ-SINAL M${p.tf} · ${p.quotex} ${s.direction}?`, { body: `Candle fecha em ${p.seconds_left}s — prepare a ordem, aguarde confirmação` });
          }
        }
      } else if (m.type === "presignal_result") {
        const k = `${m.data.asset}|${m.data.tf}`;
        setPres((x) => { const y = { ...x }; delete y[k]; return y; });
        const txt = m.data.items.map((i: { key: string; status: string }) =>
          `${i.key.replace("|", " ")}: ${i.status === "confirmed" ? "CONFIRMADO" : i.status === "canceled" ? "CANCELADO — não entre" : "sinal sem pré-aviso"}`).join(" · ");
        setPreResult((x) => ({ ...x, [k]: `Último pré-sinal → ${txt}` }));
        const canceled = m.data.items.find((i: { status: string }) => i.status === "canceled");
        const confirmed = m.data.items.some((i: { status: string }) => i.status === "confirmed");
        if (canceled && !confirmed) {
          const entry = new Date(Date.parse(m.data.candle_ts) + m.data.tf * 60_000).toISOString();
          setFlashes((x) => ({ ...x, [k]: { direction: canceled.key.split("|")[1], entry_at: entry, until: Date.now() + 10_000 } }));
        }
      } else if (m.type === "bot") setBotLive(m.data);
      else if (m.type === "log") setLogs((l) => [m.data, ...l].slice(0, 200));
      else if (m.type === "resolved") api.stats().then(setStats);
      else if (m.type === "journal" || m.type === "state") refreshJournal();
    });
  }, [refreshState, refreshJournal, refreshMarket]);

  useEffect(() => {
    refreshMarket(asset, tf);
    api.presignals().then((l) => setPres(Object.fromEntries(l.map((p) => [`${p.asset}|${p.tf}`, p]))));
    const i = setInterval(() => refreshMarket(asset, tf), 30000);
    const j = setInterval(() => api.map(asset, tf).then(setMap).catch(() => {}), 5000); // especialistas ao vivo
    return () => { clearInterval(i); clearInterval(j); };
  }, [asset, tf, refreshMarket]);

  const assetSignals = useMemo(() => signals.filter((s) => s.asset === asset && s.tf === tf), [signals, asset, tf]);
  const current = assetSignals[0] ?? null;
  // tudo segue o ativo + timeframe escolhidos
  const liveSignal = assetSignals.find((s) => s.approved && Date.parse(s.valid_until) > now) ?? null;
  const preHere = pres[`${asset}|${tf}`] ?? null;
  const myTrades = journal.filter((t) => t.asset === asset);
  // alertas em outro ativo/timeframe: aparecem como aviso clicável
  const elsewhere = [
    ...signals.filter((s) => s.approved && Date.parse(s.valid_until) > now && !(s.asset === asset && s.tf === tf))
      .map((s) => ({ asset: s.asset, tf: s.tf, text: `SINAL ${s.direction}` })),
    ...Object.values(pres).filter((p) => !(p.asset === asset && p.tf === tf) && Date.parse(p.closes_at) > now)
      .flatMap((p) => p.signals.filter((s) => s.approved).slice(0, 1).map((s) => ({ asset: p.asset, tf: p.tf, text: `pré-sinal ${s.direction}?` }))),
  ];
  const focus = liveSignal ?? current;
  const tz = state?.config.timezone ?? "America/Sao_Paulo";

  const live = prices[asset] ?? state?.assets[asset]?.price ?? null;

  // ---------- selo de confirmação (gráfico + órbita) ----------
  const hm = (iso: string) => hhmmss(iso, tz).slice(0, 5);
  const mmss = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const pendingSig = journal.filter((t) => t.result === "pending" && t.signal_id)
    .map((t) => ({ t, s: signals.find((s) => s.id === t.signal_id) }))
    .find(({ s }) => s && s.asset === asset && s.tf === tf && Date.parse(s.expires_at) > now);
  const preTop = preHere?.signals.find((s) => s.approved) ?? null;
  const flash = flashes[`${asset}|${tf}`];
  let badge: Badge | null = null;
  if (demo && demo.asset === asset && demo.tf === tf && now - demo.start < 50_000) {
    const el = now - demo.start;
    const step = tf * 60_000;
    const entry = new Date(Math.ceil(demo.start / step) * step).toISOString();
    const exp = new Date(Date.parse(entry) + step).toISOString();
    const base = { direction: demo.dir, entry_at: entry, expires_at: exp, price: live, demo: true };
    badge = el < 8_000 ? { ...base, kind: "pre", title: `${demo.dir}? às ${hm(entry)}`, sub: `aguarde o fechamento · ${Math.ceil((8_000 - el) / 1000)}s` }
      : el < 28_000 ? { ...base, kind: "enter", title: `${demo.dir} às ${hm(entry)}`, sub: `expira ${hm(exp)} · entre em ${Math.ceil((28_000 - el) / 1000)}s` }
        : el < 43_000 ? { ...base, kind: "running", title: `${demo.dir} em curso`, sub: `faltam ${mmss(43_000 - el)} · expira ${hm(exp)}` }
          : { ...base, kind: "canceled", title: "CANCELADO", sub: "pré-sinal não confirmou — não entre" };
  } else if (liveSignal) {
    badge = { kind: "enter", direction: liveSignal.direction, entry_at: liveSignal.entry_at, expires_at: liveSignal.expires_at,
      price: liveSignal.price, title: `${liveSignal.direction} às ${hm(liveSignal.entry_at)}`,
      sub: `expira ${hm(liveSignal.expires_at)} · entre em ${Math.ceil((Date.parse(liveSignal.valid_until) - now) / 1000)}s` };
  } else if (pendingSig?.s) {
    const s = pendingSig.s;
    badge = { kind: "running", direction: s.direction, entry_at: s.entry_at, expires_at: s.expires_at,
      price: pendingSig.t.quotex_price ?? s.price, title: `${s.direction} em curso`,
      sub: `faltam ${mmss(Date.parse(s.expires_at) - now)} · expira ${hm(s.expires_at)}` };
  } else if (preTop && preHere && Date.parse(preHere.closes_at) > now) {
    badge = { kind: "pre", direction: preTop.direction, entry_at: preTop.entry_at, expires_at: preTop.expires_at, price: null,
      title: `${preTop.direction}? às ${hm(preTop.entry_at)}`,
      sub: `aguarde o fechamento · ${Math.ceil((Date.parse(preHere.closes_at) - now) / 1000)}s` };
  } else if (flash && flash.until > now) {
    badge = { kind: "canceled", direction: flash.direction, entry_at: flash.entry_at, expires_at: flash.entry_at, price: null,
      title: "CANCELADO", sub: "pré-sinal não confirmou — não entre" };
  }
  const risk = state?.risk;
  const bank = risk?.bankroll ?? 0;
  const pending = journal.filter((t) => t.result === "pending");

  return (
    <div className="desk">
      <header className="topbar">
        <div className="brand"><b>QUOTEX</b> DESK<small>6 agentes · 1 sinal · você decide</small></div>
        <nav className="tabs">
          {state && Object.entries(state.assets)
            .sort(([, a], [, b]) => Number(b.source === "otc") - Number(a.source === "otc"))
            .map(([k, a]) => (
            <button key={k} className={k === asset ? "on" : ""} disabled={!!a.unavailable} title={a.unavailable ?? a.quotex}
              onClick={() => setAsset(k)}>
              {a.source === "otc" ? a.quotex : k}
              <span>{prices[k] ? prices[k].toLocaleString("pt-BR") : a.unavailable ? (a.source === "otc" ? "não gravando" : "sem fonte") : "…"}</span>
            </button>
          ))}
        </nav>
        <div className="tfswitch" title="Troca gráfico, sinais, entrada, órbita, especialistas e catalogação">
          {(state?.config.timeframes ?? [1, 5]).map((v: number) => (
            <button key={v} className={v === tf ? "on" : ""} onClick={() => chooseTf(v)}>
              M{v}<span>{v === 1 ? "1 minuto" : `${v} minutos`}</span>
            </button>
          ))}
        </div>
        <div className="kpis">
          <div><label>BANCA</label><b>{money(bank)}</b></div>
          <div><label>DIA</label><b className={(risk?.day_pnl ?? 0) >= 0 ? "up" : "down"}>{money(risk?.day_pnl)}</b></div>
          <div><label>ENTRADAS</label><b>{risk?.trades_today ?? 0}/{state?.config.max_trades_day}</b></div>
          <div className="clock"><b>{new Date(now).toLocaleTimeString("pt-BR", { timeZone: tz, hour12: false })}</b><label>BRT · {new Date(now).toISOString().slice(11, 19)} UTC</label></div>
          <div className={`status ${state?.risk_block ? "blocked" : "ok"}`}>{state?.risk_block ? "BLOQUEADO" : "LIVRE"}</div>
        </div>
      </header>
      {state?.risk_block && <div className="banner">⛔ {state.risk_block}</div>}
      {state && (!state.payouts_updated_at || now - Date.parse(state.payouts_updated_at) > PAYOUT_STALE_MS) && (
        <div className="banner warnb">⚠ Payout desatualizado: confira o payout de M1 e M5 na Quotex e salve em AJUSTES — o empate depende dele.</div>
      )}
      {error && <div className="banner">Backend indisponível: {error}</div>}
      {elsewhere.length > 0 && (
        <div className="elsewhere">
          {elsewhere.map((e, i) => (
            <button key={i} onClick={() => { setAsset(e.asset); chooseTf(e.tf); }}>
              ⚡ {e.text} em <b>{e.asset} M{e.tf}</b> — ver
            </button>
          ))}
        </div>
      )}

      <section className="row trade">
        <div className="stack">
          <Panel title={`${asset} · ${state?.assets[asset]?.quotex ?? ""}`} tag={live ? live.toLocaleString("pt-BR") : ""} wide>
            <Chart candles={candles} signals={assetSignals} trades={myTrades} map={map} badge={badge}
              live={live} tf={tf} timeframes={state?.config.timeframes ?? [1, 5]} onTf={chooseTf} now={now} />
          </Panel>
          <Panel title="A ÓRBITA" tag="SPOTTER → PRIOR → EDGE → KELLY → CLOSER" wide>
            <Orbit signal={focus} pre={preHere} map={map} tf={tf} now={now} badge={badge} />
          </Panel>
        </div>
        <div className="stack">
          <Panel title="ENTRADA" tag={`${asset} · M${tf}`}>
            <QuickEntry state={state} asset={asset} tf={tf} now={now}
              pre={preHere} active={liveSignal} preResult={preResult[`${asset}|${tf}`] ?? null}
              hasPending={pending.length > 0} onDone={refreshJournal} />
          </Panel>
          <Panel title="ROBÔ" tag="SOMENTE CONTA DEMO">
            <BotPanel state={state} asset={asset} live={botLive} />
          </Panel>
          <Panel title={liveSignal ? "SINAL ATIVO" : "ÚLTIMO SINAL"} tag={`${asset} · M${tf}`}>
            <SignalCard s={focus} now={now} tz={tz} onTaken={refreshJournal} blocked={!!state?.risk_block} hasPending={pending.length > 0} />
          </Panel>
          <Panel title="ESPECIALISTAS" tag={`${asset} · M${tf}`}>
            <Specialists map={map} />
          </Panel>
        </div>
      </section>

      <section className="row r1">
        <Panel title="TESTE OTC (ROBÔ DEMO)" tag="ENTRADAS AUTOMÁTICAS · SEM VANTAGEM COMPROVADA" wide>
          <TestReport />
        </Panel>
        <Panel title="BANCA & VALIDAÇÃO" tag={stats ? `${stats.journal.validation.n}/${stats.journal.validation.target_n} OPS` : ""}>
          <BankPanel state={state} stats={stats} />
        </Panel>
        <Panel title="ESPECTRO DE SINAIS" tag={`${asset} · M${tf} · ÚLTIMOS 90 CANDLES`} wide>
          <Spectrum data={spectrum} />
        </Panel>
      </section>

      <section className="agents">
        {AGENTS.map((a) => (
          <div key={a.key} className="agent" style={{ borderColor: a.color }}>
            <i style={{ background: a.color }} />
            <div><b>{a.name}</b><small>{a.key === "forward" ? fwText(stats) : focus?.agents[a.key] ?? a.role}</small></div>
          </div>
        ))}
      </section>

      <section className="row r3">
        <Panel title="HISTÓRICO DA BANCA" tag="DIÁRIO">
          <BankHistory trades={journal} start={state?.bankroll_start ?? 100} />
        </Panel>
        <Panel title="LOG DE ATIVIDADE" tag={allLogs ? "TODOS" : `${asset} · M${tf}`}
          extra={<div className="tfs"><button className={allLogs ? "on" : ""} onClick={() => setAllLogs(!allLogs)}>todos</button></div>}>
          <div className="log">
            {logs.filter((l) => allLogs || !l.asset || (l.asset === asset && (l.tf == null || l.tf === tf))).map((l, i) => (
              <div key={i} className={`ev ${l.level}`}>
                <time>{hhmmss(l.at, tz)}</time>
                <em style={{ color: AGENTS.find((a) => a.key === l.agent)?.color ?? "#8b93a7" }}>{l.agent.toUpperCase()}</em>
                <span>{l.asset ? <b>{l.asset} </b> : null}{l.text}</span>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="CATALOGAÇÃO" tag={`${asset} · M${tf}`}>
          <CatalogTable cat={catalog} />
        </Panel>
      </section>

      <section className="row">
        <Panel title="LABORATÓRIO EVOLUTIVO" tag={`${asset} · M${tf} · TREINO 60% → VALIDAÇÃO 20% → COFRE 20%`} wide>
          <Lab asset={asset} tf={tf} state={state} />
        </Panel>
      </section>

      <section className="row r4">
        <Panel title="DIÁRIO DE OPERAÇÕES" tag="REGISTRE TODA ENTRADA">
          <JournalPanel state={state} trades={journal} asset={asset} tf={tf} onChange={refreshJournal} />
        </Panel>
        <Panel title="CALIBRAÇÃO" tag="PREVISTO × REAL">
          <Calibration stats={stats} state={state} onChange={refreshJournal} />
        </Panel>
        <Panel title="AJUSTES" tag="BANCA · PAYOUT">
          <Settings state={state} onSaved={setState}
            onDemo={() => setDemo({ start: Date.now(), asset, tf, dir: Math.random() < 0.5 ? "CALL" : "PUT" })} />
        </Panel>
      </section>
      <footer>Ferramenta de apoio. Não executa ordens. Opções binárias têm valor esperado negativo sem vantagem comprovada — o painel veta por padrão.</footer>
    </div>
  );
}

const fwText = (s: Stats | null) => {
  if (!s) return "teste ao vivo";
  const a = s.forward.approved, v = s.forward.vetoed;
  return `aprovados ${pct(a.winrate)} (${a.n}) · vetados ${pct(v.winrate)} (${v.n})`;
};

function Panel({ title, tag, children, wide, extra }: { title: string; tag?: string; children: React.ReactNode; wide?: boolean; extra?: React.ReactNode }) {
  return (
    <div className={`panel ${wide ? "wide" : ""}`}>
      <div className="ph"><span>■ {title}</span>{extra}<em>{tag}</em></div>
      <div className="pb">{children}</div>
    </div>
  );
}

function BankPanel({ state, stats }: { state: State | null; stats: Stats | null }) {
  const o = stats?.journal.overall;
  const be = o?.breakeven ?? 1 / (1 + (state?.config.payout ?? 0.85));
  const v = stats?.journal.validation;
  return (
    <div className="bank">
      <div className="big">{money(state?.risk.bankroll)}</div>
      <div className="grid4">
        <Kpi label="ACERTO REAL" value={pct(o?.winrate)} tone={o?.winrate != null ? (o.winrate > be ? "up" : "down") : ""} />
        <Kpi label="EMPATE" value={pct(be, 2)} />
        <Kpi label="LIMITE INF." value={o?.n ? pct(o.lo) : "—"} />
        <Kpi label="P&L TOTAL" value={money(o?.pnl)} tone={(o?.pnl ?? 0) >= 0 ? "up" : "down"} />
      </div>
      <Bar label="validação (100 operações)" value={(v?.n ?? 0) / (v?.target_n ?? 100)} color="#3a86ff" />
      <Bar label={`acerto vs empate ${pct(be)}`} value={o?.winrate ?? 0} mark={be} color={o?.edge_proven ? "#22e39b" : "#ff9f1c"} />
      <p className="note">{v?.passed ? "✔ vantagem comprovada no seu histórico real." :
        o?.n ? `Ainda sem prova: o limite inferior (${pct(o.lo)}) precisa passar ${pct(be)} com 100+ operações.` :
          "Nenhuma operação registrada ainda."}</p>
    </div>
  );
}

const Kpi = ({ label, value, tone = "" }: { label: string; value: string; tone?: string }) => (
  <div className="kpi"><label>{label}</label><b className={tone}>{value}</b></div>
);

const Bar = ({ label, value, mark, color }: { label: string; value: number; mark?: number; color: string }) => (
  <div className="bar"><label>{label}</label>
    <div><i style={{ width: `${Math.min(100, value * 100)}%`, background: color }} />{mark != null && <u style={{ left: `${mark * 100}%` }} />}</div>
  </div>
);

function SignalCard({ s, now, tz, onTaken, blocked, hasPending }: { s: Signal | null; now: number; tz: string; onTaken: () => void; blocked: boolean; hasPending: boolean }) {
  const [busy, setBusy] = useState(false);
  if (!s) return <div className="empty">Aguardando o fechamento do próximo candle…</div>;
  const left = Math.max(0, Math.round((Date.parse(s.valid_until) - now) / 1000));
  const active = s.approved && left > 0;
  const take = async () => {
    setBusy(true);
    try { await api.addTrade({ asset: s.asset, direction: s.direction, expiry: s.expiry, stake: s.stake, payout: s.payout, signal_id: s.id }); onTaken(); }
    finally { setBusy(false); }
  };
  return (
    <div className={`sig ${s.approved ? s.direction.toLowerCase() : "veto"}`}>
      <div className="sig-head">
        <b>{s.direction}</b>
        <span className="tfbadge">M{s.tf}</span>
        <span>{s.quotex}</span>
        <em>{active ? `${left}s` : s.approved ? "expirado" : "VETADO"}</em>
      </div>
      <dl>
        <dt>Setup</dt><dd>{SETUP_PT[s.setup] ?? s.setup}</dd>
        <dt>Expiração</dt><dd>{s.expiry} min · modo horário <b>{hhmmss(s.expires_at, tz).slice(0, 5)}</b></dd>
        <dt>Valor</dt><dd>{s.approved ? money(s.stake) : "R$ 0,00"}</dd>
        <dt>Acerto hist.</dt><dd>{pct(s.p_hat)} (inf. {pct(s.lo)}) · {s.n} ops</dd>
        <dt>Empate</dt><dd>{pct(s.breakeven, 2)} @ payout {pct(s.payout, 0)}</dd>
        <dt>Motivo</dt><dd>{s.approved ? s.agents.closer : s.agents.edge?.startsWith("vantagem") ? s.agents.closer : s.agents.edge}</dd>
      </dl>
      {active && (
        <button className="take" disabled={busy || blocked || hasPending} onClick={take}>
          {hasPending ? "feche a operação aberta no diário" : blocked ? "bloqueado pelo risco" : `ENTREI NA QUOTEX — REGISTRAR ${money(s.stake)}`}
        </button>
      )}
    </div>
  );
}

function BankHistory({ trades, start }: { trades: Trade[]; start: number }) {
  const pts = useMemo(() => {
    const done = [...trades].reverse().filter((t) => t.result !== "pending");
    let b = start;
    return [start, ...done.map((t) => (b += t.pnl ?? 0))];
  }, [trades, start]);
  const W = 340, H = 150;
  const min = Math.min(...pts), max = Math.max(...pts);
  const y = (v: number) => H - 10 - ((v - min) / (max - min || 1)) * (H - 20);
  const x = (i: number) => (i / Math.max(pts.length - 1, 1)) * W;
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const up = pts[pts.length - 1] >= start;
  return (
    <div className="history">
      <div className="big small">{money(pts[pts.length - 1])}</div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <path d={`${d} L${W},${H} L0,${H} Z`} fill={up ? "#22e39b22" : "#ff4d6d22"} />
        <path d={d} fill="none" stroke={up ? "#22e39b" : "#ff4d6d"} strokeWidth={2} />
        <line x1={0} x2={W} y1={y(start)} y2={y(start)} stroke="#4b5468" strokeDasharray="4 4" />
      </svg>
    </div>
  );
}

function JournalPanel({ state, trades, asset, tf, onChange }: { state: State | null; trades: Trade[]; asset: string; tf: number; onChange: () => void }) {
  const [f, setF] = useState({ direction: "CALL" as "CALL" | "PUT", expiry: tf, stake: 2 });
  const [exitPx, setExitPx] = useState<Record<number, string>>({});
  useEffect(() => setF((x) => ({ ...x, expiry: tf })), [tf]);
  const add = async () => {
    await api.addTrade({ asset, ...f, tf, payout: state?.assets[asset]?.payouts?.[String(tf)] ?? state?.assets[asset]?.payout });
    onChange();
  };
  const res = async (id: number, r: Trade["result"]) => {
    const px = Number((exitPx[id] ?? "").replace(",", "."));
    await api.setResult(id, r, px > 0 ? px : null);
    onChange();
  };
  return (
    <div className="journal">
      <div className="jform">
        <span className="hint">Entrada manual (fora de sinal) em <b>{asset}</b>:</span>
        <select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value as "CALL" | "PUT" })}><option>CALL</option><option>PUT</option></select>
        <input type="number" min={1} value={f.expiry} onChange={(e) => setF({ ...f, expiry: +e.target.value })} title="expiração (min)" />
        <input type="number" min={1} step={0.5} value={f.stake} onChange={(e) => setF({ ...f, stake: +e.target.value })} title="valor" />
        <button onClick={add}>REGISTRAR</button>
      </div>
      <table>
        <thead><tr><th>hora</th><th>ativo</th><th>dir</th><th>exp</th><th>valor</th><th>origem</th><th>resultado</th><th /></tr></thead>
        <tbody>
          {trades.slice(0, 40).map((t) => (
            <tr key={t.id} className={t.result}>
              <td>{hhmmss(t.created_at, state?.config.timezone).slice(0, 5)}</td>
              <td>{t.asset}</td><td className={t.direction === "CALL" ? "up" : "down"}>{t.direction}</td>
              <td>{t.expiry}m</td><td>{money(t.stake)}</td><td>{t.setup ? "sinal" : "manual"}</td>
              <td>{t.result === "pending" ? (
                <span className="res">
                  {t.signal_id && (
                    <input className="px" placeholder="preço fim Quotex" title="Opcional: preço de expiração que a Quotex mostrou (mede a diferença do feed)"
                      value={exitPx[t.id] ?? ""} onChange={(e) => setExitPx({ ...exitPx, [t.id]: e.target.value })} />
                  )}
                  <button className="w" onClick={() => res(t.id, "win")}>GANHOU</button>
                  <button className="l" onClick={() => res(t.id, "loss")}>PERDEU</button>
                  <button onClick={() => res(t.id, "push")}>EMPATE</button>
                </span>
              ) : <b className={t.result === "win" ? "up" : t.result === "loss" ? "down" : ""}>{money(t.pnl)}</b>}</td>
              <td><button className="x" title="apagar" onClick={async () => { if (confirm("Apagar esta operação?")) { await api.delTrade(t.id); onChange(); } }}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Calibration({ stats, state, onChange }: { stats: Stats | null; state: State | null; onChange: () => void }) {
  if (!stats) return null;
  const row = (k: string, b: Block) => (
    <tr key={k}><td>{setupLabel(k)}</td><td>{b.n}</td><td className={b.winrate != null && b.winrate > b.breakeven ? "up" : "down"}>{pct(b.winrate)}</td><td>{b.n ? pct(b.lo) : "—"}</td><td>{pct(b.breakeven)}</td></tr>
  );
  const fq = stats.feed_vs_quotex;
  const brk = Object.entries(state?.breakers ?? {});
  return (
    <div className="calib">
      {brk.length > 0 && (
        <>
          <h4 className="down">⛔ Disjuntor — estratégias suspensas</h4>
          <ul className="breakers">{brk.map(([k, b]) => (
            <li key={k}><b>{k.replace(/\|/g, " · ")}</b> — {b.reason}
              <button onClick={async () => { if (confirm(`Reativar ${k}? Ela volta a contar do zero.`)) { await api.resetBreaker(k); onChange(); } }}>reativar</button></li>
          ))}</ul>
        </>
      )}
      <h4>Feed × Quotex (sinais que você operou)</h4>
      {fq?.n ? (
        <p className={fq.rate != null && fq.rate >= 0.9 ? "note" : "note warn"}>
          O resultado no feed bateu com a Quotex em <b>{pct(fq.rate, 0)}</b> ({fq.agree}/{fq.n}).
          {fq.entry_bps != null && <> Diferença de preço na entrada: <b>{fq.entry_bps.toFixed(1)} bps</b>.</>}
          {fq.exit_bps != null && <> Na expiração: <b>{fq.exit_bps.toFixed(1)} bps</b>.</>}
          {fq.rate != null && fq.rate < 0.9 && <> Abaixo de 90%: o histórico do feed representa mal a Quotex nesse ativo — desconfie das aprovações.</>}
        </p>
      ) : <p className="note">Opere a partir de sinais e marque o resultado: aqui aparece se o feed concorda com a Quotex. Preencher os preços da Quotex mede a diferença.</p>}
      <h4>Forward test automático (resolvido pelo feed)</h4>
      <table><thead><tr><th /><th>n</th><th>acerto</th><th>inf.</th><th>empate</th></tr></thead>
        <tbody>{row("aprovados", stats.forward.approved)}{row("vetados", stats.forward.vetoed)}
          {Object.entries(stats.forward.by_setup).map(([k, b]) => row(k, b))}</tbody></table>
      <h4>Suas operações reais</h4>
      <table><thead><tr><th /><th>n</th><th>acerto</th><th>inf.</th><th>empate</th></tr></thead>
        <tbody>{Object.entries(stats.journal.followed).map(([k, b]) => row(k, b))}
          {Object.entries(stats.journal.setup).map(([k, b]) => row(k, b))}</tbody></table>
      <h4>Células aprovadas pelo histórico</h4>
      {state?.approved_cells.length ? (
        <ul>{state.approved_cells.map((c) => (
          <li key={`${c.asset}${c.tf}${c.setup}${c.expiry}${c.bucket}`}>{c.asset} · M{c.tf} · {SETUP_PT[c.setup] ?? c.setup} · {c.expiry}m ·{" "}
            {c.bucket < 0 ? "qualquer hora" : `${String(c.bucket * 4).padStart(2, "0")}–${String(c.bucket * 4 + 4).padStart(2, "0")}h UTC`} · {pct(c.p_hat)} (inf. {pct(c.lo)}, n={c.n})</li>
        ))}</ul>
      ) : <p className="note">Nenhuma — todos os sinais serão vetados. Isso é o esperado em mercado eficiente.</p>}
    </div>
  );
}

const PAYOUT_STALE_MS = 2 * 3600_000;

function Settings({ state, onSaved, onDemo }: { state: State | null; onSaved: (s: State) => void; onDemo: () => void }) {
  const [bank, setBank] = useState<string>("");
  const [pay, setPay] = useState<Record<string, Record<string, string>>>({});
  const tfs: number[] = state?.config.timeframes ?? [1, 5];
  useEffect(() => {
    if (!state) return;
    setBank(String(state.bankroll_start));
    setPay(Object.fromEntries(Object.entries(state.assets).map(([k, a]) => [k,
      Object.fromEntries(tfs.map((t) => [String(t), String(Math.round((a.payouts?.[String(t)] ?? a.payout) * 100))]))])));
  }, [state?.bankroll_start, state?.payouts_updated_at]);
  if (!state) return null;
  const save = async () => {
    const payouts = Object.fromEntries(Object.entries(pay).map(([k, v]) =>
      [k, Object.fromEntries(Object.entries(v).map(([t, x]) => [t, Number(x) / 100]))]));
    onSaved(await api.settings({ bankroll_start: Number(bank), payouts }));
  };
  const upd = state.payouts_updated_at;
  const stale = !upd || Date.now() - Date.parse(upd) > PAYOUT_STALE_MS;
  return (
    <div className="settings">
      <label>Banca inicial (R$)<input type="number" value={bank} onChange={(e) => setBank(e.target.value)} /></label>
      <p className={`note ${stale ? "warn" : ""}`}>
        Payout atual na Quotex (%) por ativo e timeframe — o empate depende dele.{" "}
        {upd ? `Atualizado ${new Date(upd).toLocaleString("pt-BR", { day: "2-digit", hour: "2-digit", minute: "2-digit" })}.` : "Nunca atualizado."}
        {stale && " ⚠ Confira na Quotex e salve."}
      </p>
      <table className="paygrid">
        <thead><tr><th />{tfs.map((t) => <th key={t}>M{t}</th>)}</tr></thead>
        <tbody>
          {Object.entries(state.assets).map(([k, a]) => (
            <tr key={k} className={a.unavailable ? "dim" : ""}>
              <td>{k}</td>
              {tfs.map((t) => (
                <td key={t}><input type="number" value={pay[k]?.[String(t)] ?? ""}
                  onChange={(e) => setPay({ ...pay, [k]: { ...pay[k], [String(t)]: e.target.value } })} />%</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={save}>SALVAR</button>
      <button className="demo" onClick={() => { onDemo(); window.scrollTo({ top: 0, behavior: "smooth" }); }}
        title="Mostra no gráfico, só no seu navegador, como aparecem PRÉ-SINAL → ENTRAR → EM CURSO → CANCELADO">
        ▶ Prévia do selo (simulação, ~50 s)
      </button>
      <p className="note">Stop diário {pct(state.config.daily_stop_frac, 0)} · máx. {state.config.max_trades_day} entradas · pausa {state.config.cooldown_minutes} min após {state.config.max_consecutive_losses} perdas · ¼ Kelly, teto {pct(state.config.max_stake_frac, 0)}. Edite em config.json.</p>
    </div>
  );
}
