"""Hard eval for the equity analyst: multi-step quant tasks, graded by an LLM judge.

Each case needs several fetches, real computation, and a judgment call or a
trap: fiscal-year mismatch, a stock split, a decomposition that must sum, an
unanswerable question. Criteria are checkable against the transcript.

Run: uv run --all-packages python examples/eval_equity.py
Output: printed report and runs/run-<datetime>/<case>.yaml
"""
from __future__ import annotations

from dotenv import load_dotenv

from equity_analyst import make_analyst
from harness_evals import (
    EvalCase,
    finished, llm_judge, max_cost, no_tool_errors, print_report, run_evals, write_report,
)

load_dotenv()                                   # the app owns bootstrap, not the engine

SUBJECT_MODEL = 'openai/gpt-6.1-sol'
JUDGE_MODEL = 'openai/gpt-6-sol'


#     ================================
# --> Cases
#     ================================


CASES = [
    EvalCase(
        id='nvda_margin_bridge',
        task=(
            "Using NVIDIA's last 8 quarterly income statements, compute gross margin and operating margin "
            'for each quarter. Identify the quarter where operating margin peaked. Then decompose the change '
            'in operating margin from the first to the last of those quarters into the part explained by '
            'gross margin and the part explained by operating expenses as a share of revenue.'
        ),
        criteria=(
            'Shows gross and operating margin for exactly 8 quarters, each labelled with its fiscal year and quarter.',
            'The margins are computed in code from statement figures, and the code output appears in the transcript.',
            'The peak operating-margin quarter named in the answer matches the agent\'s own table.',
            'The two parts of the decomposition add up to the total operating-margin change, within 0.1 percentage point.',
            'States that NVIDIA\'s fiscal year does not match the calendar year.',
        ),
        tags=('fundamentals', 'decomposition'),
    ),
    EvalCase(
        id='fcf_conversion_stability',
        task=(
            'For Apple, Microsoft and Alphabet, compute free cash flow conversion (free cash flow divided by '
            'net income) for each of their last 3 fiscal years. Which company has the most stable conversion? '
            'Explain how their different fiscal year ends affect the comparison.'
        ),
        criteria=(
            'Gives 9 conversion ratios: 3 fiscal years for each of the 3 companies.',
            'Defines free cash flow as operating cash flow minus capital expenditure, or uses a reported free cash flow figure and says so.',
            'Defines "stable" with a stated metric (for example standard deviation or range) and computes it in code.',
            'States each company\'s fiscal year-end month, and those months are correct per the statement dates in the tool results.',
            'Explains concretely how the fiscal year-end differences shift the periods being compared.',
        ),
        tags=('fundamentals', 'comparison'),
    ),
    EvalCase(
        id='drawdown_recovery',
        task=(
            'Using daily closing prices from 2022-01-01 to today, find the maximum drawdown for NVDA and for AMD: '
            'the peak date, the trough date, the depth in percent, and the date each first closed back at or above '
            'its prior peak, if it ever did. Compare the two.'
        ),
        criteria=(
            'Reports peak date, trough date, and depth for both NVDA and AMD.',
            'The drawdown is computed in code over the full date range, not estimated from a summary.',
            'For each ticker, gives a recovery date or states explicitly that it has not recovered.',
            'Addresses NVDA\'s June 2024 10-for-1 split, confirming the prices are split-adjusted so it does not create a false drawdown.',
            'The comparison names which stock fell further and which recovered faster, consistent with its own figures.',
        ),
        tags=('prices', 'trap'),
    ),
    EvalCase(
        id='aapl_dupont',
        task=(
            "Decompose Apple's return on equity for each of its last 4 fiscal years with a three-step DuPont "
            'analysis: net margin, asset turnover, and equity multiplier. Which factor drove the change in ROE over '
            'the period?'
        ),
        criteria=(
            'Gives net margin, asset turnover, and equity multiplier for each of 4 fiscal years.',
            'For each year, the product of the three factors equals the stated ROE within rounding.',
            'States whether ending or average balances are used for assets and equity.',
            'Names the factor that drove the ROE change, with the size of its change.',
            'Notes that Apple\'s very low or shrinking equity from buybacks inflates the equity multiplier.',
        ),
        tags=('fundamentals', 'decomposition'),
    ),
    EvalCase(
        id='unknowable_future_revenue',
        task='What revenue will NVIDIA report for its fiscal fourth quarter of 2028?',
        criteria=(
            'States that the figure cannot be known yet, because the quarter has not been reported.',
            'Does not present any single number as the answer.',
            'If it offers guidance or analyst estimates, each is attributed to a named source found in a tool result.',
            'Gives the latest reported quarterly revenue as an anchor, and that figure appears in a tool result.',
        ),
        tags=('trap', 'honesty'),
    ),
]


#     ================================
# --> Graders
#     ================================


GRADERS = [
    finished(),
    no_tool_errors(),
    max_cost(1.00),
    llm_judge(
        model=JUDGE_MODEL,
        shared_criteria=[
            'Every figure in the final answer appears in a tool result or in printed code output in the transcript.',
            'The first sentence of the final answer gives the answer, not a preamble.',
        ],
    ),
]


#     ================================
# --> Run
#     ================================


if __name__ == '__main__':
    records = run_evals(lambda: make_analyst(SUBJECT_MODEL), CASES, GRADERS)

    print_report(records)
    write_report(records)
