"""Stock screener eval: did the screen answer the question, and was it built with the right data?

Each case asks for a screen the agent builds in pandas from CSVs its Alpaca data
tools save. The rubric grades whether the screen is sound and useful (exact
thresholds applied in code, survivor counts shown, a result a trader could act
on) and how it was built (right tool per job, no wasted pulls). Three of the six
data tools are deferred, so the judge's tools.json is what lets it say a better
tool went unused. Traps: snapshot volume is IEX-only (a few percent of the
market), Alpaca has no market-cap field, and a filter set may leave no survivors.

The whole suite runs ROUNDS times, so a score change between rounds shows how
much is model variance rather than agent quality.

Run: uv run --all-packages python examples/real_eval/eval_screener.py
Output: a printed report per round and runs/run-<datetime>/<case>.yaml per round
"""
from __future__ import annotations

from dotenv import load_dotenv

from harness_evals import (
    EvalCase,
    finished, llm_judge, max_cost, print_report, run_evals, tools_called, write_report,
)

load_dotenv()                                       # the app owns bootstrap, not the engine

from screener_agent import make_screener_agent     # noqa: E402  the data tools read Alpaca keys per call, after load_dotenv

SUBJECT_MODEL = 'openai/gpt-6.1-sol'
JUDGE_MODEL = 'openai/gpt-6-sol'
ROUNDS = 1


#     ================================
# --> Cases
#     ================================


CASES = [
    EvalCase(
        id='active_uptrends',
        task=(
            "Take today's 50 most active US stocks by share volume and keep the ones in a clean uptrend: last close above "
            'the 50-day simple moving average, and the 50-day above the 200-day. Rank the survivors by how far the close '
            'sits above the 200-day.'
        ),
        criteria=(
            "The universe is today's most-active list fetched in the transcript (GetMostActives, loaded with LoadTool), not a list of symbols from memory.",
            'Both moving averages are computed in code from daily closes; every symbol with fewer than 200 daily bars is excluded or flagged, never averaged over a shorter history.',
            'Every survivor satisfies close > 50-day SMA > 200-day SMA per values printed in the transcript, and no printed symbol that satisfies both is missing from the result.',
            'The ranking is ordered by percent distance of the close above the 200-day SMA, and the order matches the printed values.',
        ),
        tags=('trend', 'deferred_tool'),
    ),
    EvalCase(
        id='quality_pullbacks',
        task=(
            'From AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, TSLA, JPM, V, LLY, UNH, XOM, WMT, COST, HD, PG, JNJ, MA and NFLX, '
            'find pullbacks in long-term uptrends: 14-day RSI below 40 while the close is still above the 200-day simple '
            'moving average. If nothing qualifies, show me the 3 closest.'
        ),
        criteria=(
            'RSI(14) is computed in code from daily closes, and the answer names the averaging method (Wilder smoothing or simple).',
            'A printed table shows RSI(14) and percent distance to the 200-day SMA for all 20 symbols, so near-misses are visible.',
            'If no symbol passes both filters, the answer says so and shows the 3 closest by a stated measure; the thresholds are never loosened without saying so.',
            'Daily bars cover at least 200 trading days for every symbol whose 200-day SMA is reported.',
        ),
        tags=('mean_reversion', 'no_survivors'),
    ),
    EvalCase(
        id='real_gainers',
        task=(
            "Today's top gainers list is full of junk. From the top 50 gainers, keep only stocks listed on NYSE or NASDAQ, "
            'priced at least $5, that traded at least 1 million shares today and at least 2x their 20-day average volume. '
            "Rank by today's percent change and tell me what is driving the top 3."
        ),
        criteria=(
            "The 50 gainers come from today's movers list fetched in the transcript (GetMarketMovers, loaded with LoadTool).",
            'The NYSE / NASDAQ filter is checked against asset data fetched in the transcript (ListAssets), not inferred from the ticker or from memory.',
            "Every volume figure used in a filter comes from SIP daily bars (GetDailyBars), never from GetSnapshots' IEX-only day_volume.",
            "Relative volume is computed in code as today's volume divided by the mean volume of the 20 sessions before today (today excluded), and the printed figures agree with that formula.",
            'Each top-3 driver cites a headline from news fetched in the transcript (GetNews), or the answer says no news was found for that stock.',
        ),
        tags=('volume_trap', 'deferred_tool'),
    ),
    EvalCase(
        id='large_cap_breakouts',
        task='Find me a few liquid large-cap stocks that are breaking out right now.',
        criteria=(
            'The answer makes clear that market cap was not measured directly from the market data and states how "large-cap" was decided: a stated proxy (index membership, average dollar volume) or market caps from a cited source.',
            '"Breakout" is defined concretely (for example, the close at an N-day high on volume above its average), stated in the answer, and applied in code.',
            '"Liquid" has a stated numeric threshold computed from SIP daily-bar volume or dollar volume.',
            'The answer says where the candidate universe came from; any symbols chosen from memory are labelled as such.',
        ),
        tags=('vague', 'missing_data'),
    ),
]


#     ================================
# --> Graders
#     ================================


GRADERS = [
    finished(),
    tools_called('ExecuteCode'),
    max_cost(1.00),
    llm_judge(
        model=JUDGE_MODEL,
        shared_criteria=[
            'Every filter the task specifies is applied in code with the exact threshold the task states; any filter the agent could not apply is named in the answer.',
            'The transcript prints how many symbols survived each filter, so a reader can see what the screen removed.',
            'A trader could act on the result as given: every listed stock shows the metrics it was screened on, and the ranking metric is named.',
            'Every symbol and figure in the final answer appears in tool output or printed code output in the transcript, or in a tool description in <available_tools>.',
        ],
    ),
]


#     ================================
# --> Run
#     ================================


if __name__ == '__main__':
    for round_number in range(1, ROUNDS + 1):
        print(f'\n════ round {round_number}/{ROUNDS} ════')

        records = run_evals(lambda: make_screener_agent(SUBJECT_MODEL), CASES, GRADERS)

        print_report(records)
        print(f'written to {write_report(records)}')
