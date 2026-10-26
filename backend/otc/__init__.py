"""Gravação e análise de pares OTC da Quotex.

O preço OTC é gerado pela própria corretora e só existe lá dentro: não há fonte externa.
Por isso gravamos os ticks que a página da Quotex já recebe (só leitura, sem enviar nada)
e montamos candles de 1 min no mesmo formato do resto do projeto.

    data_cache/otc/ticks/<PAR>/<AAAA-MM-DD>.csv   ticks brutos (ts,price), um arquivo por dia UTC
    data_cache/otc/instruments.jsonl              payout e status de cada par a cada conexão
    data_cache/otc/<PAR>_1m.parquet               candles montados (python -m backend.otc.build)
    data_cache/otc/status.json                    último tick e par gravado (para o painel)
"""
from ..config import DATA_DIR

OTC_DIR = DATA_DIR / "otc"
TICKS_DIR = OTC_DIR / "ticks"
