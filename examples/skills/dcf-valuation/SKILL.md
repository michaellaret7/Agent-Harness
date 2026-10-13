---
name: dcf-valuation
description: Value a public company with a five-year unlevered (FCFF) DCF on fresh FMP data; use for intrinsic value or price-target questions.
---

## 1. Fetch (fresh, into variables)
Fetch from `https://financialmodelingprep.com/stable/` with `apikey=os.environ['FMP_API_KEY']` straight into pandas DataFrames; never save responses to files. Probe one record per endpoint first to confirm field names.
- `cash-flow-statement`, `income-statement`, `balance-sheet-statement` (annual, 6 years)
- `profile` (beta, market cap), `quote` (price, timestamp), `shares-float` (outstanding shares)
- `treasury-rates` (latest 10-year yield = risk-free rate)

## 2. Free cash flow to the firm (FCFF)
- Effective tax rate t = incomeTaxExpense / incomeBeforeTax (latest year; clamp to 0-35%).
- FCFF = freeCashFlow + interestExpense x (1 - t). Discount FCFF, never levered FCF, because net debt is subtracted later.
- If capex in the latest year is far above its 5-year average (investment cycle), say so and state whether the base year is normalised.

## 3. Discount rate (WACC)
- Cost of equity = risk-free + beta x 5.0% equity risk premium (state the premium).
- Pre-tax cost of debt = interestExpense / totalDebt; after-tax = that x (1 - t).
- Weights from market cap (equity) and totalDebt (debt). Show every input.

## 4. Projection and value
- Growth: start from the 5-year FCFF CAGR; state and justify any adjustment. Terminal growth at most 3% and below WACC.
- PV of five years of FCFF + PV of terminal value FCFF_5 x (1 + g) / (WACC - g).
- Net debt = totalDebt - cashAndShortTermInvestments (includes short-term investments).
- Equity value = enterprise value - net debt; per share = equity value / outstanding shares.

## 5. Checks before reporting
- Sensitivity table: WACC +/-1% against terminal growth +/-0.5%.
- Terminal value share of enterprise value; flag it above 75%.
- Reverse DCF: solve the five-year growth rate that makes the per-share value equal the current price, and compare it with the assumption.
- If the value differs from the price by more than 40%, explain the main driver before concluding.
- Report the quote timestamp and statement dates.
