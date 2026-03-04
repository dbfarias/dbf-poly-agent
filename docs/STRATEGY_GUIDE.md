# PolyBot Strategy Guide / Guia de Estrategias

> **PT-BR** | **EN** -- Documento bilingue / Bilingual document

---

## Visao Geral / Overview

```
+---------------------------------------------------------------+
|                    CICLO DE TRADING (60s)                       |
|                    TRADING CYCLE (60s)                          |
|                                                                 |
|  +----------+   +----------+   +----------+   +----------+     |
|  |  GAMMA   |-->| QUALITY  |-->|STRATEGIES|-->|  DEDUP   |     |
|  |  API     |   | FILTER   |   |  SCAN    |   |          |     |
|  | 500 mkts |   | ~484 pass|   | 6 active |   | per-strat|     |
|  +----------+   +----------+   +----------+   +----------+     |
|                                                      |          |
|  +----------+   +----------+   +----------+          v          |
|  | POSITION |<--| ORDER    |<--|   RISK   |<-- signals          |
|  | MONITOR  |   | EXECUTE  |   | MANAGER  |   (ranked)          |
|  | (exits)  |   | (CLOB)   |   | 9 checks |                    |
|  +----------+   +----------+   +-----+----+                    |
|                                      |                          |
|  +----------+   +----------+         v                          |
|  | LEARNER  |   |REBALANCER|   "Max positions"                  |
|  | (5min)   |   | (1/cycle)|   --> close worst                  |
|  +----------+   +----------+       loser                        |
+---------------------------------------------------------------+
```

**Meta / Goal:** Crescer $5 -> $500 via micro-operacoes de curto prazo (min 1%/dia)
**Goal:** Grow $5 -> $500 via short-term micro-operations (min 1%/day target)

---

## Fluxo Completo de Decisao / Complete Decision Flow

```
                     INICIO DO CICLO / CYCLE START
                              |
                              v
                  +------------------------+
                  |  1. Sync Portfolio     |
                  |  (Polymarket + DB)     |
                  +------------------------+
                              |
                              v
                  +------------------------+
                  |  2. Update Learner     |
                  |  (stats, urgency,      |
                  |   multipliers)         |
                  +------------------------+
                              |
                              v
                  +------------------------+
                  |  3. Check Exits        |
                  |  (take profit, stop    |
                  |   loss, time expiry)   |
                  +------------------------+
                              |
                              v
                  +------------------------+
                  |  4. Scan Markets       |
                  |  (Gamma API, 500 mkts) |
                  |  (quality filter)      |
                  |  (5 strategies scan)   |
                  +------------------------+
                              |
                              v
              +-------------------------------+
              | 5. Para cada sinal / For each |
              |    signal (ranked by score):  |
              |                               |
              |  a) Learner pausou strat? --> SKIP
              |  b) Ja tem pending order? --> SKIP
              |  c) Risk Manager (9 checks):  |
              |     APROVADO --> liquidity --> execute
              |     REJEITADO -->              |
              |       "Max positions"?        |
              |         YES --> REBALANCE      |
              |         NO  --> log + skip     |
              +-------------------------------+
                              |
                              v
                  +------------------------+
                  |  6. Monitor Pending    |
                  |  (verify fills, cancel |
                  |   expired orders)      |
                  +------------------------+
                              |
                              v
                  +------------------------+
                  |  7. Snapshot + Summary  |
                  +------------------------+
```

---

## Tiers de Capital / Capital Tiers

```
          $5                    $25                   $100
     <-------------------><-----------------><------------------->
     |     TIER 1           |     TIER 2        |     TIER 3        |
     | - 6 posicoes max     | - 6 posicoes max  | - 15 posicoes max |
     | - 40% max/posicao    | - 20% max/posicao | - 15% max/posicao |
     | - 85% max deployed   | - 80% max deployed| - 85% max deployed|
     | - Kelly 25%          | - Kelly 15%       | - Kelly 20%       |
     | - Min edge 1%        | - Min edge 2%     | - Min edge 2%     |
     | - Min win prob 55%   | - Min win prob 70%| - Min win prob 60% |
     | - Daily loss 10%     | - Daily loss 8%   | - Daily loss 6%   |
     | - Drawdown 25%       | - Drawdown 15%    | - Drawdown 12%    |
     | - Categ max 40%      | - Categ max 30%   | - Categ max 30%   |
     |                      |                    |                    |
     | Estrategias/Strats:  | + Swing Trading   | + Market Making   |
     | - Arbitrage          |                    |                    |
     | - Time Decay         |                    |                    |
     | - Value Betting      |                    |                    |
     +----------------------+--------------------+--------------------+
```

