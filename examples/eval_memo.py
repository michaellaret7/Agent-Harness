"""In-depth eval: open-ended research tasks graded on per-case success criteria.

Each case carries its own criteria: concrete, transcript-checkable statements
of what a good answer to THAT task contains. The judge scores every case
criterion plus the shared house rules, and the overall score is the mean.
No case has a single right answer; the criteria define success instead.

Run: uv run --all-packages python examples/eval_memo.py
"""
from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from harness_evals import (
    EvalCase,
    finished, llm_judge, max_cost, max_iterations, no_tool_errors, print_report, run_evals, write_report,
)

load_dotenv()                                   # the app owns bootstrap, not the engine

SUBJECT_MODEL = 'stealth/space-bunny-alpha'
JUDGE_MODEL = 'anthropic/claude-sonnet-4.5'

# 1. the subject: a fresh agent per case
def make_agent() -> Agent:
    return Agent(
        model=SUBJECT_MODEL,
        tools=[execute_code_tool(packages=['pandas'])],   # a Python sandbox: shell out, compute, parse
        max_iters=30,
    )

# 2. the cases: task only reaches the agent; criteria stay with the judge
cases = [
    EvalCase(
        id='aapl_risk_memo',
        task='Write a one-page risk memo on Apple for an investment committee meeting next week.',
        criteria=(
            '''
            names three distinct risks to Apple in 2026, each under its own heading. talks about the new turn of the ceo 
            and the new direction of the company.
            '''
        ),
        tags=('finance', 'writing'),
    ),
    EvalCase(
        id='rate_cut_brief',
        task='Brief me on what the market expects from the next Federal Reserve meeting.',
        criteria=(
            'States the date of the next meeting, and that date appears in a tool result.',
            'Gives the market-implied probability of a cut or hold, with the source named.',
            'Names at least two data points the Fed is watching, each traceable to a search result.',
            'Separates what is known from what is speculation, explicitly.',
        ),
        tags=('macro',),
    ),
    EvalCase(
        id='compare_cloud_capex',
        task='Compare the 2025 capital expenditure of Microsoft, Alphabet, and Amazon.',
        criteria=(
            'Gives a dollar figure for each of the three companies.',
            'Every figure appears in a tool result; none is from memory.',
            'States the period each figure covers (fiscal year, calendar year, or trailing twelve months).',
            'Ranks the three from largest to smallest.',
            'Flags any figure that is a guidance number rather than a reported one.',
        ),
        tags=('finance', 'comparison'),
    ),
]

# 3. the grader list: every grader runs on every case
graders = [
    finished(),
    no_tool_errors(),
    max_iterations(8),
    max_cost(0.25),
    llm_judge(
        model=JUDGE_MODEL,
        shared_criteria=[
            'The first sentence of the final answer addresses the task, not a preamble.',
            'No two tool calls in the transcript share the same tool name with overlapping queries or arguments.',
        ],
    ),
]

# 4. run, print the table + judge reasoning, keep report.json beside the per-case folders
records = run_evals(make_agent, cases, graders)

print_report(records)
write_report(records)
