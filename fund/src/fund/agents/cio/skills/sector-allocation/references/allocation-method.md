# From regime and research to target weights

## 1. Classify the regime

Place the economy on two axes from the macro dive, and state the evidence for each:

| | Inflation falling | Inflation rising |
|---|---|---|
| **Growth accelerating** | Goldilocks / early cycle | Reflation / late-mid cycle |
| **Growth slowing** | Disinflationary slowdown | Stagflation |

Add the policy direction (easing, on hold, tightening) and the curve shape. Recession probability, the unemployment trend, and credit (HYG/IEF) decide whether "slowing" means a soft landing or a contraction.

## 2. Typical leadership by regime (a prior, not a rule)

| Regime | Sectors that usually lead | Usually lag | Bonds | International |
|---|---|---|---|---|
| Early cycle / goldilocks | Technology, Consumer Cyclical, Financial Services, Industrials | Utilities, Consumer Defensive | Little need | Helped by a falling dollar |
| Reflation | Energy, Basic Materials, Industrials, Financial Services | Utilities, Real Estate, long-duration Technology | Short duration, TIPS | EM and commodity exporters |
| Disinflationary slowdown | Healthcare, Consumer Defensive, Utilities, quality Technology | Energy, Basic Materials, Financial Services | Intermediate and long Treasuries hedge best | Usually lags the US |
| Stagflation | Energy, Consumer Defensive, Healthcare, Utilities | Consumer Cyclical, Technology, Real Estate | Short Treasuries, TIPS; long duration hurts | Usually lags |

Confirm or reject the prior with the tables: `beta_10y` shows which sectors gain or lose when yields rise, `pe_pctile` shows how much good news is already priced, `excess_3y` / `excess_5y` show the trend against SPY, and `corr_spy` shows whether a bond bucket actually hedges equities right now.

## 3. Build the targets from conviction

The output is the portfolio you would hold to beat SPY, not SPY adjusted. Every target is a percent of the
total portfolio, and all targets plus the 2% cash sum to 100.

1. **Rank the opportunities.** From the regime, the research, and the tables, rank every sector, bond bucket,
   and region by expected return over the next 12-24 months against its risk. Write the ranking and its
   reasons before choosing any number.
2. **Size by conviction and risk.** Give the most capital to the highest-conviction, best risk-adjusted
   ideas, and scale each target down by its volatility and drawdown: 15% in Energy carries far more risk
   than 15% in Consumer Defensive. A sector you expect to lag can be small or zero.
3. **Respect the limits.** No sector above 30%. Bonds at most 20% in total, international at most 15%,
   cash exactly 2%.
4. **Measure against SPY last.** Only after the targets are set, compare each with its `spy_weight`. The
   differences are your bets against the benchmark: name the largest ones in the portfolio thesis and check
   that each is a bet you meant to make. Do not pull a target toward SPY just to reduce the difference.

### Bonds and international

SPY is 100% US equity, so every point here is a bet that it beats US equity on a risk-adjusted basis.

- **Fixed income (0-20%)**: hold it only when it earns its place. Either the yield is high in its own history (`yield_pctile`) and the regime favors duration, or the bucket hedges equities (`corr_spy` below zero) in a regime where an equity drawdown is likely. Pick buckets by duration and credit from the regime; avoid credit buckets that move with SPY when the point is defense.
- **International (0-15%)**: hold it when the region's trend vs SPY, valuation, and the dollar all point the same way. A rising dollar is a headwind.
- **Cash**: fixed at 2% as a buffer; it is not an allocation decision.

## 4. Split each sector and name its themes

- **Core**: the long-term holdings in that sector, held through the cycle. Most of a sector's target.
- **Tactical**: room for shorter-term trades within that sector. Larger where the sector is cyclical, volatile, or catalyst-heavy (policy, earnings, commodity moves); smaller in stable sectors.
- `core_weight + tactical_weight` equals the sector's `weight`; all three are percent of the total portfolio.
- **Themes**: the sub-industries or themes to favor inside the sector (e.g. "semiconductors", "defense"), from the industry snapshots and research. They are filled later with stocks, sector ETFs, or theme ETFs, and count against this sector's target. Broad basket ETFs (equal-weight, size, factor) are spread across sectors by their own holdings, not budgeted here.

## 5. Write each case

- `thesis`: the macro and data reasons for the weight, with the numbers that support it.
- `risks`: what would make the call wrong.
- `review_trigger`: a measurable condition that forces a re-allocation (e.g. "10y yield above 5.5%", "copper/gold ratio below its 200-day average").
