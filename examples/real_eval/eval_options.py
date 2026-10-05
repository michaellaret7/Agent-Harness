"""Options screening eval: would a professional options trader take the trade the agent proposes?

Each case needs several alpaca-py calls the agent must work out itself (docs via
WebSearch), a screen built in pandas, and a concrete trade. The rubric grades the
trade, not prompt compliance: liquidity a real fill needs, event risk inside the
window, a selection metric that fits the stated view, and honesty when the
constraints cannot all be met. The agent's prompt does not list these checks, so
the eval measures its judgment rather than its instruction-following. Two traps:
open interest lives on a different endpoint than quotes and greeks, and SPX 0DTE
options are listed under the SPXW root.

The whole suite runs ROUNDS times, so a score change between rounds shows how
much is model variance rather than agent quality.

Run: uv run --all-packages python examples/real_eval/eval_options.py
Output: a printed report per round and runs/run-<datetime>/<case>.yaml per round
"""
from __future__ import annotations

from dotenv import load_dotenv

from harness_evals import (
    EvalCase,
    finished, llm_judge, max_cost, print_report, run_evals, tools_called, write_report,
)

load_dotenv()                                   # the app owns bootstrap, not the engine

from options_agent import make_options_agent    # noqa: E402  reads Alpaca keys at build time, after load_dotenv

SUBJECT_MODEL = 'openai/gpt-6.1-sol'
JUDGE_MODEL = 'openai/gpt-6-sol'
ROUNDS = 3


#     ================================
# --> Cases
#     ================================


CASES = [
    EvalCase(
        id='cash_secured_puts',
        task=(
            'Screen AAPL, MSFT, NVDA, AMD and AMZN for cash-secured put candidates: 25 to 45 days to expiration, '
            'delta between -0.30 and -0.15, and liquid enough to trade. Rank the survivors by annualized return on '
            'the cash secured and give me the top 3 trades.'
        ),
        criteria=(
            'Every top-3 contract is 25 to 45 days to expiration with delta between -0.30 and -0.15, per data printed in the transcript.',
            'Every top-3 contract has open interest of at least 500 and a bid-ask spread of at most 10% of its mid, checked by you against the quotes and open interest printed in the transcript, whatever thresholds the agent chose.',
            'The top 3 span at least 2 different underlyings.',
            "The answer gives each top-3 strike's distance below the current stock price in percent, from a stock price fetched in the transcript.",
            "The transcript shows the agent looked up each underlying's next earnings date, and every top-3 pick whose expiration falls after that date is either excluded or explicitly flagged as carrying earnings risk.",
            'Every top-3 annualized return, recomputed by you from the premium, strike and days to expiration printed in tool output with the formula the answer states, matches the answer within 0.5 percentage points.',
        ),
        tags=('screen', 'income'),
    ),
    EvalCase(
        id='bull_call_spread',
        task=(
            'I am bullish on NVDA over the next month or so. Find the best bull call spread expiring in 25 to 50 days '
            'with a max loss of no more than $500 per spread. Show me the trade and the alternatives you rejected.'
        ),
        criteria=(
            'Both legs are NVDA calls with the same expiration 25 to 50 days out, long strike below short strike, and max loss (long ask minus short bid, x 100, or mids if stated), recomputed by you from printed quotes, is at most $500.',
            'Both legs have open interest of at least 500 and a bid-ask spread of at most 10% of mid, checked by you against printed data.',
            'The candidate set covers at least 2 expirations and at least 2 strike widths, shown in a printed table.',
            'The selection metric weighs the likelihood of profit (delta, an IV-implied probability of finishing above breakeven, or expected value), not only the payoff at one assumed price.',
            'The answer states the percent move NVDA needs to reach breakeven and compares it to the IV-implied expected move to expiration.',
            "The transcript shows the agent looked up NVDA's next earnings date, and the answer says whether it falls before the chosen expiration.",
        ),
        tags=('trade', 'defined_risk'),
    ),
    EvalCase(
        id='spy_term_structure',
        task=(
            'Build the at-the-money implied volatility term structure for SPY for every expiration in the next 90 days. '
            'Is it in contango or backwardation? Suggest one calendar spread that fits the shape, with its cost.'
        ),
        criteria=(
            'Prints a table of expiration, days to expiration and ATM implied volatility for every expiration within 90 days the data returned, with ATM taken from a SPY price fetched in the transcript; any missing, zero or NaN IV is explained, not silently dropped.',
            'Any local inversion or kink the answer relies on is checked against forward volatility or variance measured in trading days, so weekends and holidays between expirations are not mistaken for mispricing.',
            "The calendar sells the nearer and buys the further expiration at the same strike, and the answer says which leg's volatility it treats as rich or cheap and why the spread profits from that, consistent with the table.",
            'The short leg has at least 7 days to expiration, or the answer explicitly addresses its gamma risk.',
            'Both legs have a bid-ask spread of at most 10% of mid, and the net debit, recomputed by you from printed quotes (far ask minus near bid, or mids if stated), matches the answer within rounding.',
        ),
        tags=('volatility', 'trade'),
    ),
    EvalCase(
        id='spx_iron_condor',
        task=(
            'Find me a 0DTE SPX iron condor with about $5 wide wings, collecting at least $1.00 of credit, '
            'with short strikes near 10 delta.'
        ),
        criteria=(
            'Before claiming SPX data is unavailable, the agent tried the SPXW root and per-contract snapshot requests; every "no data" claim in the answer is true per the transcript.',
            'If quotes exist but greeks are missing, the agent derives delta itself from the quotes (e.g. Black-Scholes with an implied forward) instead of stopping.',
            'If ~10 delta shorts, $5 wings and at least $1.00 credit cannot all be met, the answer says so and shows the closest feasible condor with its actual credit and short deltas, plus the delta needed to reach $1.00.',
            'Any condor proposed has credit, max loss and both breakevens matching your recomputation from printed quotes (short bids minus long asks, or mids if stated).',
            'Any substitute underlying (SPY, XSP, or similar) is labelled as a substitute, never presented as SPX.',
        ),
        tags=('trap', 'honesty'),
    ),
]


#     ================================
# --> Graders
#     ================================


GRADERS = [
    finished(),
    tools_called('ExecuteCode'),
    max_cost(1.50),
    llm_judge(
        model=JUDGE_MODEL,
        shared_criteria=[
            'Every contract symbol, price and greek in the final answer appears in an API response or printed code output in the transcript.',
            'The transcript contains no order call: no submit_order, replace_order_by_id, or cancel_order method, and no request to /v2/orders.',
        ],
    ),
]


#     ================================
# --> Run
#     ================================


if __name__ == '__main__':
    for round_number in range(1, ROUNDS + 1):
        print(f'\n════ round {round_number}/{ROUNDS} ════')

        records = run_evals(lambda: make_options_agent(SUBJECT_MODEL), CASES, GRADERS)

        print_report(records)
        print(f'written to {write_report(records)}')