---

## As 6 Estrategias / The 6 Strategies

### 1. ARBITRAGE (Tier 1+)

**PT:** Explora inconsistencias de preco onde YES + NO < $1.00
**EN:** Exploits pricing inconsistencies where YES + NO < $1.00

```
    Exemplo / Example:

    YES = $0.48  +
                  |-- Soma = $0.99 (gap = $0.01)
    NO  = $0.51  +

    Compra ambos / Buy both:
    +--------------------------------------+
    | Custo total / Total cost: $0.99      |
    | Retorno garantido / Guaranteed: $1.00|
    | Lucro / Profit: $0.01 (1%)          |
    | Risco / Risk: ZERO (matematico)     |
    +--------------------------------------+
```

| Parametro / Parameter | Valor / Value |
|---|---|
| MIN_ARB_EDGE | 1% |
| Confianca / Confidence | 0.95 (fixa) |
| Saida / Exit | Nunca (espera resolucao) / Never (hold to resolution) |
| Frequencia / Frequency | Rara (1-2/semana) / Rare (1-2/week) |

> **ATENCAO / NOTE:** Polymarket cobra 2% de taxa no payout vencedor. Arbitragens < 3% edge podem perder dinheiro apos taxas.

---

### 2. TIME DECAY (Tier 1+) -- Principal / Primary Strategy

**PT:** Compra resultados de alta probabilidade perto da resolucao. Quanto menos tempo falta, mais certeza -> preco converge para $1.00.
**EN:** Buys high-probability outcomes near resolution. Less time = more certainty -> price converges to $1.00.

```
    Decaimento Temporal / Time Decay:

    Preco / Price ($)
    1.00 ----------------------o  <-- resolucao/resolution
    0.98 ----------------o     |
    0.96 ----------o            |
    0.93 ----o      ^           |
    0.90 --o  |     |           |
         |   |  ZONA DE COMPRA |
         |   |  BUY ZONE       |
         72h 48h  24h  12h  0h  <-- tempo restante / time remaining

    MAX_PRICE dinamico / Dynamic MAX_PRICE:
    <=12h --> max $0.99  (quase certo / almost certain)
    <=24h --> max $0.98
    <=48h --> max $0.97
    <=72h --> max $0.96  (precisa mais margem / needs more room)
```

**Exemplo Real / Real Example:**
```
    Mercado: "Rojas guilty in Texas illegal abortion case?"

    Dados / Data:
    |-- YES price: $0.9265
    |-- Tempo restante / Time left: ~48h
    |-- Volume 24h: alto / high
    +-- Spread: $0.02 (bom / good)

    Calculo / Calculation:
    |-- estimated_prob = 0.93 + 0.033 + 0.03 = 0.993 -> 0.99
    |-- edge = 0.99 - 0.93 = 6%  (> 1.5% min)
    |-- confidence = 0.75 + 0.06 + 0.06 = 0.87
    +-- score = time(0.90) * 0.6 + edge(0.75) * 0.4 = 0.84

    Resultado / Result:
    |-- APROVADO pelo risk manager / APPROVED by risk manager
    |-- Kelly = $3.67 -> bumped para 5 shares = $4.71
    +-- Ordem: BUY 5 YES @ $0.94 (best ask)
```

