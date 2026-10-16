# Web research agenda

Run this before touching FMP data. The web tells you what the market is debating and why; the data step then
tests those claims. Cover every topic below, and exhaust each one: there is no time or call budget, only a
standard of depth.

## How to research one topic

1. **Search wide.** One `WebSearch` per angle, `mode='advanced'`, `max_results=8`, `after_date` about 60 days
   back (12 months for structural topics). Give it a precise `objective` and 2-3 distinct `search_queries`.
   WebSearch and WebExtract run in parallel, so issue several searches in one turn.
2. **Read every useful source in full.** `WebExtract` every URL that adds information, with an `objective`.
   Follow leads: when a source cites a report, speech, or dataset, find and read that original.
   Prefer primary sources (Federal Reserve, BLS, BEA, Treasury, IMF, OECD, company filings), then major
   research and press (central bank speeches, bank and asset-manager outlooks, Reuters, Bloomberg, FT, WSJ).
   Treat blogs and SEO content as leads to verify, not evidence.
3. **Write notes to a file.** After each topic, append to `research/web_notes.md` with ExecuteCode: the
   topic, every finding as a bullet with its numbers and dates, the consensus view, the strongest contrarian view, and
   every source as `- [title](url), publisher, date`. Files survive a kernel reset; your context may not.
4. **Note disagreements.** When sources conflict, record both sides and what data would settle it. The FMP
   step should settle it.
5. **Keep going until saturated.** Run at least three search rounds per topic, each from a new angle (bull
   case, bear case, primary data, a different region or publisher, second-order effects). A topic is done
   only when two rounds in a row add no new fact, number, or argument to your notes.

## Topics (all required)

| # | Topic | Questions to answer |
|---|---|---|
| 1 | Fed policy path | Latest FOMC statement, minutes, dot plot, and speeches; market-implied cuts or hikes |
| 2 | Inflation | Latest CPI/PCE detail (core, services, shelter, goods); wage growth; expectations |
| 3 | Growth and labor | Latest GDP, payrolls, claims, ISM; recession calls and their reasoning |
| 4 | Rates and the curve | Term premium, Treasury supply, curve shape; strategist 10-year yield targets |
| 5 | Credit and liquidity | Credit spreads, defaults, bank lending, private credit stress |
| 6 | Fiscal and politics | Budget, tariffs and trade policy, regulation, elections, and their sector effects |
| 7 | Earnings | Latest season's beats and misses by sector, forward EPS revisions, margin outlook |
| 8 | Valuation and positioning | S&P 500 forward P/E, concentration in the largest stocks, fund flows, sentiment surveys |
| 9 | Sector outlooks | Major banks' and asset managers' sector over- and underweights, and why |
| 10 | Structural themes | AI capex and power demand, energy transition, defense, reshoring, healthcare innovation |
| 11 | Commodities and the dollar | Oil supply and OPEC, copper and industrial metals, gold, dollar outlook |
| 12 | Global | Europe, Japan, China, EM growth and policy; relative valuations vs the US |
| 13 | Geopolitical risks | Conflicts, sanctions, shipping and supply-chain risks, and which sectors they hit |
| 14 | Bonds as an asset | Strategist views on duration, TIPS, IG and high-yield credit for the next 12 months |

## Red team (after the allocation draft)

For each of the five largest active bets, search specifically for the strongest case against it, read it in
full, and append the counter-argument and your answer to `research/web_notes.md`. Change the bet when the
counter-argument wins.

## Finishing

- Read `research/web_notes.md` back and write `research/web_summary.md`: one paragraph per topic, the five
  biggest debates, and the claims the FMP data must confirm or reject.
- Collect every source you relied on; each one goes into `research_sources` in the final answer.
