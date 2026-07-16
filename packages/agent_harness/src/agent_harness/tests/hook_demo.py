"""Hook demo. Run: python -m agent_harness.tests.hook_demo

A hook is an observer: it fires on a lifecycle event, receives one
HookContext, and its return value is ignored — side effects only.
Register with agent.add_hook(event, fn, tool=...).

The star here is `kickoff_side_review`. Hooks run synchronously on the
loop's worker thread, so a hook that wants to do real work without
stalling the main agent spawns its own thread and returns immediately.
This one fires after every WebSearch and launches a *second Agent* in the
background to audit the search results — the main agent carries on
summarizing while the side job runs. After run() returns, we join the
side jobs and print their reports.
"""
import threading

from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.hooks import HookContext
from agent_harness.sinks import BaseSink

MODEL = 'moonshotai/kimi-k3'

# Populated by the hook (loop worker / pool threads), read after run() returns.
side_jobs: list[threading.Thread] = []
side_reports: list[str] = []


def review_results(payload: str) -> None:
    """Thread body: run a quiet side agent that grades one search's results."""

    reviewer = Agent(
        provider='openrouter',
        model="x-ai/grok-4.5",
        system='You are a strict research auditor. Be brief.',
    )

    report = reviewer.run(
        task=(
            'Grade these web search results 1-10 on freshness and source quality. '
            'Two sentences max, then the score.\n\n' + payload[:4000]
        ),
        sink=BaseSink(),  # silent — keep the main agent's stdout stream clean
    )

    side_reports.append(report)


def kickoff_side_review(ctx: HookContext) -> None:
    """tool_end (WebSearch only): fire-and-forget a background review job.

    Running the reviewer inline would stall the main loop for a whole extra
    LLM call, so the hook starts a daemon thread and returns immediately.
    """

    job = threading.Thread(target=review_results, args=(ctx.outcome.payload,), daemon=True)

    side_jobs.append(job)
    job.start()

    print(f'\n[hook] side review #{len(side_jobs)} kicked off in the background')


def log_tool_result(ctx: HookContext) -> None:
    """tool_end (all tools): proof the main loop keeps moving while side jobs run."""

    print(f'\n[hook] {ctx.tool_name} -> {ctx.outcome.status} in {ctx.outcome.duration:.2f}s')


load_dotenv()

agent = Agent(
    provider='openrouter',
    model=MODEL,
    system='You are a helpful research assistant.',
)

agent.add_hook('tool_end', log_tool_result)
agent.add_hook('tool_end', kickoff_side_review, tool='WebSearch')

agent.run(task='Search the web for the latest news about AI agents and give me a three-bullet summary.')

# Main run is done — now collect whatever the side jobs produced.
for job in side_jobs:
    job.join(timeout=120)

print('=' * 80)
print(f'side reports ({len(side_reports)}):')

for report in side_reports:
    print(f'- {report}')