| Parametro / Parameter | Valor / Value | Descricao / Description |
|---|---|---|
| MIN_PRICE | $0.60 | So alta probabilidade / High prob only |
| MAX_PRICE | $0.96-0.99 | Dinamico por tempo / Dynamic by time |
| MIN_EDGE | 1.5% | Margem minima / Minimum edge |
| MIN_IMPLIED_PROB | 70% | Prob implicita minima / Min implied prob |
| CONFIDENCE_BASE | 0.75 | Base de confianca / Base confidence |
| MAX_HOURS | 72h | Horizonte maximo (dinamico com urgency) |
| EXIT_THRESHOLD | $0.70 | Sai se cair abaixo / Exit if drops below |
| EXIT_TAKE_PROFIT_PCT | 3% | Take profit apos 12h hold / Take profit after 12h hold |
| EXIT_MIN_HOLD_HOURS | 12h | Min hold antes do TP / Min hold before TP |

**Horizonte Dinamico / Dynamic Horizon:**
```
    urgency > 1.0 (atras da meta / behind target):
        MAX_HOURS expande ate 168h (7 dias)
        -> Mais oportunidades disponiveis

    urgency < 1.0 (na frente da meta / ahead of target):
        MAX_HOURS contrai para 48h
        -> Menos risco, so apostas rapidas
```

---

### 3. VALUE BETTING (Tier 1+)

**PT:** Detecta mercados mal precificados usando desequilibrio do order book.
**EN:** Detects mispriced markets using order book imbalance.

```
    Order Book Analysis:

    BIDS (compradores)        | ASKS (vendedores)
    |||||||||||| 800           | || 150
    |||||||||| 600             | | 100
    |||||||| 500               | | 80
    |||||| 400                 | . 60
    |||| 300                   | . 50
    -----------------------------------------------
    Total: 2600                | Total: 440

    Imbalance = (2600-440)/3040 = 71% mais bids!

    Interpretacao / Interpretation:
    +-------------------------------------------+
    | Compradores dominam / Buyers dominate     |
    | -> Mercado SUBprecificado / UNDERpriced   |
    | -> estimated_prob = price + 71% x 0.1     |
    | -> Edge = +7.1%                           |
    +-------------------------------------------+
```

| Parametro / Parameter | Valor / Value |
|---|---|
| MIN_EDGE | 2% (mais alto que time_decay) |
| IMBALANCE_THRESHOLD | 10% |
| MAX_HOURS | 168h (7 dias / days, dinamico com urgency) |
| EXIT_THRESHOLD | $0.40 |
| EXIT_STOP_LOSS | -10% do entry / from entry |
| EXIT_TAKE_PROFIT_PCT | 3% apos 6h hold / after 6h hold |
| EXIT_MIN_HOLD_HOURS | 6h |

---

### 4. PRICE DIVERGENCE (Tier 1+)

**PT:** Detecta divergencia entre preco do mercado e valor esperado usando sinais de sentimento e precos de cripto.
**EN:** Detects divergence between market price and expected value using sentiment signals and crypto prices.

| Parametro / Parameter | Valor / Value |
|---|---|
| MIN_EDGE | 2% |
| TAKE_PROFIT | 3% |
| STOP_LOSS | 5% |
| MAX_HOLD (crypto) | 24h |
| MAX_HOLD (other) | 4h |
| PRICE_HISTORY | 50 ticks |

---

### 5. SWING TRADING (Tier 2+)

**PT:** Compra mercados com momentum confirmado (3 ticks subindo). Vende rapido.
**EN:** Buys markets with confirmed upward momentum (3 rising ticks). Quick exit.

```
    Deteccao de Momentum / Momentum Detection:

    Preco / Price
    $0.540 ----------------o   <-- COMPRA / BUY (3 ticks up!)
    $0.535 ----------o     |
    $0.531 ----o           |
    $0.527 -o  |           |     momentum = 1.7%
    $0.523 o   |           |
    $0.520 |   |           |
           -------------------------
           t1  t2  t3  t4  t5    (cada tick = 30s)


    Cenarios de Saida / Exit Scenarios:

    $0.548 ---- [ok] Take Profit (+1.5%)
    $0.540 ---- Entrada / Entry
    $0.532 ---- [x] Stop Loss (-1.5%)

    Ou / Or:
    [clock] Max 4 horas -> sai independente / exits regardless
    [down] 3 ticks caindo -> sai por reversao / exits on reversal
```

