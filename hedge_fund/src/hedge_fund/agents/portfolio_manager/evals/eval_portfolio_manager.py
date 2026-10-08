"""Portfolio Manager eval: can the PM read its own Alpaca book and run sound analytics on it?

Each case (one YAML file in `cases/`) asks the PM to pull account and position
data from the Alpaca paper account through ExecuteCode and compute something a
PM reviews every morning:
the account snapshot, concentration, P&L attribution, market risk, factor
exposure, risk-limit breaches, a rebalance trade list, and diversification. The
rubric grades whether every figure is computed in code from data fetched in
the transcript, and whether the PM stayed read-only. The book is live, so no
criterion pins a number; each one checks the answer against the transcript.

Traps: the account runs on margin (cash is negative), so leverage must be read
from gross exposure over equity, not from cash; Alpaca has no sector field, so
sectors need FMP; neither Alpaca nor FMP has factor returns, so the PM must
fetch Fama-French data or build ETF spreads; the book sits near 1.5x, right
at the leverage limit; and the PM's prompt tells it to make decisions, but these
tasks only ask for analysis, so any order call fails the `read_only` grader.

Run: uv run --package hedge-fund python hedge_fund/src/hedge_fund/agents/portfolio_manager/evals/eval_portfolio_manager.py
Output: a printed report and runs/run-<datetime>/<case>.yaml
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from dotenv import load_dotenv

from harness_evals import (
    EvalCase, Grader, RunResult, Score,
    finished, llm_judge, max_cost, no_tool_errors, print_report, run_evals, tools_called, write_report,
)

load_dotenv()                                       # the app owns bootstrap, not the engine

from hedge_fund.agents.portfolio_manager import build_portfolio_manager   # noqa: E402  reads Alpaca / FMP keys at build time

JUDGE_MODEL = 'openai/gpt-6-sol'
MAX_WORKERS = 4                                     # cases at once; FMP and Alpaca rate-limit per minute

# alpaca-py TradingClient methods that change the book; the eval tasks never need them.
ORDER_CALLS = re.compile(r'\b(submit_order|replace_order_by_id|cancel_orders?(_by_id)?|close_(all_)?positions?|exercise_options_position)\s*\(')

# One YAML file per case: id, task, criteria, tags. Every task ends by forbidding orders.
CASES_DIR = Path(__file__).parent / 'cases'


#     ================================
# --> Helper funcs
#     ================================


def _load_case(path: Path) -> EvalCase:
    """Build an EvalCase from one YAML case file; fail fast on a missing or unknown key."""
    with path.open(encoding='utf-8') as f:
        data = yaml.safe_load(f)

    return EvalCase(
        id=data.pop('id'),
        task=data.pop('task'),
        criteria=tuple(data.pop('criteria')),
        tags=tuple(data.pop('tags', ())),
        **data,
    )


def _executed_code(run: RunResult) -> list[str]:
    """Every `code` argument the subject sent to ExecuteCode, in call order."""
    sources: list[str] = []

    for message in run.messages:
        for call in message.get('tool_calls') or []:
            if call['function']['name'] != 'ExecuteCode':
                continue

            args = json.loads(call['function']['arguments'] or '{}')

            sources.append(args.get('code', ''))

    return sources


#     ================================
# --> Graders
#     ================================


def read_only() -> Grader:
    """Pass when no ExecuteCode call invoked an order- or position-changing TradingClient method."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        hits = sorted({m.group(1) for code in _executed_code(run) for m in ORDER_CALLS.finditer(code)})

        return Score(name='read_only', value=0.0 if hits else 1.0, detail=', '.join(hits))

    return Grader('read_only', grade)


#     ================================
# --> Cases
#     ================================


CASES = [_load_case(path) for path in sorted(CASES_DIR.glob('*.yaml'))]


GRADERS = [
    finished(),
    tools_called('ExecuteCode'),
    no_tool_errors(),
    read_only(),
    max_cost(1.00),
    llm_judge(
        model=JUDGE_MODEL,
        shared_criteria=[
            'Every figure in the final answer appears in printed code output in the transcript, or is directly derivable from printed values.',
            'The answer leads with the result, followed by a compact table of the figures behind it.',
        ],
    ),
]


#     ================================
# --> Run
#     ================================


if __name__ == '__main__':
    records = run_evals(build_portfolio_manager, CASES, GRADERS, max_workers=MAX_WORKERS)

    print_report(records)
    print(f'written to {write_report(records)}')
