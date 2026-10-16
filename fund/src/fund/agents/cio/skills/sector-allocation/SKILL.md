---
name: sector-allocation
description: Extensive web research, then a deep macro dive plus sector, bond, and international research on FMP data, ending in conviction-based target weights (sectors split core and tactical, bonds, international) built to beat SPY; use for any allocation or portfolio-construction mandate.
---

## Steps

1. **Load the scripts.** In one ExecuteCode call:
   `fmp = runpy.run_path('skills/curated/sector-allocation/scripts/fmp.py')`,
   `sectors = runpy.run_path('skills/curated/sector-allocation/scripts/sectors.py')`, and
   `sleeves = runpy.run_path('skills/curated/sector-allocation/scripts/sleeves.py')`.
   Fetches are cached to `data/*.parquet` for the day, so re-running a step is free.

2. **Web research first.** Read `references/web-research.md` and work through all 14 topics before any
   FMP data: wide parallel searches, full reads of the best primary sources, and notes appended to
   `research/web_notes.md` after each topic. Finish with `research/web_summary.md`, including the claims
   the data must confirm or reject.

3. **Macro dive.** Read `references/macro-indicators.md`, then fetch at least 10 years of:
   growth (`realGDP`, `industrialProductionTotalIndex`, `retailSales`), labor (`unemploymentRate`,
   `totalNonfarmPayroll`, `initialClaims`), inflation (`CPI`, `inflationRate`), policy (`federalFunds`),
   the Treasury curve, the cycle (`smoothedUSRecessionProbabilities`, `consumerSentiment`), and the market
   series (credit, VIX, dollar, commodities, breadth). For each, print the latest value, its y/y change,
   its 3-month trend, and its 10-year percentile. Never print raw series. Then test each claim from
   `research/web_summary.md` against the data and say which hold.

4. **Catalysts.** Pull the next 30 days of high-impact US events from the economic calendar and set them
   against the debates from step 2.

5. **Regime.** Classify growth and inflation direction, policy, and curve shape, following
   `references/allocation-method.md` section 1. Write the evidence for each axis, data and web.

6. **Sector research.** `table = sectors['sector_table'](fmp)` gives every sector's SPY weight, trailing
   and excess returns, volatility, drawdown, beta to SPY, rate sensitivity, and P/E percentile. Print it
   rounded. Compare it with the regime prior in `references/allocation-method.md` section 2 and with the
   sector outlooks from step 2. For themes, rank `fmp['snapshot']('industry-performance-snapshot', date=...,
   exchange='NYSE')` and `industry-pe-snapshot` by industry, and check them against the theme research.

7. **Sleeve research.** `sleeves['bond_table'](fmp)` gives each bond bucket's yield, its 10-year
   percentile, and its correlation to SPY; `sleeves['international_table'](fmp)` gives each region's
   returns vs SPY and the dollar trend. Follow `references/allocation-method.md` section 3 (bonds and international).

8. **Allocate.** Follow `references/allocation-method.md` sections 3-5: rank every opportunity, set
   conviction-based targets as percent of the total portfolio, size them by risk, compare with SPY only
   at the end, split each sector into core and tactical, name its favored themes, and write every
   thesis, risk, and review trigger. Save the
   draft to `research/allocation_draft.md`, then run the red team in `references/web-research.md` and revise.

9. **Check, then submit.** In ExecuteCode, assert that all targets plus cash sum to 100% of the
   portfolio, no sector is above 30%, bonds total at most 20%, international at most 15%, and each
   sector's core plus tactical equals its weight. List every web source you relied on in `research_sources`. Then
   call SubmitResult.

## Files
- `scripts/fmp.py`: `fmp_get`, `indicator_history`, `treasury_history`, `price_history`, `sector_pe_history`, `snapshot`; run it, do not rewrite it.
- `scripts/sectors.py`: `SECTOR_ETFS` and `sector_table(fmp)`; the ETFs only measure each sector, they are not holdings.
- `scripts/sleeves.py`: `BOND_BUCKETS`, `REGIONS`, `bond_table(fmp)`, `international_table(fmp)`; proxy ETFs only measure.
- `references/web-research.md`: the 14-topic web research agenda, source standards, and note format; read at step 2.
- `references/macro-indicators.md`: valid indicator names, frequencies, and market-series symbols; read at step 3.
- `references/allocation-method.md`: regime grid, sector priors, conviction-based target sizing, bonds and international, core/tactical rules; read at steps 5-8.