| Parametro / Parameter | Valor / Value |
|---|---|
| TAKE_PROFIT | 1.5% |
| STOP_LOSS | 1.5% |
| MAX_HOLD | 4 horas / hours |
| MIN_MOMENTUM | 0.5% |
| MIN_TICKS | 3 consecutivos / consecutive |
| PRICE_RANGE | $0.15 - $0.85 |
| MIN_VOLUME_24H | $200 |

---

### 6. MARKET MAKING (Tier 3+, $100+)

**PT:** Coloca ordens de compra abaixo do mid-price e captura o spread.
**EN:** Places buy orders below mid-price and captures spread when filled.

```
    Order Book:

    Best Ask: $0.52  <-- vende aqui / sell here
              |
    Mid:      $0.485
              |
    Best Bid: $0.45
    Our Bid:  $0.46  <-- compra aqui / buy here

    Spread = $0.07
    Lucro esperado / Expected profit = $0.035/share
```

| Parametro / Parameter | Valor / Value |
|---|---|
| MIN_SPREAD | $0.03 |
| MAX_SPREAD | $0.15 |
| Confidence | 0.55 (baixa / low -- risco de inventario) |

---

## Comparacao de Estrategias / Strategy Comparison

| | Arbitrage | Time Decay | Value Betting | Price Diverg. | Swing | Market Making |
|---|---|---|---|---|---|---|
| **Tier** | 1+ | 1+ | 1+ | 1+ | 2+ | 3+ |
| **Edge** | 1%+ | 1.5%+ | 2%+ | 2%+ | 0.5%+ | spread |
| **Horizonte** | resolucao | <72h | <168h | 4-24h | <4h | 1-2h |
| **Win Rate** | ~95% | ~90% | ~65% | ~60% | ~60% | ~55% |
| **Risco** | Zero | Baixo | Medio | Medio | Medio | Alto |

---

## Risk Manager -- 9 Checks Cascateados / 9 Cascading Checks

Cada sinal precisa passar por TODOS os 9 checks em sequencia.
Each signal must pass ALL 9 checks in sequence.

```
    Signal recebido / received
         |
         v
    +-- 1. Trading pausado?         ---- x REJEITA
    +-- 2. Posicao duplicada?       ---- x REJEITA (mesmo market_id)
    +-- 3. Perda diaria excedida?   ---- x REJEITA (Tier1: 10%, Tier2: 8%, Tier3: 6%)
    +-- 4. Drawdown excedido?       ---- x REJEITA (Tier1: 25%, Tier2: 15%, Tier3: 12%)
    +-- 5. Max posicoes atingido?   ---- x REJEITA (Tier1: 6, Tier2: 6, Tier3: 15)
    |                                     |
    |                                     +--> REBALANCE? (se edge >= min_rebalance_edge)
    |                                                |
    +-- 6. Capital deployed > max?  ---- x REJEITA (Tier1: 85%, Tier2: 80%, Tier3: 85%)
    |                                     |
    |                                     +--> REBALANCE? (tambem trigger)
    |                                                |
    +-- 7. Categoria saturada?      ---- x REJEITA (Tier1: 40%, Tier2: 30%, Tier3: 30%)
    +-- 8. Edge muito baixo?        ---- x REJEITA (ajustado por tempo + learner)
    +-- 9. Win prob muito baixa?    ---- x REJEITA (Tier1: 55%, Tier2: 70%, Tier3: 60%)
         |
         v
    [ok] APROVADO --> calcula tamanho via Kelly
                  --> bump para 5 shares se necessario
                  --> check liquidez (spread + bid depth)
                  --> executa ordem no CLOB
```

### Check #5 Detalhado: Max Positions + Rebalance

```
    Max positions atingido?
         |
        YES
         |
         v
    "Max positions reached: 6 >= 6 (6 open + 0 pending)"
         |
         v
    Rebalance ja feito neste ciclo?
         |
        NO
         |
         v
    Signal edge >= 3%?
         |
        YES
         |
         v
    Buscar posicoes perdedoras:
    - unrealized_pnl <= 0 (nunca fecha vencedoras)
    - size >= 5 shares (pode vender no CLOB)
    - held >= 5 min (nao vende o que acabou de comprar)
         |
         v
    Ordenar por PnL% (mais negativo primeiro)
         |
         v
    Fecha a pior posicao
    --> Registra PnL
    --> Loga no Activity ("rebalance")
    --> Re-avalia o sinal com o slot liberado
```

