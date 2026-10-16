# Macro data on FMP (verified names and shapes)

All series come from `scripts/fmp.py`. `indicator_history` and `treasury_history` page through FMP's ~90-day cap for you.

## Economic indicators: `indicator_history(name, years)` -> date, value

| Theme | Name | Frequency | Read it as |
|---|---|---|---|
| Growth | `realGDP` | Quarterly | Level; compute q/q annualized and y/y |
| Growth | `GDP` | Quarterly | Nominal level |
| Growth | `industrialProductionTotalIndex` | Monthly | y/y change: goods-cycle momentum |
| Growth | `retailSales` | Monthly | y/y change: consumer demand |
| Growth | `durableGoods` | Monthly | y/y change: capex intent |
| Growth | `newPrivatelyOwnedHousingUnitsStartedTotalUnits` | Monthly | Housing starts (thousands); rate-sensitive cycle lead |
| Labor | `unemploymentRate` | Monthly | Level and 3-month change (a Sahm-style rise is a recession signal) |
| Labor | `totalNonfarmPayroll` | Monthly | Level (thousands); monthly diff = payroll growth |
| Labor | `initialClaims` | Weekly | 4-week average trend |
| Inflation | `CPI` | Monthly | Index; compute y/y and 3-month annualized |
| Inflation | `inflationRate` | Daily | Market-implied inflation, percent |
| Policy | `federalFunds` | Monthly | Policy rate, percent |
| Credit | `30YearFixedRateMortgageAverage` | Weekly | Mortgage rate, percent |
| Credit | `commercialBankInterestRateOnCreditCardPlansAllAccounts` | Quarterly | Consumer credit cost, percent |
| Sentiment | `consumerSentiment` | Monthly | Michigan sentiment level |
| Cycle | `smoothedUSRecessionProbabilities` | Monthly | Percent; above ~20 is a warning |
| Trade | `tradeBalanceGoodsAndServices` | Monthly | Millions USD |

`housingStarts` is not a valid name; an invalid name raises `ValueError: ... Invalid name`.

## Rates: `treasury_history(years)` -> date, month1 … year30

- Curve slope: `year10 - year2` and `year10 - month3`. An inversion that is un-inverting often precedes recession.
- Real-rate proxy: `year10` minus the latest `inflationRate`.

## Market series: `price_history(symbol, start)` -> date, close

| Signal | Symbols | Read it as |
|---|---|---|
| Credit risk appetite | `HYG` / `IEF` ratio | Rising = spreads tightening, risk-on |
| Volatility | `^VIX` | Level vs its 1-year range |
| Dollar | `DX-Y.NYB` | Trend; a strong dollar hurts multinationals and materials |
| Commodities | `CLUSD` (oil), `HGUSD` (copper), `GCUSD` (gold) | Copper/gold ratio rising = growth expectations up |
| Breadth | `RSP` / `SPY` ratio | Falling = narrow, mega-cap-led market |
| Size and global | `IWM`, `EFA`, `EEM` vs `SPY` | Domestic small-cap and global risk appetite |

## Snapshots: `snapshot(path, **params)`

- `snapshot('economic-calendar', **{'from': today, 'to': today + 30 days})`: filter `country == 'US'` and `impact == 'High'` for the next month's catalysts.
- `snapshot('market-risk-premium')`: filter `country == 'United States'` for the equity risk premium.
- `snapshot('news/general-latest', limit=50)`: headlines. Use WebSearch for Fed communication and the policy outlook.
