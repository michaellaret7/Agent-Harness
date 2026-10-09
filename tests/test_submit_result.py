"""Validated-termination contracts. Run: uv run --all-packages pytest tests/test_submit_result.py"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from contract_support import message_text, scripted_agent, tool_call, tool_delta


class Sum(BaseModel):
    """The structured answer the agent must hand in."""

    total: int
    evidence: str


#     ================================
# --> Helper funcs
#     ================================


def submit(args: dict, call_id: str) -> list[dict]:
    """One model turn that calls SubmitResult with `args`."""

    return [tool_delta(tool_call('SubmitResult', args, call_id))]


def tool_reply(request: dict) -> str:
    """The text of the last tool result the model saw in `request`."""
    message = request['messages'][-1]
    assert message['role'] == 'tool', message

    return message_text(message)


#     ================================
# --> Contract tests
#     ================================


def test_rejects_until_proven_then_returns_model() -> None:
    """Plain text is nudged, a bad shape and a failed check go back to the model, a valid result ends the run."""
    responses = [
        [{'content': 'Done! The answer is 5.'}],                          # claims done in plain text
        submit({'total': 5}, 'missing-field'),                            # shape check fails
        submit({'total': 6, 'evidence': 'added 2 and 3'}, 'wrong'),       # verifier rejects
        submit({'total': 5, 'evidence': 'added 2 and 3'}, 'right'),       # accepted
    ]

    with scripted_agent(responses) as (agent, api, sink):
        agent.output_model = Sum
        agent.verifier = lambda r: None if r.total == 5 else f'total {r.total} is wrong'

        result = agent.run('Add 2 and 3.', sink=sink)

    assert result == Sum(total=5, evidence='added 2 and 3'), 'run returns the validated object'
    assert len(api.requests) == 4, 'no extra structured-output call after acceptance'

    assert 'SubmitResult' in message_text(api.requests[1]['messages'][-1]), 'plain text was nudged, not accepted'
    assert 'evidence' in tool_reply(api.requests[2]) and 'Invalid result' in tool_reply(api.requests[2])
    assert 'Rejected: total 6 is wrong' in tool_reply(api.requests[3])

    assert sink.values('loop_end') == [('answer_ready', 4)]
    assert sink.values('turn_end') == [Sum(total=5, evidence='added 2 and 3').model_dump_json()]


def test_plain_text_forever_fails_loudly() -> None:
    """A model that never calls SubmitResult cannot end the run with an unproven answer."""
    responses = [[{'content': 'All done.'}]] * 4

    with scripted_agent(responses) as (agent, api, sink):
        agent.output_model = Sum

        with pytest.raises(RuntimeError, match='without calling SubmitResult'):
            agent.run('Add 2 and 3.', sink=sink)

    assert len(api.requests) == 4


def test_chat_mode_unchanged() -> None:
    """Without output_model, plain text still ends the run and no SubmitResult tool exists."""
    with scripted_agent([[{'content': 'hi'}]]) as (agent, api, sink):
        assert agent.run('Say hi.', sink=sink) == 'hi'

    assert all(t['function']['name'] != 'SubmitResult' for t in api.requests[0]['tools'])


def test_each_run_needs_its_own_submission() -> None:
    """A submission accepted in one run does not end the next run."""
    responses = [
        submit({'total': 5, 'evidence': 'first'}, 'one'),
        submit({'total': 9, 'evidence': 'second'}, 'two'),
    ]

    with scripted_agent(responses) as (agent, api, sink):
        agent.output_model = Sum

        assert agent.run('First.', sink=sink) == Sum(total=5, evidence='first')
        assert agent.run('Second.', sink=sink) == Sum(total=9, evidence='second')

    assert len(api.requests) == 2
