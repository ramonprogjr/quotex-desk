import { useState } from "react";
import { api, hhmmss, money, pct, type PreSignal, type Signal, type State } from "./api";

export const SETUP_PT: Record<string, string> = {
  rsi_bb_reversal: "RSI + Bollinger", engulfing_reversal: "Engolfo", breakout_momentum: "Rompimento",
  ema_pullback: "Pullback EMA", sr_rejection: "Rejeição S/R", pinbar_zone: "Pin bar na zona", mhi: "MHI",
  confluencia: "Confluência (IA)",
};

type Props = {
  state: State | null; asset: string; tf: number; now: number;
  pre: PreSignal | null; active: Signal | null; preResult: string | null;
  hasPending: boolean; onDone: () => void;
};

/** Painel de entrada: pré-sinal → sinal ativo → botões CALL/PUT com o valor certo. */
export function QuickEntry({ state, asset, tf, now, pre, active, preResult, hasPending, onDone }: Props) {
  const [stakeMode, setStakeMode] = useState<"kelly" | "min" | "min2">("kelly");
  const [busy, setBusy] = useState(false);
  const [qxPrice, setQxPrice] = useState("");
  const tz = state?.config.timezone ?? "America/Sao_Paulo";
  const cfg = state?.config;
  const minStake: number = cfg?.min_stake ?? 1;
  const expiry: number = (cfg?.expiries?.[String(tf)] ?? [tf])[0];
  const blocked = state?.risk_block ?? null;
  const preTop = pre?.signals.find((s) => s.approved) ?? null;
  const preOthers = pre?.signals.filter((s) => !s.approved) ?? [];

  const suggested = active?.stake || preTop?.stake || 0;
  const cap = (state?.risk.bankroll ?? 0) * (cfg?.max_stake_frac ?? 0.02);
  const stake = Math.min(
    stakeMode === "kelly" ? (suggested || minStake) : stakeMode === "min" ? minStake : 2 * minStake,
    Math.max(cap, minStake),
  );

  const left = active ? Math.max(0, Math.round((Date.parse(active.valid_until) - now) / 1000)) : 0;
  const preLeft = pre ? Math.max(0, Math.round((Date.parse(pre.closes_at) - now) / 1000)) : 0;

  const enter = async (direction: "CALL" | "PUT") => {
    setBusy(true);
    try {
      const sig = active && active.direction === direction ? active : null;
      const px = Number(qxPrice.replace(",", "."));
      await api.addTrade({ asset, direction, tf, expiry: sig?.expiry ?? expiry, stake,
        payout: sig?.payout ?? state?.assets[asset]?.payouts?.[String(tf)] ?? state?.assets[asset]?.payout,
        signal_id: sig?.id ?? null, quotex_price: px > 0 ? px : null });
      setQxPrice("");
      onDone();
    } finally { setBusy(false); }
  };

  let stage: "idle" | "pre" | "active" = "idle";
  if (active && left > 0) stage = "active";
  else if (preTop && preLeft > 0) stage = "pre";

  return (
    <div className="qe">
      <div className={`qe-stage ${stage} ${(active ?? preTop)?.direction.toLowerCase() ?? ""}`}>
        {stage === "active" && active && (
          <>
            <div className="qe-tag">SINAL ATIVO · M{active.tf} · {SETUP_PT[active.setup] ?? active.setup}</div>
            <div className="qe-dir">{active.direction}</div>
            <div className="qe-meta">
              <span>expira <b>{hhmmss(active.expires_at, tz).slice(0, 5)}</b> (modo horário)</span>
              <span>valor <b>{money(active.stake)}</b></span>
            </div>
            <div className="qe-timer"><i style={{ width: `${(left / Math.max(1, (Date.parse(active.valid_until) - Date.parse(active.entry_at)) / 1000)) * 100}%` }} />entre em {left}s</div>
            <div className="qe-why">{pct(active.p_hat)} histórico (inf. {pct(active.lo)}) · empate {pct(active.breakeven)}{active.p_model != null ? ` · IA ${pct(Math.max(active.p_model, 1 - active.p_model), 0)}` : ""}</div>
          </>
        )}
        {stage === "pre" && preTop && pre && (
          <>
            <div className="qe-tag blink">PRÉ-SINAL · M{pre.tf} · {SETUP_PT[preTop.setup] ?? preTop.setup}</div>
            <div className="qe-dir">{preTop.direction}?</div>
            <div className="qe-meta">
              <span>candle fecha em <b>{preLeft}s</b></span>
              <span>expiraria <b>{hhmmss(preTop.expires_at, tz).slice(0, 5)}</b></span>
            </div>
            <div className="qe-why">Abra a Quotex em <b>{pre.quotex}</b>, deixe {preTop.direction} · {preTop.expiry} min · {money(preTop.stake)} pronto. <b>Só clique quando confirmar.</b></div>
          </>
        )}
        {stage === "idle" && (
          <>
            <div className="qe-tag">AGUARDANDO</div>
            <div className="qe-dir dim">—</div>
            <div className="qe-why">
              {preResult ?? (preOthers.length ? `${preOthers.length} setup(s) se formando, mas vetado(s): ${preOthers.map((s) => SETUP_PT[s.setup] ?? s.setup).join(", ")}` :
                "Nenhum setup com vantagem se formando agora.")}
            </div>
          </>
        )}
      </div>

      <div className="qe-stake">
        <span>valor</span>
        {(["kelly", "min", "min2"] as const).map((m) => (
          <button key={m} className={stakeMode === m ? "on" : ""} onClick={() => setStakeMode(m)}>
            {m === "kelly" ? `sugerido${suggested ? ` ${money(suggested)}` : ""}` : m === "min" ? `mín ${money(minStake)}` : `2× ${money(2 * minStake)}`}
          </button>
        ))}
        <b>{money(stake)}</b>
      </div>

      <label className="qe-px" title="Opcional: o preço de entrada que a Quotex mostrou. Serve para medir a diferença entre o feed e a Quotex.">
        preço na Quotex <input inputMode="decimal" placeholder="opcional" value={qxPrice} onChange={(e) => setQxPrice(e.target.value)} />
        <span>payout M{tf} {pct(state?.assets[asset]?.payouts?.[String(tf)] ?? state?.assets[asset]?.payout ?? null, 0)}</span>
      </label>

      <div className="qe-buttons">
        <button className={`call ${active?.direction === "CALL" && stage === "active" ? "hl" : ""}`} disabled={busy || hasPending} onClick={() => enter("CALL")}>
          ▲ CALL<small>{expiry} min</small>
        </button>
        <button className={`put ${active?.direction === "PUT" && stage === "active" ? "hl" : ""}`} disabled={busy || hasPending} onClick={() => enter("PUT")}>
          ▼ PUT<small>{expiry} min</small>
        </button>
      </div>
      <p className="note">
        {hasPending ? "Há operação aberta: marque o resultado no diário." :
          blocked ? `⛔ ${blocked} — registrar ainda é possível, mas o risco recomenda parar.` :
            "Os botões REGISTRAM a entrada que você fez na Quotex (não enviam ordem)."}
      </p>
    </div>
  );
}
