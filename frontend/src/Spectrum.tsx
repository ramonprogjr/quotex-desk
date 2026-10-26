import { useEffect, useRef } from "react";

const LABELS: Record<string, string> = {
  rsi_bb_reversal: "RSI+BB",
  engulfing_reversal: "ENGOLFO",
  breakout_momentum: "ROMPIM.",
  ema_pullback: "PULLBACK",
};

export function Spectrum({ data }: { data: { ts: string[]; setups: Record<string, number[]> } }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const names = Object.keys(data.setups);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    const cols = data.ts.length || 1;
    const rows = names.length || 1;
    const cw = w / cols, rh = h / rows;
    names.forEach((n, r) => {
      data.setups[n].forEach((v, c) => {
        // fundo "ruído" sutil + célula acesa quando o setup dispara
        g.fillStyle = v > 0 ? "#22e39b" : v < 0 ? "#ff4d6d" : (c + r) % 3 ? "#121826" : "#151c2b";
        g.fillRect(c * cw + 0.5, r * rh + 1, Math.max(cw - 1, 1), rh - 2);
      });
    });
  }, [data, names.join()]);

  return (
    <div className="spectrum">
      <div className="spec-labels">{names.map((n) => <span key={n}>{LABELS[n] ?? n}</span>)}</div>
      <canvas ref={ref} />
    </div>
  );
}