### Check #8 Detalhado: Min Edge (Ajustado por Tempo)

```
    Edge minimo = config.min_edge_pct
                  x edge_multiplier (learner)
                  / urgency_multiplier (daily target)

    Depois ajusta por tempo ate resolucao:
    <=12h -> x 0.3  (~0.6% edge OK)
    <=24h -> x 0.4  (~0.8% edge OK)
    <=48h -> x 0.5  (~1.0% edge OK)
    <=96h -> x 0.7  (~1.4% edge OK)
    >96h  -> x 1.0  (edge completo)

    Exemplo / Example (Tier 2, time_decay, 12h resolucao):
    base = 2%
    edge_mult = 0.8 (strategy winning)
    urgency = 1.3 (behind target)
    effective_mult = 0.8 / 1.3 = 0.62
    adjusted_min = 2% x 0.62 = 1.24%
    time_adjusted = 1.24% x 0.3 = 0.37%  <-- aceita edges bem baixos!
```

---

## Position Sizing -- Kelly Criterion

```
    f* = (p - c) / (1 - c)

    onde / where:
      p = probabilidade real estimada / estimated real probability
      c = preco de mercado / market price (cost)

    Exemplo / Example:
    |-- p = 0.95 (estimamos 95% chance)
    |-- c = 0.90 (preco no mercado / market price)
    |-- f* = (0.95 - 0.90) / (1 - 0.90) = 0.50 (50% Kelly!)
    |-- Fractional Kelly = 0.50 x 0.15 (Tier2) = 7.5% do bankroll
    |-- Bankroll = $31 -> position = $2.33
    +-- Bump: 5 shares x $0.90 = $4.50 (minimo Polymarket)
        -> Final: $4.50

    [!] Polymarket exige minimo de 5 shares por ordem
        Posicoes < 5 shares NAO PODEM ser vendidas!
        Devem esperar resolucao do mercado.

    [!] Size capped a 95% do capital disponivel (5% buffer para fees)
```

---

## Sistema de Aprendizado / Learning System

```
    +-----------------------------------------------------+
    |              PERFORMANCE LEARNER                     |
    |              (recomputa a cada 5min)                 |
    |                                                      |
    |  Inputs:                                             |
    |  |-- Ultimos 500 trades (30 dias)                   |
    |  |-- PnL diario vs. meta de 1%                      |
    |  +-- Win rate por estrategia x categoria             |
    |                                                      |
    |  Outputs:                                            |
    |                                                      |
    |  1. Edge Multipliers (por estrategia x categoria)    |
    |     Win rate > 60% -> 0.8 (relaxa edge minimo)      |
    |     Win rate 40-60% -> 1.0 (normal)                 |
    |     Win rate < 40% -> 1.5 (exige mais edge)         |
    |     Sem dados -> 1.2 (cauteloso)                    |
    |                                                      |
    |  2. Category Confidence (por categoria)              |
    |     Win rate > 70% -> 1.2 (boost)                   |
    |     Win rate 50-70% -> 1.0 (normal)                 |
    |     Win rate < 50% -> 0.7 (penaliza)                |
    |                                                      |
    |  3. Urgency Multiplier (progresso diario)            |
    |     > 100% da meta -> 0.7 (conservador)             |
    |     50-100% -> 1.0 (normal)                         |
    |     0-50%   -> 1.3 (agressivo)                      |
    |     Negativo -> 1.5+ (muito agressivo)              |
    |                                                      |
    |  4. Auto-Pause (por estrategia)                      |
    |     Ultimos 5 trades < 30% win rate                 |
    |     E PnL total < -$0.05 -> PAUSA 12h               |
    |     Manual unpause via API (6h grace period)        |
    |                                                      |
    |  5. Calibration (confiabilidade das probabilidades)  |
    |     Se sinais de 95% ganham so 60% das vezes        |
    |     -> Ajusta edge requirement para cima              |
    +-----------------------------------------------------+
```

### Como urgency afeta os parametros / How urgency affects parameters

