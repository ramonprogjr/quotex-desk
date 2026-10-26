import numpy as np
import pytest

from backend.data import resample
from backend.lab import evolve as E
from backend.lab import genome as G

from .conftest import make_candles


@pytest.mark.parametrize("family", G.FAMILIES)
def test_genome_signal_has_no_lookahead(family):
    rng = np.random.default_rng(5)
    df = make_candles(1600, seed=2)
    for _ in range(3):
        g = G.random_genome(rng, family)
        g["sess"] = [True] * G.N_SESSIONS
        g["atr_min"], g["atr_span"], g["adx_min"], g["adx_span"] = 0.0, 1.0, 0, 100
        full = G.signal(g, G.Features(df))
        for t in (700, 1200, 1599):
            part = G.signal(g, G.Features(df.iloc[: t + 1]))
            assert part[-1] == full[t], f"{family} mudou no candle {t} ao ver o futuro"


def test_mutation_and_crossover_respect_bounds():
    rng = np.random.default_rng(1)
    for f in G.FAMILIES:
        a, b = G.random_genome(rng, f), G.random_genome(rng, f)
        for _ in range(200):
            c = E.mutate(E.crossover(a, b, rng), rng, p=0.9, scale=1.0)
            for k, (lo, hi, typ) in G.specs(f).items():
                assert lo <= c[k] <= hi and isinstance(c[k], typ)
            assert c["family"] == f and any(c["sess"])


def test_same_seed_same_result():
    df = resample(make_candles(30_000, seed=3), 5)
    cfg = E.EvoConfig(pop=24, immigrants=4, gens=3, seed=7)
    a = E.run(df, "TEST", 5, 0.85, cfg, save=False)
    b = E.run(df, "TEST", 5, 0.85, cfg, save=False)
    assert [g["best"] for g in a["generations"]] == [g["best"] for g in b["generations"]]
    assert [f["id"] for f in a["finalists"]] == [f["id"] for f in b["finalists"]]


def test_species_quotas_kept():
    df = resample(make_candles(30_000, seed=4), 5)
    r = E.run(df, "TEST", 5, 0.85, E.EvoConfig(pop=24, immigrants=4, gens=4, seed=1), save=False)
    for gen in r["generations"]:
        for f in G.FAMILIES:
            assert gen["species"][f]["count"] >= 5   # (24 − 4) / 4


@pytest.mark.slow
def test_random_walk_promotes_nothing():
    """Teste de falso positivo do protocolo: sem vantagem nos dados, nada pode passar no cofre."""
    df = make_candles(120_000, seed=21)
    r = E.run(df, "TEST", 1, 0.85, E.EvoConfig(pop=48, immigrants=8, gens=8, seed=3), save=False)
    assert r["total_tested"] > 150
    assert r["promoted"] == []
    refs = {b["name"]: b for b in r["baselines"]}
    assert refs["cara ou coroa"]["rate"] is not None and 0.45 < refs["cara ou coroa"]["rate"] < 0.55
