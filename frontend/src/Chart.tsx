import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, MarketMap, Signal, Trade } from "./api";

const t = (iso: string) => (Date.parse(iso) / 1000) as UTCTimestamp;
const floorTf = (sec: number, tf: number) => Math.floor(sec / (tf * 60)) * tf * 60;

type Toggles = { ema: boolean; bb: boolean; zones: boolean; marks: boolean };
const DEFAULT_T: Toggles = { ema: true, bb: false, zones: true, marks: true };
const loadToggles = (): Toggles => {
  try { return { ...DEFAULT_T, ...JSON.parse(localStorage.getItem("chartToggles") || "{}") }; } catch { return DEFAULT_T; }
};

/** Selo de confirmação desenhado no gráfico, no candle de entrada. */
export type Badge = {
  kind: "pre" | "enter" | "running" | "canceled";
  direction: "CALL" | "PUT";
  entry_at: string; expires_at: string;
  price: number | null;   // preço de referência da entrada (linha horizontal); null = usa o último preço
  title: string; sub: string;
  demo?: boolean;
};

type Props = {
  candles: Candle[]; signals: Signal[]; trades: Trade[]; map: MarketMap | null; badge: Badge | null;
  live: number | null; tf: number; timeframes: number[]; onTf: (tf: number) => void; now: number;
};