```
    Meta diaria / Daily target: 1% do equity

    Acompanhamento / Tracking:
    realized_pnl_today = PnL realizado hoje
    day_start_equity = equity as 00:00 UTC (fixo)
    target_usd = day_start_equity x 1%
    progress = realized_pnl / target_usd

    Efeitos / Effects:

    urgency = 1.3 (atras da meta / behind target):
    |-- edge_multiplier dividido por 1.3 -> aceita edges menores
    |-- MAX_HOURS expandido ate 168h -> mais mercados disponiveis
    +-- Resultado: bot fica mais agressivo buscando oportunidades

    urgency = 0.7 (na frente da meta / ahead of target):
    |-- edge_multiplier dividido por 0.7 -> exige mais edge
    |-- MAX_HOURS contraido para 48h -> so mercados rapidos
    +-- Resultado: bot fica conservador, protege ganhos
```

---

## Rebalanceamento Ativo / Active Rebalancing

**Problema / Problem:** Bot com 6/6 posicoes ou >85% capital deployed encontra 9+ sinais novos por ciclo, mas bloqueia todos com "Max positions" ou "Max deployed capital." Fica esperando exits naturais (take profit, stop loss, resolucao) que podem levar dias.

**Solucao / Solution:** Quando na capacidade maxima e um sinal de alta qualidade aparece, automaticamente fecha a pior posicao perdedora para abrir espaco.

### Fluxo Completo / Complete Flow

```
    Sinal rejeitado por "Max positions" ou "Max deployed capital"
         |
         v
    [check 1] Edge do sinal >= min_rebalance_edge (default 1.5%)?
         |
        NO --> Skip (sinal de baixa qualidade)
        YES
         |
         v
    [check 2] Ja fez rebalance neste ciclo?
         |
        YES --> Skip (max 1 por ciclo, evita churning)
        NO
         |
         v
    [check 3] Encontrar posicoes perdedoras:
         |
         +-- Para cada posicao aberta (da pior para melhor):
         |   - unrealized_pnl > 0? --> SKIP (nunca fecha vencedoras)
         |   - size < 5 shares (live)? --> SKIP (nao pode vender no CLOB)
         |   - held < min_hold_seconds (default 120s)? --> SKIP
         |   - Tenta vender: falhou? --> tenta proxima candidata
         |   - OK? --> fecha posicao
         |
         v
    Nenhuma candidata vendavel? --> Skip
         |
        FECHOU uma posicao
         |
         v
    Registra PnL --> Atualiza daily_pnl --> Loga activity
         |
         v
    _rebalanced_this_cycle = True
         |
         v
    Re-avalia o sinal original:
    risk_manager.evaluate_signal() com posicoes atualizadas
         |
        APROVADO --> check liquidez --> executa trade
        REJEITADO --> loga "Post-rebalance: {motivo}" --> skip
```

### Condicoes (TODAS devem ser verdadeiras) / Conditions (ALL must be true)

| # | Condicao / Condition | Motivo / Reason |
|---|---|---|
| 1 | Sinal rejeitado por "Max positions" ou "Max deployed" | So rebalanceia quando slots/capital estao cheios |
| 2 | Edge do novo sinal >= min_rebalance_edge (default 1.5%, tunable) | So vale trocar por sinais de qualidade |
| 3 | Pior posicao com PnL <= 0 | Nunca fecha posicoes que estao ganhando |
| 4 | Pior posicao com >= 5 shares | Polymarket CLOB exige minimo de 5 shares para vender |
| 5 | Posicao mantida >= min_hold_seconds (default 120s, tunable) | Evita vender algo que acabou de comprar |
| 6 | Max 1 rebalance por ciclo | Evita churning (vender e comprar excessivamente) |
| 7 | Se venda falha, tenta proxima candidata | Resiliente a ghost positions / balance issues |

### Exemplo de Rebalance / Rebalance Example

