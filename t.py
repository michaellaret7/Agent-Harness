"""Screen Databento options in Python and research a defined-risk trade pitch.

Setup: uv sync --all-packages
Run: uv run python t.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from typing import Literal

from pydantic import BaseModel

from agent_harness.agent import Agent
from agent_harness.base_tools.execute_code import execute_code_tool
from agent_harness.gates import GateContext, GateVerdict
from agent_harness.sinks import LogSink

load_dotenv()

MODEL = 'meta/muse-spark-1.3'

SYSTEM = '''
<context>
**PROVISIONAL idea only — quotes are stale. Do not trade without re-pricing live. No orders placed.**

Fresh live options for Oct 1 didn't exist in `OPRA.PILLAR` yet at pull time (dataset end `2026-10-01T13:30:00Z`, `cbbo-1m` Oct-1 query returned 0 rows). Everything below is priced off the last Sep-30 close auction window. Underlying has since moved.       

### Screen — what I actually did

- `databento.Historical(DB_KEY)`, `dataset=OPRA.PILLAR`
- `schema=definition`, `stype_in=parent`, e.g. `NVDA.OPT`, window `2026-09-29` to `2026-10-01`: enumerated expirations.
- 30–60d from Oct-1 = Oct-31 to Nov-30. Result:
  - SPY: Nov-6, Nov-20, Nov-30
  - NVDA / AAPL / TSLA / MSFT / META / AMD / AMZN / PLTR / COIN / AVGO: Nov-6 (36 DTE), Nov-20 (50 DTE)
- FMP spots (`/api/v3/quote` + `/historical-price-full`): NVDA Sep-30 close 228.38, Oct-1 close 230.91, high 231.50 / low 228.46. SPY 763.64, MSFT 519.51, META 731.28 etc.
- Liquidity check `schema=ohlcv-1d` Sep-30 aggregate volume (summed across venues) + `schema=cbbo-1m` size/spread:
  - `NVDA 261120C00230000`: 71,918 contracts — monster
  - `NVDA 261120C00240000`: 2,446
  - vs. MSFT Nov-20 520C: 994 / 540C: 281; META 730C: 503 / 760C: 106; AVGO 350C: 528 / 370C: 1,028

NVDA Nov-20 is the only one with real two-leg liquidity and a clean catalyst window. That's the shortlist winner.

### The pitch: NVDA Nov-20 230 / 240 call vertical

Directional-bullish, defined-risk, earnings-aware.

Exact legs, 1x1, expiry Nov-20-2026 (50 DTE from Oct-1, Friday expiry):

1. BUY `NVDA  261120C00230000` — NVDA Nov-20 $230 Call
2. SELL `NVDA  261120C00240000` — NVDA Nov-20 $240 Call

Width: $10.00

Last observed consolidated quotes, `cbbo-1m`, Sep-30 19:59 UTC (15:59 ET, i.e. closing print — **stale**):

- 230C: bid 12.15 x 45, ask 12.45 x 90, ts_event `2026-09-30T19:59:56Z`
- 240C: bid 7.50 x 98, ask 8.05 x 41, ts_event `2026-09-30T19:59:46Z`

Conservative pricing — pay the ask on the long, dump at the bid on the short, no mid-price fantasy:

- Debit = 12.45 - 7.50 = **4.95 = $495 per spread**
- Mid for reference: 12.30 - 7.775 = 4.525, so you're assuming ~$42.50 of slippage vs mid.
- Max loss = **$495**
- Max profit = 10.00 - 4.95 = **5.05 = $505** (+102.0% of risk)
- Breakeven at expiry = 230 + 4.95 = **234.95**
- Reward/risk = 5.05 / 4.95 = 1.02

Python-computed expiry P/L per spread (debit 4.95):

- NVDA ≤230: -$495 (-100%)
- 234.95: ~$0
- 237: +$205 (+41.4% of risk)
- ≥240: +$505 (+102.0%, capped)

Needs +1.7% from Oct-1 close 230.91 to breakeven, +4.0% to max profit. That's the deal — you need a real move, not drift.       

### Why NVDA, why this expiry

- Trend + flow: Sep-30 tape shows 71k on the 230C. Market is already voting there.
- Buyback floor: board authorized +$150B, ~$235B capacity through FY2028 — per Sep-29 outlook piece. Shrinks float, supports dips.
- Setup into mid-Nov: next earnings estimated Wed Nov-18 (MarketBeat, based on history) — **2 days before this expiry**. You capture the earnings run-up and the print itself. Bull case in that same outlook: October range $245–260 on AI demand / Rubin ramp / hyperscale capex; base $220–245; bear $195–220.
- DTE logic: Nov-6 (36 DTE, 230C vol 3,449 / 240C 1,131, last bid/ask 8.80/9.10 and 4.70/5.00) is cheaper but gives you no room if earnings slips a week and theta eats you faster. Nov-20 survives a Nov-18 earnings date; Nov-6 dies right before it.

### Risks — the part people skip

1. **Stale marks.** Underlying moved 228.38 → 230.91 after these quotes. Live debit will differ. Re-quote or walk away.
2. **Earnings binary.** Nov-18 inside expiry = IV crush + gap risk. A miss and both legs go to zero fast; max loss is real.     
3. **Paying up for momentum.** $4.95 on a $10 wide is ~49.5% of width. No drift win — you need >234.95.
4. **Short-leg liquidity.** 240C did 2,446 vs 71k on the long; spread 0.55 wide, size 98x41 at last print. Exits in stress will be ugly.
5. **Narrative risk:** AI-capex digestion, yields, semis sentiment, competition, valuation/profit-taking after +21.6% YTD run. Bear scenario $195–220 would zero this.
6. **American exercise / dividend / early assignment** on the short call — low but nonzero into earnings.

### Exit plan

- Entry: limit **4.95 debit or better**, day-only. If live ask/bid implies >5.20, pass — R/R collapses.
- Profit: scale 1/2 at +50% of debit (~7.40 credit value), runner to +80–100% or into Nov-16/17 pre-earnings. Don't hold a $9+ winner into the Nov-18 print hoping for max.
- Stop: exit on spread value ≤2.50 (~-50%) or NVDA breaks and holds under 225 / 21-day trend fails. No averaging down.
- Time stop: if NVDA <232 by Nov-6 (~2 weeks left, earnings imminent, theta accelerating), cut to 1/4 or flat — don't donate to crush.
- Earnings rule: no new entry after Nov-16; existing position either trimmed to house-money or closed pre-print unless you explicitly want the binary.

Bottom line: best liquid 30–60d defined-risk bull in this screen is NVDA Nov-20 230/240 calls at ~4.95, $495 risk / $505 max, BE 234.95. Provisional until live quotes confirm — if the debit isn't there, it's a **no_trade**.
Done. It's on — and you got price improvement vs. the stale screen.

**Account:** Alpaca paper (`PA3XKFDQYWQL***`), $1M equity, options level 3, market open. No prior positions/orders.

**Live re-price before firing** (Oct-1 10:11 ET, because Sep-30 marks were garbage):
- NVDA stock 230.27 / 230.48
- 230C: 12.93 x 818 / 13.54 x 63
- 240C: 8.49 x 242 / 8.68 x 451
- Live mid spread = ~4.65. Your screen's 4.95 conservative debit was stale-high.

**Order placed:**
- MLEG limit 10x NVDA Nov-20 230/240 call vertical
- BUY `NVDA261120C00230000` / SELL `NVDA261120C00240000`, both `buy_to_open` / `sell_to_open`, DAY
- Limit 4.95 debit
- Order ID `2afd1ea1-ce0c-4681-a6c4-5595de7e3c75`

**Fill:** FILLED at 4.65 net
- Long 10 @ 13.15, short 10 @ 8.50
- That's mid, $0.30 better than your 4.95 limit

**Sizing — I picked 10 spreads:**
- Risk = $465 x 10 = **$4,650 (~0.47% of equity)**
- Max profit = (10 - 4.65) x 1000 = **$5,350**
- Breakeven = **234.65** (vs 234.95 on old marks)
- R/R = 1.15

Why 10 and not YOLO: earnings Nov-18 sits 2 days before expiry. This is still a binary with IV crush. Half a percent risk is plenty to feel it without blowing up if Jensen sneezes wrong.

**Your exits, updated to 4.65 cost:**
- Profit: scale half around ~7.00 spread value (+50%), runner into Nov-16/17. Don't hold a big winner into the print.
- Stop: spread ≤2.30 (~-50%) or NVDA breaks and holds under 225. No averaging down.
- Time stop: NVDA <232 by Nov-6, cut to 1/4 or flat.

It's live in the account now. If you wanted live-money instead of paper, those keys point to paper — say so and I'll re-route.
<context>
'''

TASK = (
    '''just tell me what the time horizon of this trade is and what the risk/reward is. and why did you choose nvidia and are you sure you screened them properly?
    do another biug screen with many more stocks preferably in the sp500 and then tell me if you found any other liquid ones.
    '''
)


agent = Agent(
    provider='openrouter',
    model=MODEL,
    system=SYSTEM,
    tools=[
        execute_code_tool(
            env={
                'FMP_API_KEY': os.environ['FMP_API_KEY'],
                'PARALLEL_API_KEY': os.environ['PARALLEL_API_KEY'],
                'DB_KEY': os.environ['DATABENTO_API_KEY'],
                'ALPACA_API_KEY': 'PKPVX5OIHEDL2IRWJLP2S57C3F',
                'ALPACA_SECRET_KEY': '2tZAcz7bNCQX2ZSGDkhjsUTAFWu59nhunQzrSkRx2ydj',
            },
            packages=['databento', 'pandas', 'httpx', 'alpaca-py'],
            j_screen=True,
        )
    ]
)


if __name__ == '__main__':
    report = agent.run(task=TASK, sink=LogSink('screen'))

    print(report.model_dump_json(indent=2) if isinstance(report, BaseModel) else report)
