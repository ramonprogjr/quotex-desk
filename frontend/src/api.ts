export type Signal = {
  id: string; asset: string; quotex: string; tf: number; setup: string; direction: "CALL" | "PUT";
  candle_ts: string; entry_at: string; valid_until: string; expiry: number; expires_at: string;
  price: number; approved: boolean; stake: number; p_hat: number; lo: number; breakeven: number;
  n: number; payout: number; agents: Record<string, string>;
  votes: Record<string, number>; p_model: number | null; pre: boolean;
};
export type PreSignal = {
  asset: string; quotex: string; tf: number; candle_ts: string; closes_at: string; seconds_left: number;
  signals: Signal[]; seen: Record<string, boolean>;
};
export type Zone = { price: number; kind: "res" | "sup"; touches: number; dist_atr: number | null };
export type MarketMap = {
  zones: Zone[]; p_model: number | null; forming?: boolean;
  model: { trained_at: string; thr: number } | null;
  snapshot: { votes: Record<string, number>; volatility_pct: number | null; adx: number | null; rsi: number | null;
    stoch_k: number | null; streak: number | null } | null;
  overlays: Record<string, { time: number; value: number }[]>;
};
export type BotState = {
  enabled: boolean; status: string; account: "DEMO" | "REAL" | null; balance: number | null; seen: string | null;
  checks: Record<string, string | null> | null; process: boolean; timeframes: number[];
};
export type TestScore = {
  n: number; wins: number; losses: number; pushes: number; pending: number; unknown: number;
  rate: number | null; lo: number | null; hi: number | null; payout: number | null; breakeven: number | null;
  proven: boolean; pnl: number;
};
export type TestTrade = {
  id: string; at: string; asset: string; quotex: string; tf: number; expiry: number; direction: "CALL" | "PUT";
  stake: number; payout: number | null; setup: string; approved: boolean; result: string; pnl?: number;
};
export type TestReport = {
  total: TestScore; by: Record<"setup" | "asset" | "tf", (TestScore & { key: string })[]>; trades: TestTrade[];
};
export type LabSeg ={ w: number; n: number; rate: number | null; lo?: number; hi?: number };
export type LabReport = {
  asset: string; tf: number; seed: number; status: "running" | "done"; started: string; finished?: string;
  breakeven: number; splits: Record<"train" | "val" | "vault", [string, string]>;
  generations: { gen: number; best: number; mean: number; hall: number; tested: number;
    funnel: { generated: number; n_ok: number; val_pass: number };
    species: Record<string, { count: number; best: number; best_rate: number | null }> }[];
  finalists: { id: string; family: string; promoted: boolean; fitness: number; train: LabSeg; val: LabSeg; vault: LabSeg;
    dna: { gene: string; value: number | string; min: number; max: number; norm: number }[] }[];
  baselines: (LabSeg & { name: string; evolved?: boolean; promoted?: boolean })[];
  total_tested?: number; hall_size?: number; z_bonferroni?: number; promoted?: string[];
};
export type Catalog = {
  window: [string, string] | null; expiry?: number;
  rows: { setup: string; n: number; wins: number; winrate: number | null; breakeven: number; last: number[] }[];
};
export type Candle = { ts: string; open: number; high: number; low: number; close: number };
export type LogEv = { at: string; agent: string; asset: string | null; tf?: number | null; text: string; level: string };
export type Trade = {
  id: number; created_at: string; asset: string; direction: "CALL" | "PUT"; expiry: number;
  stake: number; payout: number; result: "pending" | "win" | "loss" | "push"; pnl: number | null;
  setup: string | null; signal_id: string | null; followed_signal: number; p_pred: number | null;
  notes: string | null; quotex_price?: number | null; quotex_exit?: number | null;
};
export type Block = {
  n: number; wins: number; winrate: number | null; lo: number; hi: number; breakeven: number;
  pnl?: number; edge_proven?: boolean; predicted?: number | null;
};
export type Stats = {
  journal: {
    overall: Block; setup: Record<string, Block>; asset: Record<string, Block>;
    hour: Record<string, Block>; followed: Record<string, Block>;
    price_divergence_bps: number | null; validation: { target_n: number; n: number; passed: boolean };
  };
  forward: { approved: Block; vetoed: Block; by_setup: Record<string, Block>;
    presignals?: { announced: number; confirmed: number; canceled: number; surprise: number } };
  feed_vs_quotex: { n: number; agree: number; rate: number | null; entry_bps: number | null; exit_bps: number | null;
    n_prices: number; mismatches: { id: number; quotex: string; feed: string }[] };
};
export type Risk = {
  bankroll: number; day_start_bankroll: number; day_pnl: number; trades_today: number;
  consecutive_losses: number; last_loss_at: string | null; open_trades: number;
};
export type State = {
  config: Record<string, any> & { payout: number; max_trades_day: number; daily_stop_frac: number; timezone: string };
  assets: Record<string, { quotex: string; source: string; unavailable: string | null; payout: number;
    payouts: Record<string, number>; price: number | null }>;
  payouts_updated_at: string | null;
  breakers: Record<string, { n: number; wins: number; winrate: number; hi: number; breakeven: number; reason: string }>;
  watch: string[]; bankroll_start: number; risk: Risk; risk_block: string | null;
  approved_cells: Array<{ asset: string; tf: number; setup: string; expiry: number; bucket: number; p_hat: number; lo: number; n: number }>;
  priors_meta: Record<string, any>;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export const api = {
  state: () => req<State>("/api/state"),
  candles: (a: string, tf: number) => req<Candle[]>(`/api/candles/${a}?tf=${tf}&n=300`),
  spectrum: (a: string, tf: number) =>
    req<{ ts: string[]; setups: Record<string, number[]> }>(`/api/spectrum/${a}?tf=${tf}&n=90`),
  map: (a: string, tf: number) => req<MarketMap>(`/api/map/${a}?tf=${tf}`),
  catalog: (a: string, tf: number) => req<Catalog>(`/api/catalog/${a}?tf=${tf}`),
  presignals: () => req<PreSignal[]>("/api/presignals"),
  bot: () => req<BotState>("/api/bot"),
  botLaunch: () => req("/api/bot/launch", { method: "POST" }),
  botStop: () => req("/api/bot/stop", { method: "POST" }),
  botEnable: (on: boolean) => req<BotState>(`/api/bot/enable?on=${on}`, { method: "POST" }),
  botTest: (asset: string, direction: "CALL" | "PUT", tf: number) =>
    req(`/api/bot/test?asset=${asset}&direction=${direction}&tf=${tf}`, { method: "POST" }),
  botDiagnose: () => req("/api/bot/diagnose", { method: "POST" }),
  testReport: () => req<TestReport>("/api/bot/test_report"),
  lab: (a: string, tf: number) => req<{ running: boolean; report: LabReport | null; live: string[] }>(`/api/lab/${a}?tf=${tf}`),
  runLab: (a: string, tf: number, gens: number) => req(`/api/lab/run?asset=${a}&tf=${tf}&gens=${gens}`, { method: "POST" }),
  signals: () => req<Signal[]>("/api/signals?limit=60"),
  activity: () => req<LogEv[]>("/api/activity?limit=120"),
  journal: () => req<Trade[]>("/api/journal"),
  stats: () => req<Stats>("/api/stats"),
  addTrade: (t: Partial<Trade> & { quotex_price?: number | null; tf?: number }) => req<Trade>("/api/journal", { method: "POST", body: JSON.stringify(t) }),
  setResult: (id: number, result: Trade["result"], quotex_exit?: number | null) =>
    req<Trade>(`/api/journal/${id}`, { method: "PATCH", body: JSON.stringify({ result, quotex_exit: quotex_exit || null }) }),
  resetBreaker: (key: string) => req(`/api/breakers/reset?key=${encodeURIComponent(key)}`, { method: "POST" }),
  delTrade: (id: number) => req(`/api/journal/${id}`, { method: "DELETE" }),
  settings: (s: { bankroll_start?: number; payouts?: Record<string, Record<string, number>>; watch?: string[] }) =>
    req<State>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
};

export function connectWS(onMsg: (m: { type: string; data?: any }) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => onMsg(JSON.parse(e.data));
    ws.onclose = () => { if (!closed) setTimeout(open, 2000); };
  };
  open();
  return () => { closed = true; ws?.close(); };
}

export const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
export const money = (x: number | null | undefined) =>
  x == null ? "—" : x.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export const hhmmss = (iso: string, tz?: string) =>
  new Date(iso).toLocaleTimeString("pt-BR", { timeZone: tz, hour12: false });