export function Chart({ candles, signals, trades, map, badge, live, tf, timeframes, onTf, now }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<Record<string, ISeriesApi<"Line">>>({});
  const ghost = useRef<ISeriesApi<"Line"> | null>(null);
  const zoneLines = useRef<IPriceLine[]>([]);
  const last = useRef<Candle | null>(null);
  const forming = useRef<{ time: number; open: number; high: number; low: number } | null>(null);
  const [tg, setTg] = useState<Toggles>(loadToggles);
  const [band, setBand] = useState<{ x1: number; x2: number; label: string; dir: string } | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number; w: number } | null>(null);
  const entryLine = useRef<IPriceLine | null>(null);
  const [follow, setFollow] = useState(true);

  const toggle = (k: keyof Toggles) => {
    const next = { ...tg, [k]: !tg[k] };
    setTg(next);
    try { localStorage.setItem("chartToggles", JSON.stringify(next)); } catch { /* ok */ }
  };

  // ---------- criação ----------
  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#8b93a7", fontFamily: "JetBrains Mono" },
      grid: { vertLines: { color: "#141a25" }, horzLines: { color: "#141a25" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#222a38", rightOffset: 8 },
      rightPriceScale: { borderColor: "#222a38" },
      crosshair: { mode: 0 },
    });
    series.current = c.addCandlestickSeries({
      upColor: "#22e39b", downColor: "#ff4d6d", borderVisible: false, wickUpColor: "#22e39b", wickDownColor: "#ff4d6d",
    });
    const mk = (color: string, width: 1 | 2 = 1, style: LineStyle = LineStyle.Solid) =>
      c.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    lines.current = {
      ema9: mk("#ffd166"), ema21: mk("#3a86ff"), ema50: mk("#a259ff", 2),
      bb_upper: mk("#4b546888", 1, LineStyle.Dotted), bb_mid: mk("#4b546855", 1, LineStyle.Dotted), bb_lower: mk("#4b546888", 1, LineStyle.Dotted),
    };
    ghost.current = mk("transparent");
    c.timeScale().subscribeVisibleLogicalRangeChange(() => {
      const r = c.timeScale().getVisibleLogicalRange();
      const n = series.current?.data().length ?? 0;
      if (r) setFollow(r.to >= n - 2);
    });
    chart.current = c;
    return () => c.remove();
  }, []);

  // ---------- candles + espaço futuro ----------
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.setData(candles.map((k) => ({ time: t(k.ts), open: k.open, high: k.high, low: k.low, close: k.close })));
    last.current = candles[candles.length - 1] ?? null;
    forming.current = null;
    if (last.current) {
      const base = t(last.current.ts);
      ghost.current?.setData(Array.from({ length: 12 }, (_, i) => ({ time: (base + (i + 1) * tf * 60) as UTCTimestamp })));
    }
  }, [candles, tf]);

  // ---------- candle em formação ----------
  useEffect(() => {
    const k = last.current;
    if (!series.current || !k || live == null) return;
    const nowT = floorTf(Date.now() / 1000, tf);
    if (nowT <= t(k.ts)) return;
    const f = forming.current?.time === nowT ? forming.current : { time: nowT, open: k.close, high: k.close, low: k.close };
    forming.current = { ...f, high: Math.max(f.high, live), low: Math.min(f.low, live) };
    series.current.update({ ...forming.current, time: nowT as UTCTimestamp, close: live });
    if (follow) chart.current?.timeScale().scrollToRealTime();
  }, [live, tf, follow]);

  // ---------- overlays ----------
  useEffect(() => {
    const ov = map?.overlays ?? {};
    for (const [k, s] of Object.entries(lines.current)) {
      const on = k.startsWith("ema") ? tg.ema : tg.bb;
      s.setData(on && ov[k] ? ov[k].map((p) => ({ time: p.time as UTCTimestamp, value: p.value })) : []);
    }
  }, [map, tg.ema, tg.bb]);

  // ---------- zonas S/R ----------
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    zoneLines.current.forEach((l) => s.removePriceLine(l));
    zoneLines.current = [];
    if (!tg.zones || !map) return;
    for (const z of map.zones) {
      zoneLines.current.push(s.createPriceLine({
        price: z.price, color: z.kind === "res" ? "#ff4d6d99" : "#22e39b99",
        lineWidth: Math.min(4, z.touches) as 1 | 2 | 3 | 4, lineStyle: LineStyle.Dashed, axisLabelVisible: true,
        title: `${z.kind === "res" ? "R" : "S"}×${z.touches}`,
      }));
    }
  }, [map, tg.zones]);

  // ---------- marcadores ----------
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (!tg.marks) { s.setMarkers([]); return; }
    const times = new Set(candles.map((k) => t(k.ts) as number));
    const m: SeriesMarker<Time>[] = [];
    for (const sg of signals) {
      const tm = t(sg.candle_ts);
      if (!times.has(tm)) continue;
      m.push(sg.approved
        ? { time: tm, position: sg.direction === "CALL" ? "belowBar" : "aboveBar", color: sg.direction === "CALL" ? "#22e39b" : "#ff4d6d",
          shape: sg.direction === "CALL" ? "arrowUp" : "arrowDown", text: `${sg.direction} ${sg.expiry}m` }
        : { time: tm, position: sg.direction === "CALL" ? "belowBar" : "aboveBar", color: "#4b5468", shape: "circle", size: 0.5 });
    }
    for (const tr of trades) {
      const tm = floorTf(Date.parse(tr.created_at) / 1000, tf) - tf * 60; // entrada registrada logo após o fechamento
      if (!times.has(tm)) continue;
      const col = tr.result === "win" ? "#22e39b" : tr.result === "loss" ? "#ff4d6d" : "#ff9f1c";
      m.push({ time: tm as UTCTimestamp, position: "inBar", color: col, shape: "square",
        text: tr.result === "win" ? "✔" : tr.result === "loss" ? "✘" : "•" });
    }
    m.sort((a, b) => (a.time as number) - (b.time as number));
    s.setMarkers(m);
  }, [candles, signals, trades, tg.marks, tf]);

  // ---------- selo de confirmação + faixa de expiração ----------
  const computeBand = useCallback(() => {
    const c = chart.current, s = series.current;
    if (!c || !s || !badge) { setBand(null); setPos(null); return; }
    const w = el.current?.clientWidth ?? 600;
    const ts = c.timeScale();
    const c1 = ts.timeToCoordinate(t(badge.entry_at));
    const c2 = ts.timeToCoordinate(t(badge.expires_at));
    // horário fora da área visível/espaço futuro: encosta à direita do último candle
    const lastX = ts.timeToCoordinate(t(candles[candles.length - 1]?.ts ?? badge.entry_at));
    const x1: number = c1 ?? (lastX ?? w - 120) + 12;
    const x2: number = c2 ?? x1 + 24;
    const price = badge.price ?? live ?? candles[candles.length - 1]?.close ?? null;
    const y = price != null ? s.priceToCoordinate(price) : null;
    setPos({ x: x1, y: y ?? 120, w });
    setBand(badge.kind === "canceled" ? null : { x1, x2, dir: badge.direction, label: "" });
  }, [badge?.kind, badge?.direction, badge?.entry_at, badge?.expires_at, badge?.price, live, candles]);
  useEffect(() => { computeBand(); }, [computeBand, now]);
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    c.timeScale().subscribeVisibleLogicalRangeChange(computeBand);
    return () => c.timeScale().unsubscribeVisibleLogicalRangeChange(computeBand);
  }, [computeBand]);

  // linha de preço da entrada (só quando é para entrar ou a operação está em curso)
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (entryLine.current) { s.removePriceLine(entryLine.current); entryLine.current = null; }
    if (!badge || (badge.kind !== "enter" && badge.kind !== "running")) return;
    const price = badge.price ?? live;
    if (price == null) return;
    entryLine.current = s.createPriceLine({
      price, color: badge.direction === "CALL" ? "#22e39b" : "#ff4d6d", lineWidth: 2, lineStyle: LineStyle.Solid,
      axisLabelVisible: true, title: `${badge.demo ? "SIMULAÇÃO " : ""}ENTRADA ${badge.direction}`,
    });
  }, [badge?.kind, badge?.direction, badge?.price, badge?.demo]);

  // ---------- contagem regressiva ----------
  const step = tf * 60;
  const left = Math.ceil(step - ((now / 1000) % step));
  const mm = Math.floor(left / 60), ss = left % 60;
  const hot = left <= (tf === 1 ? 10 : 30);

  return (
    <div className="chartbox" ref={wrap}>
      <div className="toolbar">
        <div className="seg">{timeframes.map((v) => <button key={v} className={v === tf ? "on" : ""} onClick={() => onTf(v)}>M{v}</button>)}</div>
        <div className="seg">
          <button className={tg.ema ? "on" : ""} onClick={() => toggle("ema")} title="EMA 9 / 21 / 50">EMAs</button>
          <button className={tg.bb ? "on" : ""} onClick={() => toggle("bb")} title="Bandas de Bollinger 20,2">Bollinger</button>
          <button className={tg.zones ? "on" : ""} onClick={() => toggle("zones")} title="Suportes e resistências do Mapeador">Zonas S/R</button>
          <button className={tg.marks ? "on" : ""} onClick={() => toggle("marks")} title="Sinais e suas entradas">Marcações</button>
        </div>
        <div className="seg">
          <button onClick={() => chart.current?.timeScale().fitContent()}>Ajustar</button>
          <button className={follow ? "on" : ""} onClick={() => { chart.current?.timeScale().scrollToRealTime(); setFollow(true); }}>Ao vivo ▸</button>
        </div>
        <div className={`countdown ${hot ? "hot" : ""}`} title="tempo para o candle fechar">
          fecha em <b>{mm}:{String(ss).padStart(2, "0")}</b>
        </div>
      </div>
      <div className="chart-area">
        <div ref={el} className="chart" />
        {band && badge && (
          <div className={`band ${band.dir.toLowerCase()} ${badge.kind === "pre" ? "pre" : ""}`}
            style={{ left: Math.min(band.x1, band.x2), width: Math.max(4, Math.abs(band.x2 - band.x1)) }} />
        )}
        {badge && pos && (
          <div className={`entry-badge ${badge.kind} ${badge.direction.toLowerCase()}`}
            style={{
              left: Math.min(Math.max(pos.x, 70), pos.w - 150),
              top: badge.direction === "CALL" ? pos.y + 10 : undefined,
              bottom: badge.direction === "PUT" ? `calc(100% - ${pos.y - 10}px)` : undefined,
            }}>
            <div className="eb-arrow">{badge.kind === "canceled" ? "✕" : badge.direction === "CALL" ? "▲" : "▼"}</div>
            <div className="eb-txt">
              {badge.demo && <em>SIMULAÇÃO</em>}
              <b>{badge.title}</b>
              <span>{badge.sub}</span>
            </div>
          </div>
        )}
        {map?.zones && tg.zones && map.zones.length === 0 && <div className="chart-note">sem zonas claras no período</div>}
      </div>
    </div>
  );
}
