"""Configuração central. Valores padrão podem ser sobrescritos em config.json na raiz."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data_cache"
DB_PATH = ROOT / "journal.db"
CONFIG_PATH = ROOT / "config.json"


@dataclass
class Config:
    # Corretora
    payout: float = 0.85            # retorno líquido no acerto (85% → ganha 0,85 por 1 apostado)
    min_stake: float = 1.0          # entrada mínima da Quotex
    timeframes: list[int] = field(default_factory=lambda: [1, 5])  # M1 e M5 rodando juntos
    # expirações (min) testadas em cada timeframe; precisam ser múltiplas dele
    expiries: dict[str, list[int]] = field(default_factory=lambda: {"1": [1], "5": [5]})

    # Estatística (agentes Prior/Edge)
    z: float = 1.645                # z unilateral de 95% para o limite inferior de Wilson
    min_n: int = 150                # mínimo de operações decididas por célula para aprovar
    discovery_frac: float = 0.7     # fração do histórico usada para descoberta; o resto confirma
    hour_bucket: int = 4            # agrupamento de horas UTC (4 → 6 sessões por dia)

    # Kelly
    kelly_fraction: float = 0.25    # Kelly fracionado
    max_stake_frac: float = 0.02    # teto: 2% da banca por entrada

    # Risco (agente Closer)
    daily_stop_frac: float = 0.06   # para o dia ao perder 6% da banca do início do dia
    daily_target_frac: float = 0.0  # 0 = sem meta de ganho
    max_trades_day: int = 15
    max_consecutive_losses: int = 2
    cooldown_minutes: int = 30
    timezone: str = "America/Sao_Paulo"

    # Ao vivo
    poll_seconds: int = 3
    live_window: int = 1000         # candles mantidos em memória por ativo (M1 ≈ 16 h, M5 ≈ 3,5 dias)
    signal_ttl_seconds: int = 30    # padrão: quanto tempo após o fechamento do candle o sinal ainda vale
    # por timeframe, a partir do teste de atraso (cli delay): no M1 o acerto cai rápido com o atraso
    signal_ttl_by_tf: dict[str, int] = field(default_factory=lambda: {"1": 10, "5": 30})

    # robô (só conta DEMO): timeframes que ele opera. M5 por padrão — o teste de atraso
    # mostrou que no M1 alguns segundos de atraso já derrubam o acerto.
    bot_timeframes: list[int] = field(default_factory=lambda: [5])
    # Modo teste OTC (só DEMO): entra também nos sinais VETADOS dos pares OTC gravados, para testar
    # a mecânica e medir cada setup ao vivo. Não é vantagem comprovada. As entradas vão para
    # data_cache/bot_test_otc.jsonl, fora do diário (não mexem na banca, no risco nem na calibração).
    bot_test_otc: bool = False
    bot_test_stake: float = 100.0
    bot_test_timeframes: list[int] = field(default_factory=lambda: [1, 5])
    bot_test_max_day: int = 30          # entradas por dia
    bot_test_day_stop: float = 1000.0   # perda máxima por dia (em $ da demo)
    bot_test_min_payout: float = 0.80   # abaixo disso o empate fica alto demais; pula

    def signal_ttl(self, tf: int) -> int:
        return int(self.signal_ttl_by_tf.get(str(tf), self.signal_ttl_seconds))

    def expiries_for(self, tf: int) -> list[int]:
        return [int(e) for e in self.expiries.get(str(tf), [tf])]

    @staticmethod
    def expiry_candles(expiry: int, tf: int) -> int:
        if expiry % tf:
            raise ValueError(f"expiração {expiry} min não é múltipla do timeframe M{tf}")
        return expiry // tf

    def breakeven(self) -> float:
        return 1.0 / (1.0 + self.payout)

    def to_dict(self) -> dict:
        return asdict(self)


def load_config() -> Config:
    cfg = Config()
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        for k, v in data.items():
            if k in known:
                setattr(cfg, k, v)
    return cfg
