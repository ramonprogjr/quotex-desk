# Quotex Desk

Painel de apoio a operações **manuais** na Quotex. Ele analisa o gráfico, mede se existe vantagem estatística, sugere entrada e valor, e registra o que você fez. **Não faz login nem executa ordens.**

## Como os agentes decidem

| Agente | O que faz |
|---|---|
| **Spotter** | Detecta setups no fechamento do candle de 1 min: RSI+Bollinger, engolfo, rompimento, pullback na EMA |
| **Prior** | Taxa de acerto histórica daquele setup × ativo × expiração × sessão do dia |
| **Edge** | Aprova só se o limite inferior de Wilson ficar acima do empate `1/(1+payout)` na descoberta (70% do histórico) **e** na confirmação (30%) |
| **Kelly** | ¼ de Kelly sobre o acerto conservador, com teto de 2% da banca. Sem vantagem, o valor é 0 |
| **Closer** | Stop diário de 6%, máximo de 15 entradas, pausa de 30 min após 2 perdas seguidas, uma operação por vez e **anti-martingale** |
| **Forward** | Resolve todo sinal, aprovado ou vetado, pelo próprio feed: teste ao vivo contínuo |

Por padrão, tudo é vetado. Um sinal só fica verde quando os dados provam vantagem.

## Instalação (uma vez)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..
```

## Uso

```powershell
# 1. histórico (cripto via Binance, sem chave). M1 e M5 saem do mesmo arquivo de 1 min.
.venv\Scripts\python -m backend.cli download BTCUSD --days 365
# 2. o que funcionaria? (inclui o sinal aleatório para comparação)
.venv\Scripts\python -m backend.cli backtest BTCUSD
# 3. o método de seleção funciona fora da amostra?
.venv\Scripts\python -m backend.cli walkforward BTCUSD
# 4. treina a Confluência (IA) em walk-forward: leva alguns minutos
.venv\Scripts\python -m backend.cli train BTCUSD
# 5. gera as taxas históricas que o painel usa (já inclui a Confluência)
.venv\Scripts\python -m backend.cli priors BTCUSD
# 6. painel
.\iniciar.bat            # abre http://127.0.0.1:8000
```

Toda semana, refaça os passos 1, 4 e 5. Com o painel aberto, chame `POST /api/reload` (ou reinicie o painel) para carregar os modelos novos.

## Especialistas

| Especialista | Olha para |
|---|---|
| Mapeador S/R | topos e fundos fractais, extremos de 50 candles, números redondos, toques na zona |
| Tendência | EMAs 9/21/50, ADX, DI, MACD |
| Exaustão | RSI, Estocástico, Williams %R, CCI, %B |
| Price action | corpo, pavios, pin bar, sequência de cores, retornos |
| Contexto | volatilidade (percentil do ATR), horário, quadrante, volume |
| **Confluência (IA)** | combina tudo. Treina só no passado e opera no futuro. O limiar de confiança vem da validação |

Estratégias testadas: RSI+Bollinger, engolfo, rompimento, pullback, rejeição S/R, pin bar na zona, MHI (sem gale) e Confluência. Todas passam pelo Edge. Em 1 ano de dados, só **RSI+Bollinger** e **Confluência** passaram do empate.

## Pré-sinal e gráfico

- Nos **últimos 25 s (M1) ou 60 s (M5)** do candle, o painel avalia o candle em formação. Se algo aprovado estiver se formando, dá um bipe grave e mostra **PRÉ-SINAL** com uma faixa tracejada no gráfico. Deixe a ordem pronta na Quotex.
- No fechamento, o sinal é **CONFIRMADO** (bipe agudo e faixa sólida: entre) ou **CANCELADO** (não entre). A taxa de confirmação aparece no forward test.
- Botões do gráfico: M1/M5, EMAs, Bollinger, Zonas S/R, Marcações, Ajustar, Ao vivo e a contagem regressiva do candle.
- Painel **ENTRADA**: botões CALL/PUT grandes, que registram no diário, e o seletor de valor (sugerido, mínimo ou 2× o mínimo).

## M1 e M5

Os dois timeframes rodam ao mesmo tempo. O botão **M1 / M5** no gráfico troca o que você vê. O alerta de sinal aprovado toca nos dois.
- **M1:** expiração de 1 minuto.
- **M5:** expiração de 5 minutos, fechando junto com o candle de 5.

Para mudar, edite `timeframes` e `expiries` em `config.json`.

## Fluxo de uma operação

1. O painel apita e mostra o **SINAL ATIVO**: direção, expiração no **modo horário** (ex.: 14:03), valor e uma contagem regressiva de 20 s.
2. Você confere o payout na Quotex e, se quiser entrar, abre a operação lá.
3. Clique em **ENTREI NA QUOTEX** para registrar no diário.
4. Quando expirar, marque **GANHOU**, **PERDEU** ou **EMPATE**. Enquanto houver operação aberta, novos sinais ficam bloqueados.

Registre também as entradas feitas por conta própria, no formulário do diário. A calibração compara "seguiu o sinal" com "por conta própria".

## Critério para continuar (fase de validação)

Opere com o valor mínimo por 2 a 4 semanas. Continue só se, com **100 ou mais operações**, o limite inferior do seu acerto real passar do empate. O painel mostra esse progresso em **Banca & Validação**.

## Forex (OANDA demo)

1. Crie uma conta **demo** grátis na OANDA. No painel da conta, vá em **Manage API Access → Generate** para gerar o token.
2. Copie `.env.example` para `.env` e cole o token em `OANDA_TOKEN=`.
3. Rode para cada par (EURUSD, GBPUSD, USDJPY):
   ```powershell
   .venv\Scripts\python -m backend.cli download EURUSD --days 365
   .venv\Scripts\python -m backend.cli train EURUSD
   .venv\Scripts\python -m backend.cli priors EURUSD
   ```
4. Reinicie o painel. Os pares saem de "sem fonte" e passam a ter pré-sinal e preço ao vivo.

A alternativa `TWELVEDATA_API_KEY` (plano grátis, 800 requisições por dia) serve para o histórico, mas ao vivo aguenta só ~1 par e não tem pré-sinal.

## Robô (somente conta DEMO)

O robô entra sozinho nos sinais **aprovados**, para testar a estratégia em volume na demo:
1. No painel **ROBÔ**, clique em **▶ Abrir navegador do robô**. Abre um Chrome separado.
2. Nesse Chrome, **faça login você mesmo** e selecione a **CONTA DEMO**. O código nunca vê a sua senha. A sessão fica salva em `data_cache/quotex_profile`; não compartilhe essa pasta.
3. Deixe o campo **Tempo** no modo cronômetro (`00:05:00`), e não no modo horário.
4. Clique em **Diagnóstico**. Todos os itens precisam aparecer com ✔. Se algum falhar, mande o print.
5. Faça um **teste mecânico** (CALL ou PUT de $1 num par fora do OTC) e confira se a ordem apareceu na Quotex.
6. Clique em **⚡ Ligar robô (DEMO)**.

Travas:
- só opera **conta DEMO**: confere antes de cada ordem e desliga sozinho se aparecer conta REAL;
- só **sinais aprovados** (Edge, Closer e disjuntor), no **M5** (`bot_timeframes` no config);
- sem OTC, uma operação por vez, valor do Kelly e stop diário;
- se o payout real for menor que o usado no sinal, pula a entrada e atualiza o payout no painel;
- registra cada ordem no diário e marca o resultado pela variação do saldo da Quotex.

O robô usa um caminho não oficial (automação do navegador). A Quotex pode mudar o site e quebrar os cliques, ou restringir a conta. Use só na demo.

## Laboratório Evolutivo

Inspirado no SETS Machine, mas com um protocolo mais rígido. Um algoritmo genético cria, testa e elimina estratégias binárias sozinho:
- 4 espécies com cota: reversão, tendência, rompimento e price action;
- 96 estratégias por geração, com 8 imigrantes aleatórios;
- genes de gatilho mais filtros (sessão, volatilidade, ADX).

O protocolo usa três cortes de dados:
- **Treino (60%):** a evolução só enxerga esse período.
- **Validação (20%):** o portão de cada geração.
- **Cofre (20%):** nunca é olhado durante a evolução. No fim, só os finalistas passam por ele, **uma vez**, com correção de Bonferroni.

Só quem passa no cofre é promovido, e ainda assim passa pelo Edge, pelo Closer e pelo disjuntor.

```powershell
.venv\Scripts\python -m backend.cli evolve ETHUSD --tf 5 --gens 40
```

Também dá para rodar pelo botão **▶ Rodar evolução** no painel LABORATÓRIO. O painel mostra o funil e a kill rate, o fitness por geração, o DNA de cada finalista e as referências honestas no cofre: cara ou coroa, **sempre CALL**, **sempre PUT**, os setups fixos e a Confluência.

## Confiabilidade: o que separa o backtest da vida real

- **Disjuntor:** se uma estratégia aprovada piorar ao vivo, ela é **suspensa automaticamente**. A regra é: em 40 ou mais sinais, até o melhor cenário estatístico fica abaixo do empate. Sinais que o risco bloqueou também contam. A suspensão aparece em CALIBRAÇÃO, com o botão "reativar", que zera a contagem.
- **Teste de atraso:** `python -m backend.cli delay ETHUSD` baixa dados de 1 s (cripto) ou 5 s (forex) e mostra o acerto entrando 0, 5, 10, 20 e 30 s depois. Use para saber quanto tempo você tem de verdade.
- **Feed × Quotex:** ao registrar uma entrada, preencha (opcional) o **preço na Quotex**. Ao marcar o resultado, preencha o **preço final**. A CALIBRAÇÃO mostra em quantos % o resultado no feed bateu com a Quotex. Abaixo de 90%, o histórico não representa bem a Quotex naquele ativo.
- **Payout por timeframe:** em AJUSTES há uma grade ativo × M1/M5. Se passar de 2 h sem atualizar, aparece o aviso **"payout desatualizado"**.

## Limitações importantes

- **Ativos OTC** (fim de semana e "-OTC") têm preço gerado pela Quotex. O sistema recusa analisá-los.
- O feed externo (Binance/OANDA) pode divergir um pouco do gráfico da Quotex. Se você digitar o preço de entrada da Quotex no diário, o painel mede essa diferença.
- O payout muda ao longo do dia. Atualize em **Ajustes**, porque o empate depende dele.
- Os parâmetros de risco ficam em `config.json` (veja `backend/config.py`).

## Testes

```powershell
.venv\Scripts\python -m pytest -q
```