```
    Situacao / Situation:
    - Tier 2, 6/6 posicoes abertas
    - Novo sinal: time_decay, edge = 5%, market "BTC > $100K by March"
    - Posicoes atuais:

    | Market           | Strategy  | PnL%   | Shares | Held  |
    |------------------|-----------|--------|--------|-------|
    | ETH merge date   | value_bet | -12%   | 10     | 2h    | <-- PIOR
    | Trump approval   | time_decay| -3%    | 8      | 1h    |
    | Fed rate cut     | time_decay| +2%    | 6      | 30min |
    | S&P 5000         | swing     | -1%    | 5      | 45min |
    | Gold $2500       | time_decay| +5%    | 7      | 3h    |
    | BTC halving      | value_bet | 0%     | 4      | 10min | <-- < 5 shares

    Decisao / Decision:
    1. Edge 5% >= 3% --> OK
    2. Nao rebalanceou ainda --> OK
    3. Candidatas (PnL <= 0, >= 5 shares, >= 5 min):
       - ETH merge date: -12% PnL, 10 shares, 2h  (ok)
       - Trump approval: -3% PnL, 8 shares, 1h     (ok)
       - S&P 5000: -1% PnL, 5 shares, 45min        (ok)
       - BTC halving: 0%, mas 4 shares < 5 -> SKIP
       - Fed rate cut: +2% -> SKIP (winning)
       - Gold $2500: +5% -> SKIP (winning)
    4. Pior = ETH merge date (-12%)
    5. Fecha ETH merge date
    6. Re-avalia "BTC > $100K" -> APROVADO (5/6 posicoes agora)
    7. Executa ordem
```

---

## Filtro de Qualidade de Mercado / Market Quality Filter

Antes de qualquer estrategia avaliar um mercado, ele precisa passar:
Before any strategy evaluates a market, it must pass:

```
    Mercado da Gamma API
         |
         v
    Binary? (exatamente 2 outcomes)
         |
        NO --> SKIP
        YES
         |
         v
    Active? (nao archived, nao fechado)
         |
        NO --> SKIP
        YES
         |
         v
    neg_risk enabled? (Polymarket CLOB compativel)
         |
        NO --> SKIP
        YES
         |
         v
    Volume 24h adequado?
         |
        NO --> SKIP
        YES
         |
         v
    PASSA para avalicacao das estrategias
```

### Filtro de Liquidez Pre-Trade / Pre-Trade Liquidity Filter

Depois do risk manager aprovar, antes de executar:
After risk manager approval, before execution:

```
    Signal aprovado
         |
         v
    Order book tem spread <= 5 cents?
         |
        NO --> "Spread too wide" --> SKIP
        YES
         |
         v
    Best bid >= 80% do fair price?
    (tem como sair se precisar)
         |
        NO --> "No exit liquidity" --> SKIP
        YES
         |
         v
    Slippage <= 3 cents?
    (ask nao muito acima do sinal)
         |
        NO --> "Excessive slippage" --> SKIP
        YES
         |
         v
    Edge ainda positivo no preco real?
         |
        NO --> "Edge evaporated at ask" --> SKIP
        YES
         |
         v
    [ok] EXECUTA no CLOB
```

---

## Activity Log / Log de Atividades

Cada decisao do bot eh registrada na tabela `bot_activity` e visivel na pagina Activity do dashboard.
Every bot decision is logged to the `bot_activity` table and visible on the Activity dashboard page.

### Tipos de Evento / Event Types

| Tipo / Type | Nivel / Level | Descricao / Description |
|---|---|---|
| `signal_found` | info | Sinal encontrado por uma estrategia |
| `signal_rejected` | warning | Sinal rejeitado (com motivo detalhado) |
| `order_placed` | success/info | Ordem colocada (filled ou pending) |
| `order_filled` | success | Ordem pendente confirmada preenchida |
| `order_expired` | warning | Ordem cancelada por timeout (5 min) |
| `exit_triggered` | info | Estrategia sinalizou saida |
| `position_closed` | success/warning | Posicao fechada (com PnL) |
| `rebalance` | info | Posicao fraca fechada para abrir espaco |
| `price_adjust` | info | Preco ajustado ao order book (slippage) |
| `cycle_summary` | info | Resumo do ciclo (a cada 5 ciclos) |
| `bot_event` | varies | Eventos de lifecycle (start, stop, error) |

### Retencao / Retention

