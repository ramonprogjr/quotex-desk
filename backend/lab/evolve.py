"""Laboratório Evolutivo: cria, testa e elimina estratégias binárias sozinho.

Inspirado no SETS Machine, com um protocolo mais rígido:
  TREINO (60%)    → a evolução só enxerga isto (fitness = z contra o empate)
  VALIDAÇÃO (20%) → portão: quem não prova vantagem aqui não entra no "hall"
  COFRE (20%)     → nunca é olhado durante a evolução; no fim, no máximo K finalistas
                    passam UMA vez, com z corrigido por Bonferroni para K testes.
Só quem passa no cofre é promovido — e ainda assim entra no mesmo Edge/Closer/disjuntor.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import NormalDist

import numpy as np
import pandas as pd

from ..agents import prior
from ..agents.spotter import SETUPS, random_baseline
from ..backtest.binary_bt import simulate
from ..config import DATA_DIR
from ..stats import breakeven, wilson
from . import genome as G


@dataclass
class EvoConfig:
    pop: int = 96
    immigrants: int = 8
    gens: int = 40
    mut_p: float = 0.18
    mut_scale: float = 0.15
    tournament: int = 3
    elite_per_species: int = 2
    min_train_n: int = 150
    min_val_n: int = 150          # igual ao min_n do Edge (descoberta = validação)
    min_vault_n: int = 64         # igual ao mínimo de confirmação do Edge
    finalists_per_species: int = 2
    z: float = 1.645
    seed: int = 2026
    splits: tuple[float, float] = (0.6, 0.8)


def lab_path(asset: str, tf: int):
    return DATA_DIR / f"lab_{asset.upper()}_M{tf}.json"


def evolved_path(asset: str, tf: int):
    return DATA_DIR / f"evolved_{asset.upper()}_M{tf}.json"


def gid(g: dict) -> str:
    h = hashlib.sha1(json.dumps(G.key(g)).encode()).hexdigest()[:6]
    return f"evo_{g['family'][:3]}_{h}"


def _counts(res: np.ndarray) -> tuple[int, int]:
    return int((res == 1).sum()), int((res != 0).sum())


def _z(w: int, n: int, be: float) -> float:
    return (w - n * be) / math.sqrt(n * be * (1 - be)) if n else 0.0


# ---------------- operadores genéticos ----------------
def crossover(a: dict, b: dict, rng: np.random.Generator) -> dict:
    child = {"family": a["family"]}
    for k in G.specs(a["family"]):
        child[k] = a[k] if rng.random() < 0.5 else b[k]
    child["sess"] = [x if rng.random() < 0.5 else y for x, y in zip(a["sess"], b["sess"])]
    return child


def mutate(g: dict, rng: np.random.Generator, p: float, scale: float) -> dict:
    out = dict(g)
    for k, (lo, hi, _typ) in G.specs(g["family"]).items():
        if rng.random() < p:
            out[k] = out[k] + rng.normal(0, scale * (hi - lo))
    out["sess"] = [(not s) if rng.random() < p / 2 else s for s in g["sess"]]
    return G.clip(out)


def _tournament(members: list[dict], fit: dict, rng: np.random.Generator, k: int) -> dict:
    picks = [members[int(rng.integers(len(members)))] for _ in range(k)]
    return max(picks, key=lambda g: fit[G.key(g)]["fitness"])


# ---------------- avaliação ----------------
class Evaluator:
    def __init__(self, df: pd.DataFrame, tf: int, payout: float, cfg: EvoConfig):
        self.df, self.tf, self.cfg = df, tf, cfg
        self.be = breakeven(payout)
        self.F = G.Features(df)
        n = len(df)
        self.cut1, self.cut2 = df.index[int(n * cfg.splits[0])], df.index[int(n * cfg.splits[1])]
        self.cache: dict[tuple, dict] = {}

    def trades(self, g: dict) -> pd.DataFrame:
        return simulate(self.df, pd.Series(G.signal(g, self.F), index=self.df.index), 1, tf=self.tf)

    def evaluate(self, g: dict) -> dict:
        """Métricas de TREINO e VALIDAÇÃO. O cofre não é calculado aqui."""
        k = G.key(g)
        if k in self.cache:
            return self.cache[k]
        tr = self.trades(g)
        res, t = tr["result"].to_numpy(), tr.index
        tw, tn = _counts(res[t < self.cut1])
        vw, vn = _counts(res[(t >= self.cut1) & (t < self.cut2)])
        z = _z(tw, tn, self.be)
        fitness = z if tn >= self.cfg.min_train_n else z * tn / self.cfg.min_train_n - 3
        vlo = wilson(vw, vn, self.cfg.z)[0]
        r = {"fitness": fitness, "train": {"w": tw, "n": tn, "rate": tw / tn if tn else None},
             "val": {"w": vw, "n": vn, "rate": vw / vn if vn else None, "lo": vlo},
             "n_ok": tn >= self.cfg.min_train_n,
             "val_pass": tn >= self.cfg.min_train_n and vn >= self.cfg.min_val_n and vlo > self.be}
        self.cache[k] = r
        return r

    def vault(self, sig: pd.Series, z: float) -> dict:
        tr = simulate(self.df, sig.reindex(self.df.index).fillna(0), 1, tf=self.tf)
        res = tr["result"].to_numpy()[tr.index >= self.cut2]
        w, n = _counts(res)
        lo, hi = wilson(w, n, z)
        return {"w": w, "n": n, "rate": w / n if n else None, "lo": lo, "hi": hi}


# ---------------- evolução ----------------
def run(df: pd.DataFrame, asset: str, tf: int, payout: float, cfg: EvoConfig | None = None,
        extra_baselines: dict[str, pd.Series] | None = None, progress=None, save: bool = True) -> dict:
    cfg = cfg or EvoConfig()
    rng = np.random.default_rng(cfg.seed)
    ev = Evaluator(df, tf, payout, cfg)
    per_species = (cfg.pop - cfg.immigrants) // len(G.FAMILIES)
    pop = [G.random_genome(rng, f) for f in G.FAMILIES for _ in range(per_species)]
    pop += [G.random_genome(rng) for _ in range(cfg.immigrants)]
    hall: dict[tuple, dict] = {}
    report = {
        "asset": asset.upper(), "tf": tf, "seed": cfg.seed, "config": asdict(cfg), "breakeven": ev.be,
        "status": "running", "started": datetime.now(timezone.utc).isoformat(),
        "splits": {"train": [str(df.index[0]), str(ev.cut1)], "val": [str(ev.cut1), str(ev.cut2)],
                   "vault": [str(ev.cut2), str(df.index[-1])]},
        "generations": [], "finalists": [], "baselines": [],
    }

    for gen in range(cfg.gens):
        scored = [(g, ev.evaluate(g)) for g in pop]
        fit = {G.key(g): r for g, r in scored}
        for g, r in scored:
            if r["val_pass"]:
                hall[G.key(g)] = {"genome": g, **r}
        fits = [r["fitness"] for _, r in scored]
        by_sp = {}
        for f in G.FAMILIES:
            members = [(g, r) for g, r in scored if g["family"] == f]
            if members:
                bg, br = max(members, key=lambda x: x[1]["fitness"])
                by_sp[f] = {"count": len(members), "best": br["fitness"], "best_rate": br["train"]["rate"]}
        report["generations"].append({
            "gen": gen + 1, "best": max(fits), "mean": float(np.mean(fits)),
            "funnel": {"generated": len(scored), "n_ok": sum(r["n_ok"] for _, r in scored),
                       "val_pass": sum(r["val_pass"] for _, r in scored)},
            "species": by_sp, "hall": len(hall), "tested": len(ev.cache),
        })
        if progress:
            progress(report["generations"][-1])
        if save:
            _write(report)

        # próxima geração: cotas por espécie + elite + filhos + imigrantes
        nxt: list[dict] = []
        for f in G.FAMILIES:
            members = sorted([g for g, _ in scored if g["family"] == f], key=lambda g: fit[G.key(g)]["fitness"],
                             reverse=True)
            if len(members) < 2:
                members = members + [G.random_genome(rng, f) for _ in range(2)]
                for g in members:
                    fit.setdefault(G.key(g), ev.evaluate(g))
            nxt += members[:cfg.elite_per_species]
            while sum(g["family"] == f for g in nxt) < per_species:
                a = _tournament(members, fit, rng, cfg.tournament)
                b = _tournament(members, fit, rng, cfg.tournament)
                nxt.append(mutate(crossover(a, b, rng), rng, cfg.mut_p, cfg.mut_scale))
        nxt += [G.random_genome(rng) for _ in range(cfg.immigrants)]
        pop = nxt

    # ---------------- finalistas → cofre (uma única vez) ----------------
    finalists = []
    for f in G.FAMILIES:
        cand = sorted([h for h in hall.values() if h["genome"]["family"] == f],
                      key=lambda h: (h["val"]["lo"], h["fitness"]), reverse=True)
        finalists += cand[:cfg.finalists_per_species]
    K = max(1, len(finalists))
    z_bonf = NormalDist().inv_cdf(1 - 0.05 / K)
    for h in finalists:
        g = h["genome"]
        v = ev.vault(G.series(g, df, ev.F), z_bonf)
        promoted = v["n"] >= cfg.min_vault_n and v["lo"] > ev.be
        report["finalists"].append({"id": gid(g), "family": g["family"], "genome": g, "dna": G.describe(g),
                                    "train": h["train"], "val": h["val"], "vault": v, "promoted": promoted,
                                    "fitness": h["fitness"]})
    report["z_bonferroni"] = z_bonf
    report["total_tested"] = len(ev.cache)
    report["hall_size"] = len(hall)

    # ---------------- referências honestas no mesmo período do cofre ----------------
    refs: dict[str, pd.Series] = {
        "cara ou coroa": random_baseline(df, rate=0.05, seed=cfg.seed),
        "sempre CALL": pd.Series(1, index=df.index, dtype=np.int8),
        "sempre PUT": pd.Series(-1, index=df.index, dtype=np.int8),
        **{f"fixo: {k}": fn(df) for k, fn in SETUPS.items()},
        **(extra_baselines or {}),
    }
    for name, s in refs.items():
        report["baselines"].append({"name": name, **ev.vault(s, cfg.z)})
    for fz in report["finalists"]:
        report["baselines"].append({"name": f"evoluída: {fz['id']}", **fz["vault"], "evolved": True,
                                    "promoted": fz["promoted"]})

    report["status"] = "done"
    report["finished"] = datetime.now(timezone.utc).isoformat()
    report["promoted"] = [fz["id"] for fz in report["finalists"] if fz["promoted"]]
    if save:
        _write(report)
        promote(report)
    return report


def _write(report: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lab_path(report["asset"], report["tf"]).write_text(json.dumps(report, default=str), encoding="utf-8")


def promote(report: dict) -> None:
    """Salva os promovidos e cria as células do Edge (descoberta = validação, confirmação = cofre)."""
    asset, tf = report["asset"], report["tf"]
    keep = [fz for fz in report["finalists"] if fz["promoted"]]
    evolved_path(asset, tf).write_text(json.dumps([{"id": fz["id"], "genome": fz["genome"],
                                                     "val": fz["val"], "vault": fz["vault"]} for fz in keep]),
                                       encoding="utf-8")
    data = prior.load()
    data["cells"] = {k: c for k, c in data["cells"].items()
                     if not (c["asset"] == asset and c.get("tf") == tf and str(c["setup"]).startswith("evo_"))}
    for fz in keep:
        data["cells"][prior.cell_key(asset, fz["id"], tf, prior.ALL, tf)] = {
            "asset": asset, "tf": tf, "setup": fz["id"], "expiry": tf, "bucket": prior.ALL,
            "disc_wins": fz["val"]["w"], "disc_n": fz["val"]["n"],
            "conf_wins": fz["vault"]["w"], "conf_n": fz["vault"]["n"],
        }
    data.setdefault("meta", {})[f"{asset}_M{tf}_evolved"] = report.get("finished")
    prior.PRIORS_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")


def load_evolved(asset: str, tf: int) -> list[dict]:
    p = evolved_path(asset, tf)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def load_report(asset: str, tf: int) -> dict | None:
    p = lab_path(asset, tf)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def signals_for(df: pd.DataFrame, evolved: list[dict]) -> dict[str, pd.Series]:
    """Sinais das estratégias promovidas (para o Spotter ao vivo e a catalogação)."""
    if not evolved:
        return {}
    F = G.Features(df)
    return {e["id"]: G.series(e["genome"], df, F) for e in evolved}
