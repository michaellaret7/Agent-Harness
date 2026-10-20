"""Scratch script: one research kickoff to the tech PM through the real fund org, trading gated off.

    uv run --package fund python t.py

    load .env ──► fund.org (builds the PM) ──► gate: deny every trading tool for this run
              ──► org.run([(pm_id, TASK)]) ──► PM researches, writes research/<date>-tech-scan.md, Remembers theses
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

# Imported after load_dotenv: building the fund reads credentials at import
from agent_harness.gates import GateContext, GateVerdict  # noqa: E402
from fund.org import org  # noqa: E402

TRADING_TOOLS = ('SubmitOrder', 'ReplaceOrder', 'CancelOrder', 'ClosePosition')

now = datetime.now(UTC).astimezone(ZoneInfo('America/New_York'))

TASK = f"""Research session, {now:%a %Y-%m-%d %H:%M} ET. This is research only: do not place, replace, \
or cancel orders or close positions, from tools or from code.

Your analysts are not staffed yet, so do the research yourself across your four industries:
1. Call GetAccount and GetPositions to see what the sleeve holds today.
2. For each industry, find the names with the strongest current setups: recent earnings and guidance,
   estimate revisions, valuation against history and peers, price action, and catalysts in the next
   few weeks. Use WebSearch/WebExtract for news and ExecuteCode with FMP for the numbers.
3. Pick at most 5 candidate ideas. For each, write all five parts of a qualifying idea (thesis, horizon,
   conviction with evidence, invalidation, exit conditions with a stop), plus what would have to be
   true for you to act.
4. Save the full write-up to `research/{now:%Y-%m-%d}-tech-scan.md` in your sandbox, and Remember the
   key theses so later sessions can pick them up.

Do not use SendMessage: there is no one to reply to. End with a summary of the candidates and the
path of the research file."""


def research_only(ctx: GateContext) -> GateVerdict:
    """Deny every trading call for this run."""
    return GateVerdict.deny(f'Research-only session: {ctx.tool_name} is disabled for this run.')


pm = org.get_agent(org.pm_id)
pm.add_gate(research_only, tool=TRADING_TOOLS)

org.run([(org.pm_id, TASK)], max_messages=5)

# org.run returns nothing: read the PM's final answer from its history
final = next(m for m in reversed(pm.messages) if m.get('role') == 'assistant' and m.get('content'))
content = final['content']

print('\n=== PM final answer ===')
print(content if isinstance(content, str) else ''.join(part.get('text', '') for part in content))
print(f'\nTrades recorded: {len(org.trades[org.pm_id])}')