- Max 5000 rows mantidas / kept
- Pruning automatico a cada 50 ciclos / Auto-prune every 50 cycles
- Dados mais antigos removidos primeiro / Oldest data removed first

---

## Regras de Saida / Exit Rules

### Saidas por Estrategia / Per-Strategy Exits

| Estrategia | Take Profit | Stop Loss | Time Exit | Reversal |
|---|---|---|---|---|
| Time Decay | +3% after 12h hold | < $0.70 | -- | -- |
| Value Betting | +3% after 6h hold | -10% from entry, < $0.40 | -- | -- |
| Price Divergence | +3% | -5% | 24h (crypto) / 4h (other) | -- |
| Swing Trading | +1.5% | -1.5% | 4h | 3 ticks down |
| Arbitrage | Resolucao ($1.00) | -- | -- | -- |
| Market Making | Spread capture | Spread collapse | -- | -- |

### Saidas Universais / Universal Exits

| Regra / Rule | Valor / Value | Descricao / Description |
|---|---|---|
| TAKE_PROFIT_PRICE | $0.95 | Lock in near-certainty (after 12h hold) |
| STOP_LOSS_PCT | -40% | Exit on severe loss |
| NEAR_WORTHLESS | < $0.10 | Always exit |
| MAX_POSITION_AGE | 72h (3 days) | Free capital tied in stale positions |
| UNMATCHED_STRATEGY | < $0.70 | Exit for unmatched strategies |

### Saida por Rebalance / Rebalance Exit

Posicoes perdedoras podem ser fechadas a qualquer momento se:
- Um sinal melhor (edge >= min_rebalance_edge) aparece
- A posicao tem PnL negativo
- Tem >= 5 shares e foi mantida >= min_hold_seconds

### Polymarket Constraints

```
    [!] REGRA CRITICA / CRITICAL RULE:
    Posicoes < 5 shares NAO PODEM ser vendidas no CLOB!
    Devem esperar a resolucao do mercado.
    O bot tenta "bumpar" para 5 shares no momento da compra.
    Se o bankroll nao permite 5 shares, o trade eh rejeitado.
```

---

## Resumo de Parametros Configuravel / Configurable Parameters Summary

### Via Dashboard (Settings page, persistido / persisted):
- `scan_interval_seconds` (5-3600)
- `daily_target_pct` (0-100%)
- Tier configs: max_positions, max_per_position_pct, max_deployed_pct, etc.
- Strategy params: MAX_HOURS, quality filter thresholds, take-profit %
- Learner params: pause_lookback, pause_win_rate, pause_min_loss, pause_cooldown_hours
- Rebalance params: min_rebalance_edge, min_hold_seconds
- Quality gate params: max_spread, min_volume, stop_loss, take_profit_price

### Via .env (requer restart):
- `TRADING_MODE` (paper/live)
- `INITIAL_BANKROLL`
- Polymarket API credentials
- Dashboard credentials
- Telegram bot token

---

## Ciclo de Vida de uma Posicao / Position Lifecycle

```
    SINAL ENCONTRADO
         |
         v
    Risk Manager (9 checks)
         |
    REJEITADO --> Activity log "signal_rejected"
    APROVADO
         |
         v
    Liquidity check (spread, bid, slippage)
         |
    FALHOU --> Activity log "signal_rejected"
    PASSOU
         |
         v
    order_manager.execute_signal()
         |
    FILLED --> record_trade_open() --> POSICAO ABERTA
    PENDING --> monitora a cada ciclo
         |              |
         |         FILLED (confirmado)? --> record_trade_open()
         |         TIMEOUT (5 min)? --> cancela ordem
         |
         v
    POSICAO ABERTA
         |
         +-- A cada ciclo: sync precos, verifica exits
         |
         v
    EXIT TRIGGERED?
         |
        YES (estrategia sinalizou saida)
         |   OU rebalance (posicao fraca substituida)
         |   OU force-close (via dashboard)
         |   OU mercado resolveu
         |
         v
    order_manager.close_position()
         |
         v
    portfolio.record_trade_close()
    --> calcula PnL = (close_price - avg_price) * shares
    --> atualiza _realized_pnl_today
    --> atualiza _cash
         |
         v
    Activity log "position_closed" (com PnL)
```
