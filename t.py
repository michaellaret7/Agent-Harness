import json
import threading
from pathlib import Path

from agent_harness.agent import Agent
from agent_harness.hooks import HookContext
from agent_harness.messages import system_msg
from agent_harness.sinks import StdoutSink
from coding.tools.bash import bash
from coding.tools.edit import edit
from coding.tools.glob import glob
from coding.tools.grep import grep
from coding.tools.tree import tree
from coding.tools.write import write
from agent_harness.sinks import LogSink
from dotenv import load_dotenv

load_dotenv()

MEMORY_PATH = Path('memory.md')

MEMORY_CURATOR_SYSTEM = (
    "for this particular task, justy remember some random stuff, we are testing rn"
)


def on_readfile(ctx: HookContext) -> None:
    """Fires after every ReadFile call — does xyz with the path and result."""

    path = ctx.args.get('file_path', '<unknown>') if ctx.args else '<unknown>'
    outcome = ctx.outcome

    status = outcome.status if outcome else 'unknown'
    size = len(outcome.payload) if outcome else 0

    # xyz: react to the read. Swap this for a notification, log write, etc.
    print(f'[hook] ReadFile -> {path} (status={status}, {size} chars)')

def on_start(ctx: HookContext) -> None:
    tsk = ctx.agent.task

    ctx.agent.messages.append(
        system_msg(f"Your name is jeff, which you say all the time and you love to curse.")
    )

    print(f'[hook] Agent started with task: {tsk}')


def code_reviewer(ctx: HookContext) -> None:
    transcript = json.dumps(ctx.agent.messages, indent=2, default=str)

    review_agent = Agent(
        provider='openrouter',
        model='anthropic/claude-opus-4.8',
        system="You are a helpful assistant that reviews other agents work.",
        task=f"Read the messages transcript and summarize how the agent did.\n\n<transcript>\n{transcript}\n</transcript>",
    )

    result = review_agent.run(sink=LogSink('review_agent'))

    print(f'[hook] Review agent summary: {result}')

def memory_adder(ctx: HookContext) -> None:
    """On loop end, ask an LLM (off-thread) whether anything is worth remembering.

    The single completions call runs on a separate thread so it never blocks
    the agent loop. The thread is non-daemon on purpose: in a one-shot script
    the process would otherwise exit the instant `run()` returns and kill the
    call mid-flight.
    """

    # Snapshot the transcript synchronously — the thread must not read
    # agent.messages while the loop may still be mutating it.
    transcript = json.dumps(ctx.agent.messages, indent=2, default=str)

    # Reuse the agent's already-built client/model rather than rebuilding one.
    # Both are guaranteed set by run() before the loop fires loop_end.
    client = ctx.agent.client
    model = ctx.agent.model

    assert client is not None and model is not None

    def _curate() -> None:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': MEMORY_CURATOR_SYSTEM},
                {'role': 'user', 'content': f'<transcript>\n{transcript}\n</transcript>'},
            ],
            stream=False,
        )

        verdict = (response.choices[0].message.content or '').strip()

        if not verdict or verdict == 'NONE':
            print('[hook] memory curator: nothing worth saving')
            return

        with MEMORY_PATH.open('a', encoding='utf-8') as f:
            f.write(f'- {verdict}\n')

        print(f'[hook] memory curator: saved -> {verdict}')

    threading.Thread(target=_curate, daemon=False).start()


def on_iteration_end(ctx: HookContext) -> None:
    print(f'[hook] iteration end: {ctx.detail}')

    print('number: ', ctx.detail.get('number'))
    print('action: ', ctx.detail.get('action'))
    print('content: ', ctx.detail.get('content'))
    print('tools_called: ', ctx.detail.get('tools_called'))

agent = Agent(
    provider='openrouter',
    model='anthropic/claude-opus-4.8',
    system="You are a helpful assistant that can help with coding tasks. You will sat this 'i need to remember my name is jeff'",
    task="Read the file t.py and the .gitignore IN PARALLEL and give me a one-sentence summary of what it does.",
)

agent.add_hook('iteration_end', on_iteration_end)

# Filtered to ReadFile, so the hook runs only when that tool is called.
# agent.add_hook('tool_end', on_readfile, tool=['ReadFile'])
# agent.add_hook('loop_start', on_start)
# agent.add_hook('loop_end', code_reviewer)
# agent.add_hook('loop_end', memory_adder)

agent.run()