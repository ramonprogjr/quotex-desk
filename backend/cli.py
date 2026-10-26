"""Linha de comando (sem --tf roda M1 e M5).

    python -m backend.cli download BTCUSD --days 365
    python -m backend.cli backtest BTCUSD [--tf 5]
    python -m backend.cli walkforward BTCUSD [--tf 5]
    python -m backend.cli priors BTCUSD
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .agents import confluence, prior
from .agents.edge import judge
from .agents.spotter import run_all
from .assets import get_asset
from .backtest import walkforward
from .backtest.binary_bt import equity_curve, max_drawdown, simulate, summarize
from .config import load_config
from .data import resample, store


def _load(key: str, tf: int = 1) -> pd.DataFrame:
    df = store.load(key)
    if df is None or df.empty:
        sys.exit(f"Sem dados de {key}. Rode antes: python -m backend.cli download {key}")
    return resample(df, tf)


def _tfs(a, cfg) -> list[int]:
    return [a.tf] if a.tf else cfg.timeframes


def _extra(key: str, tf: int) -> dict[str, pd.Series]:
    """Confluência fora da amostra, se já foi treinada (cli train)."""
    s = confluence.load_oos(key, tf)
    return {confluence.NAME: s} if s is not None else {}


def cmd_download(a) -> None:
    get_asset(a.asset)
    last = [None]

    def progress(ts):
        day = ts.date()
        if day != last[0]:
            last[0] = day
            print(f"  … {day}", end="\r", flush=True)

    df = store.update(a.asset, days=a.days, progress=progress)
    print(f"\n{a.asset}: {len(df):,} candles de 1 min de {df.index[0]} até {df.index[-1]}")


def cmd_backtest(a) -> None:
    cfg = load_config()
    payout = a.payout or cfg.payout
    be = 1 / (1 + payout)
    fmt = {"acerto": "{:.2%}".format, "inf": "{:.2%}".format, "sup": "{:.2%}".format,
           "EV/1": "{:+.3f}".format, "banca(100)": "{:.2f}".format, "maxDD": "{:.1%}".format}
    for tf in _tfs(a, cfg):
        df = _load(a.asset, tf)
        signals = run_all(df, include_baseline=True, extra=_extra(a.asset, tf))
        rows = []
        for setup in signals.columns:
            for e in cfg.expiries_for(tf):
                tr = simulate(df, signals[setup], cfg.expiry_candles(e, tf), tf=tf)
                if tr.empty:
                    continue
                s = summarize(tr, payout, cfg.z)
                eq = equity_curve(tr, payout, stake_frac=0.01)
                rows.append({"setup": setup, "exp": e, "ops": s.trades, "acerto": s.winrate,
                             "inf": s.lo, "sup": s.hi, "EV/1": s.ev_per_unit,
                             "banca(100)": eq.iloc[-1], "maxDD": max_drawdown(eq),
                             "vantagem": "SIM" if s.edge_proven else "não"})
        print(f"\n{a.asset} M{tf} · {len(df):,} candles · {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}")
        print(f"payout {payout:.0%} → empate em {be:.2%} · intervalo de Wilson z={cfg.z}\n")
        print(pd.DataFrame(rows).to_string(index=False, formatters=fmt))
    print("\nObs.: tabela sem filtro de horário. Muitas combinações testadas → alguma pode passar por sorte;"
          " o walk-forward e o Edge (descoberta + confirmação) existem para filtrar isso.")


def cmd_walkforward(a) -> None:
    cfg = load_config()
    if a.payout:
        cfg.payout = a.payout
    for tf in _tfs(a, cfg):
        df = _load(a.asset, tf)
        r = walkforward.run(df, cfg, tf, train_days=a.train, test_days=a.test, extra=_extra(a.asset, tf))
        print(f"\n{a.asset} M{tf} · walk-forward treino {a.train}d / teste {a.test}d · "
              f"empate {r.get('breakeven', 0):.2%}\n")
        for w in r["windows"]:
            wr = f"{w['winrate']:.1%}" if w["winrate"] is not None else "—"
            print(f"  {w['start'][:10]}  células={w['cells_selected']:>3}  ops={w['trades']:>5}  acerto={wr}")
        o = r["oos"]
        if not o:
            print("\nNenhuma célula foi selecionada em nenhuma janela: nada com vantagem para operar.")
            continue
        verdict = "VANTAGEM FORA DA AMOSTRA" if o["edge_proven"] else "SEM vantagem comprovada fora da amostra"
        print(f"\nFora da amostra: {o['trades']} ops · acerto {o['winrate']:.2%} "
              f"(inf. {o['lo']:.2%}) · EV/1 {o['ev_per_unit']:+.3f} → {verdict}")


def cmd_priors(a) -> None:
    cfg = load_config()
    key = a.asset.upper()
    for tf in _tfs(a, cfg):
        df = _load(key, tf)
        cells = prior.build(df, key, cfg, tf, extra=_extra(key, tf))
        prior.save(cells, {f"{key}_M{tf}_range": [str(df.index[0]), str(df.index[-1])]})
        approved = [c for c in cells.values() if judge(c, cfg.payout, cfg).approved]
        print(f"{key} M{tf}: {len(cells)} células salvas em {prior.PRIORS_PATH.name}; "
              f"{len(approved)} aprovadas com payout {cfg.payout:.0%}")
        for c in approved:
            v = judge(c, cfg.payout, cfg)
            b = ("qualquer hora" if c["bucket"] == -1 else
                 f"{c['bucket'] * cfg.hour_bucket:02d}h-{(c['bucket'] + 1) * cfg.hour_bucket:02d}h UTC")
            print(f"  ✔ {c['setup']} {c['expiry']}min {b}: {v.reason}")


def cmd_train(a) -> None:
    """Treina a Confluência em walk-forward, mostra o resultado fora da amostra e salva o modelo."""
    cfg = load_config()
    key = a.asset.upper()
    be = 1 / (1 + cfg.payout)
    for tf in _tfs(a, cfg):
        df = _load(key, tf)
        expiry = cfg.expiries_for(tf)[0]
        ec = cfg.expiry_candles(expiry, tf)
        print(f"\n{key} M{tf} · Confluência · expiração {expiry} min · walk-forward mensal")
        wf = confluence.walkforward(df, ec, progress=lambda f: print(
            f"  {f['start']}  treino={f['train']:>7,}  teste={f['test']:>6,}  sinais={f['fired']:>5}"))
        confluence.save_oos(key, tf, wf)
        tr = simulate(df, wf.signal, ec, tf=tf)
        if tr.empty:
            print("  nenhum sinal fora da amostra")
        else:
            s = summarize(tr, cfg.payout, cfg.z)
            verdict = "VANTAGEM" if s.edge_proven else "sem vantagem comprovada"
            print(f"  fora da amostra: {s.trades} ops · acerto {s.winrate:.2%} (inf. {s.lo:.2%}) · "
                  f"empate {be:.2%} → {verdict}")
            # acerto por faixa de confiança (fora da amostra)
            p = wf.proba.reindex(tr.index)
            conf = (p - 0.5).abs()
            bins = pd.qcut(conf, 4, duplicates="drop")
            g = tr.assign(bin=bins).query("result != 0").groupby("bin", observed=True)["result"]
            for b, r in g:
                print(f"    confiança {b}: {len(r):>5} ops · acerto {(r == 1).mean():.2%}")
        b = confluence.train_final(df, key, tf, expiry, ec)
        print(f"  modelo final salvo ({confluence.model_path(key, tf).name}, limiar {b['thr']:.4f})")
    print("\nAgora rode: python -m backend.cli priors", key)


def cmd_delay(a) -> None:
    """Quanto o acerto cai entrando 5/10/20/30 s depois (expiração no mesmo horário)."""
    from .backtest.delay import delay_table

    cfg = load_config()
    key = a.asset.upper()
    last = [None]

    def progress(ts):
        h = ts.floor("6h")
        if h != last[0]:
            last[0] = h
            print(f"  … dados finos {h:%Y-%m-%d %H}h", end="\r", flush=True)

    store.update(key, days=365)  # garante candles de 1 min até agora (incremental)
    fine = store.update_fine(key, days=a.days, progress=progress)
    print(f"\n{key}: {len(fine):,} candles finos de {fine.index[0]:%Y-%m-%d %H:%M} a {fine.index[-1]:%Y-%m-%d %H:%M}")
    be = 1 / (1 + cfg.payout)
    for tf in _tfs(a, cfg):
        df = _load(key, tf)
        expiry = cfg.expiries_for(tf)[0]
        signals = run_all(df, include_baseline=True, extra=_extra(key, tf))
        t = delay_table(df, fine, signals, tf, cfg.expiry_candles(expiry, tf))
        if t.empty:
            print(f"\nM{tf}: nenhum sinal no período dos dados finos")
            continue
        pv = t.pivot(index="setup", columns="delay_s", values="winrate")
        nn = t[t["delay_s"] == 0].set_index("setup")["n"]
        print(f"\n{key} M{tf} · expiração {expiry} min · empate {be:.2%} · acerto por atraso de entrada (s)")
        out = pv.map(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
        out.columns = [f"+{c}s" for c in out.columns]
        out.insert(0, "ops", nn)
        print(out.to_string())
    print("\nAtenção: período curto (dias) → diferenças de 1–2 pontos podem ser só ruído."
          " Compare a QUEDA entre colunas, não o nível.")


def cmd_evolve(a) -> None:
    """Laboratório Evolutivo: cria/testa/elimina estratégias e promove só quem passa no cofre."""
    from .lab import evolve as E

    cfg = load_config()
    key = a.asset.upper()
    for tf in _tfs(a, cfg):
        df = _load(key, tf)
        ev_cfg = E.EvoConfig(gens=a.gens, seed=a.seed, pop=a.pop)
        print(f"\n{key} M{tf} · Laboratório Evolutivo · {ev_cfg.pop} por geração · {ev_cfg.gens} gerações · semente {ev_cfg.seed}")
        r = E.run(df, key, tf, cfg.payout, ev_cfg, extra_baselines=_extra(key, tf), progress=lambda g: print(
            f"  ger {g['gen']:>3}  melhor z={g['best']:+.2f}  média={g['mean']:+.2f}  "
            f"n ok={g['funnel']['n_ok']:>3}  validação={g['funnel']['val_pass']:>3}  hall={g['hall']:>4}  testadas={g['tested']:>5}"))
        print(f"\n  {r['total_tested']} configurações testadas · {r['hall_size']} passaram na validação · "
              f"{len(r['finalists'])} finalistas no cofre (z Bonferroni {r['z_bonferroni']:.2f})")
        for f in r["finalists"]:
            v = f["vault"]
            mark = "✔ PROMOVIDA" if f["promoted"] else "✘ reprovada"
            rate = f"{v['rate']:.1%}" if v["rate"] is not None else "—"
            print(f"  {mark}  {f['id']:<18} validação {f['val']['rate']:.1%} (n={f['val']['n']})  "
                  f"cofre {rate} (inf. {v['lo']:.1%}, n={v['n']})")
        print(f"\n  Referências no período do cofre (empate {r['breakeven']:.2%}):")
        for b in r["baselines"]:
            if b.get("evolved"):
                continue
            rate = f"{b['rate']:.1%}" if b["rate"] is not None else "—"
            print(f"    {b['name']:<28} {rate:>6}  n={b['n']}")
    print("\nPromovidas entram no painel após recarregar (POST /api/reload ou reiniciar).")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="backend.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download"); d.add_argument("asset"); d.add_argument("--days", type=int, default=365)
    b = sub.add_parser("backtest"); b.add_argument("asset"); b.add_argument("--payout", type=float)
    w = sub.add_parser("walkforward"); w.add_argument("asset"); w.add_argument("--payout", type=float)
    w.add_argument("--train", type=int, default=30); w.add_argument("--test", type=int, default=7)
    pr = sub.add_parser("priors"); pr.add_argument("asset")
    t = sub.add_parser("train"); t.add_argument("asset")
    dl = sub.add_parser("delay"); dl.add_argument("asset"); dl.add_argument("--days", type=int, default=14)
    evo = sub.add_parser("evolve"); evo.add_argument("asset")
    evo.add_argument("--gens", type=int, default=40); evo.add_argument("--seed", type=int, default=2026)
    evo.add_argument("--pop", type=int, default=96)
    for sp in (b, w, pr, t, dl, evo):
        sp.add_argument("--tf", type=int, choices=[1, 5], help="só um timeframe (padrão: todos do config)")
    a = p.parse_args()
    {"download": cmd_download, "backtest": cmd_backtest, "walkforward": cmd_walkforward,
     "priors": cmd_priors, "train": cmd_train, "delay": cmd_delay, "evolve": cmd_evolve}[a.cmd](a)


if __name__ == "__main__":
    main()
